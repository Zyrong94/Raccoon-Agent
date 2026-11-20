"""Composition root for the optional official QQ channel."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import os
from pathlib import Path
import secrets
import time

from dotenv import dotenv_values
import httpx
import portalocker

from raccoon.bootstrap.runtime import create_runtime
from raccoon.application.conversations.permission_commands import PermissionCommandHandler
from raccoon.interfaces.qq.api import QQApi
from raccoon.interfaces.qq.channel import QQChannel
from raccoon.interfaces.qq.gateway import QQGateway
from raccoon.interfaces.qq.state import QQState
from raccoon.modules.events import EventSourceAdapter


from raccoon.interfaces.qq.session import prepare_main_session, QQSessionConflict

async def serve(project: Path, data_root: Path, env_file: Path, *, new_session: bool = False) -> None:
    channel_root = data_root / 'channels'
    channel_root.mkdir(parents=True, exist_ok=True)
    lock = portalocker.Lock(str(channel_root / 'main.lock'), timeout=0)
    try:
        lock.acquire()
    except portalocker.exceptions.LockException:
        if new_session:
            raise QQSessionConflict('主会话仍在其他本地进程运行，请先停止该宿主再明确新建。') from None
        acquired = await serve_attached(project, data_root, env_file)
        try:
            await serve_owned(project, data_root, env_file, host_lock=acquired)
        finally:
            acquired.release()
        return
    try:
        await serve_owned(project, data_root, env_file, new_session=new_session, host_lock=lock)
    finally:
        lock.release()


async def serve_attached(project: Path, data_root: Path, env_file: Path) -> portalocker.Lock:
    """The QQ gateway stays a connector while Web owns the shared main runner."""
    from raccoon.bootstrap.main_host import MainSessionBridge
    from raccoon.interfaces.local.main_remote import RemoteMainRuntime
    config = {**dotenv_values(env_file), **os.environ}
    app_id, secret = config.get('RACCOON_QQ_APP_ID'), config.get('RACCOON_QQ_APP_SECRET')
    if not app_id or not secret:
        raise ValueError('请配置 QQ 凭据')
    bridge = MainSessionBridge(project, data_root, runtime_factory=create_runtime)
    runtime = RemoteMainRuntime(bridge)
    state = QQState(data_root / 'channels' / 'qq.sqlite3')
    groups = scheduling = None
    tasks = []
    try:
        await runtime.refresh()
        async with (httpx.AsyncClient(timeout=20, follow_redirects=False) as client,
                    httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as media_client):
            api = QQApi(app_id, secret, client)
            await api.gateway_url()
            pairing_code = secrets.token_hex(8)
            channel = QQChannel(state, runtime, EventSourceAdapter(main_session_id=runtime.state['session_id']),
                pairing_code=pairing_code, pairing_expires=time.time() + 900)
            channel.handoff_wait = True
            from raccoon.interfaces.qq.attachments import QQImageInput
            channel.image_input = QQImageInput(media_client, store_root=data_root, session_id=runtime.state['session_id'])
            from raccoon.bootstrap.group_chat import QQGroups
            groups = QQGroups(state, project=project, data_root=data_root, runtime_factory=create_runtime,
                members=config.get('RACCOON_QQ_GROUP_MEMBERS', 'owner'),
                mode=config.get('RACCOON_QQ_GROUP_MODE', 'ask'), media_client=media_client)
            channel.groups = groups
            from raccoon.bootstrap.scheduling import SchedulingService
            scheduling = SchedulingService(data_root, main_bridge=bridge)
            channel.scheduling = scheduling
            await scheduling.start()
            await groups.restore()
            await bridge.call('handoff', runtime.identity())
            if not state.get('owner'):
                (data_root / 'channels' / 'qq_pairing.txt').write_text(
                    f'15 分钟内在 QQ 私聊机器人发送：\n/pair {pairing_code}\n', encoding='utf-8')
            gateway = QQGateway(api, state)
            state.set('online', False)
            tasks = [asyncio.create_task(gateway.run()), asyncio.create_task(channel.intake_loop()),
                     asyncio.create_task(channel.sender_loop(api, gateway.online)),
                     asyncio.create_task(groups.sender_loop(api, gateway.online))]
            async def acquire_owner():
                owner_lock = portalocker.Lock(str(data_root / 'channels' / 'main.lock'), timeout=0)
                while True:
                    await asyncio.sleep(.1)
                    try:
                        owner_lock.acquire()
                        return owner_lock
                    except portalocker.exceptions.LockException:
                        pass
            takeover = asyncio.create_task(acquire_owner())
            tasks.append(takeover)
            finished, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if takeover in finished:
                return takeover.result()
            for task in finished:
                task.result()
            raise RuntimeError('QQ connector stopped before ownership transfer')
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if groups is not None:
            await groups.close()
        if scheduling is not None:
            await scheduling.close()
        await bridge.close()
        state.set('online', False)
        state.close()


async def serve_owned(project: Path, data_root: Path, env_file: Path, *, new_session: bool = False, host_lock=None) -> None:
    config = {**dotenv_values(env_file), **os.environ}
    app_id, secret = config.get("RACCOON_QQ_APP_ID"), config.get("RACCOON_QQ_APP_SECRET")
    if not app_id or not secret:
        raise ValueError("请在本地 .env.qq 配置 RACCOON_QQ_APP_ID / RACCOON_QQ_APP_SECRET")
    channel_root = data_root / "channels"
    channel_root.mkdir(parents=True, exist_ok=True)
    # One host owns the daily main session, including future IM connectors.
    with host_lock:
        session_id, queue = prepare_main_session(data_root, new_session=new_session)
        state = QQState(channel_root / "qq.sqlite3")
        bundle = runtime = groups = scheduling = host = None
        tasks = []
        try:
            async with (httpx.AsyncClient(timeout=20, follow_redirects=False) as client,
                        httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as media_client):
                api = QQApi(app_id, secret, client)
                await api.gateway_url()  # Fail before expensive agent assembly if credentials are invalid.
                bundle = await asyncio.to_thread(create_runtime, project_root=project, data_root=data_root,
                    session_id=session_id, resume_session=(data_root / "sessions" / f"{session_id}.jsonl").exists())
                pairing_code = secrets.token_hex(8)
                from raccoon.bootstrap.main_host import create_personal_chat_host
                from raccoon.bootstrap.attachments import create_chat_attachments
                host = create_personal_chat_host(data_root, session_id, queue, bundle, owns_runtime=False,
                    chat_attachments=create_chat_attachments(data_root, 'personal-main', session_id, personal=True))
                runtime = host.application
                channel = QQChannel(state, runtime, EventSourceAdapter(main_session_id=session_id),
                    pairing_code=pairing_code, pairing_expires=time.time() + 900,
                    permission_commands=PermissionCommandHandler(bundle))
                host.on_qq_response = lambda sid, response, route: channel.on_response(sid, response, route=route)
                channel.response_route = lambda: host.route
                from raccoon.interfaces.qq.attachments import QQImageInput
                channel.image_input = QQImageInput(media_client, store_root=data_root, session_id=session_id)
                from raccoon.bootstrap.scheduling import SchedulingService
                from raccoon.modules.scheduling.tools import install_scheduling
                from raccoon.bootstrap.main_host import MainSessionBridge
                main_bridge = MainSessionBridge(project, data_root, runtime_factory=create_runtime)
                main_bridge.owner = host
                scheduling = SchedulingService(data_root, main_bridge=main_bridge)
                channel.scheduling = scheduling
                install_scheduling(bundle, scheduling.store, channel='qq', target=lambda: state.get('owner', ''),
                                   timezone=lambda: bundle.session.user_timezone)
                from raccoon.bootstrap.group_chat import QQGroups
                groups = QQGroups(state,project=project,data_root=data_root,runtime_factory=create_runtime,
                    members=config.get('RACCOON_QQ_GROUP_MEMBERS','owner'),
                    mode=config.get('RACCOON_QQ_GROUP_MODE','ask'), media_client=media_client)
                channel.groups = groups
                gateway = QQGateway(api, state)
                state.set("online", False)
                if not state.get("owner"):
                    pairing_path = channel_root / "qq_pairing.txt"
                    pairing_path.write_text(f"15 分钟内在 QQ 私聊机器人发送：\n/pair {pairing_code}\n", encoding="utf-8")
                    logging.info("QQ pairing instructions saved locally: %s", pairing_path)
                await host.start()
                await scheduling.start()
                await groups.restore()
                tasks = [asyncio.create_task(gateway.run()), asyncio.create_task(channel.intake_loop()),
                         asyncio.create_task(channel.sender_loop(api, gateway.online)),
                         asyncio.create_task(groups.sender_loop(api,gateway.online))]
                await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await task
            if scheduling is not None:
                await scheduling.close()
            if runtime is not None:
                await runtime.close()
            if host is not None:
                await host.close()
            if groups is not None:
                await groups.close()
            if bundle is not None:
                await asyncio.to_thread(bundle.close)
            state.set("online", False)
            state.close()
            await queue.close()


