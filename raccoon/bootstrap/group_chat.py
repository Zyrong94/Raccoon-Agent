"""Group-owned QQ conversations; private identity is linked by a one-use code."""

import asyncio
from dataclasses import replace
from pathlib import Path
import time

from raccoon.bootstrap.runtime import create_runtime
from raccoon.application.conversations.permission_commands import PermissionCommandHandler
from raccoon.interfaces.qq.channel import QQChannel
from raccoon.interfaces.qq.state import QQState
from raccoon.config.settings import load_config
from raccoon.modules.events import EventSourceAdapter

from raccoon.application.conversations.group_chat import GroupConversation
from raccoon.interfaces.qq.groups import QQGroups as QQGroupsAdapter
from raccoon.interfaces.qq.session import prepare_main_session


class QQGroups(QQGroupsAdapter):
    """Compose QQ groups with the shared runtime and context factories."""

    def __init__(self, state, *, project: Path, data_root: Path, runtime_factory=create_runtime,
                 members: str = 'owner', mode: str = 'ask', clock=time.time, media_client=None):
        super().__init__(state, project=project, data_root=data_root,
                         runtime_factory=runtime_factory, context_factory=create_group_context,
                         members=members, mode=mode, clock=clock, media_client=media_client)


async def create_group_context(row, root, *, project, runtime_factory, mode, members, media_client):
    session_id, queue = prepare_main_session(root)
    state = QQState(root/'channels'/'qq.sqlite3',target_kind='group',target_id=row['group_openid'])
    bundle = runtime = None
    try:
        state.set('owner',row['member_owner'])
        config = load_config(project_root=project)
        config = replace(config,project_config={**(config.project_config or {}),'memory':{'enabled':False}})
        bundle = await asyncio.to_thread(runtime_factory,project_root=project,data_root=root,
            session_id=session_id,resume_session=(root/'sessions'/f'{session_id}.jsonl').exists(),app_config=config)
        bundle.session.set_permission_mode(mode)
        runtime = queue
        channel = QQChannel(state,runtime,EventSourceAdapter(main_session_id=session_id),pairing_code='',pairing_expires=0,
            permission_commands=PermissionCommandHandler(bundle),allow_group_members=members=='all')
        channel.restart_mode = mode.value
        if media_client is not None:
            from raccoon.interfaces.qq.attachments import QQImageInput
            channel.image_input = QQImageInput(media_client, store_root=root, session_id=session_id)
        context = GroupConversation(row['group_openid'],session_id,root,state,bundle,runtime,channel)
        runtime.register(bundle, on_response=lambda sid,result:channel.on_response(sid,result))
        def input_event(request, answer):
            if request is not None and answer is None:
                runtime.loop.call_soon_threadsafe(channel.on_response, session_id, bundle._waiting_for_input_response(request))
        bundle.input_event_handler = input_event
        runtime.start()
        return context
    except BaseException:
        if runtime is not None:
            await runtime.close()
        if bundle is not None:
            await asyncio.to_thread(bundle.close)
        state.close();await queue.close()
        raise
