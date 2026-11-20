"""Assembly and ownership acquisition for the shared local personal host."""
import asyncio
import json
from pathlib import Path
import time
import portalocker
from raccoon.application.conversations.main_host import PersonalChatHost, MainHostUnavailable, MainHostConflict
from raccoon.interfaces.local.main_host import LocalMainEndpoint, call_main_host
from raccoon.interfaces.qq.delivery import deliver_host_response

def create_personal_chat_host(data_root, session_id, queue, bundle, **kwargs):
    data_root = Path(data_root)
    from raccoon.application.conversations.scheduled_main import ScheduledTurn
    coordinator = ScheduledTurn(data_root, session_id, queue, bundle)
    return PersonalChatHost(data_root, session_id, queue, bundle, endpoint=LocalMainEndpoint(data_root),
        scheduled_executor=coordinator,
        deliver_qq_response=lambda sid, response, route, runtime: deliver_host_response(data_root, sid, response, route, runtime), **kwargs)

class MainSessionBridge:
    """Attach, or acquire the same lock and lazily become the owner.

    A failed mutation is never retried: the owner might already have accepted it.
    The next explicit refresh can reconnect or acquire ownership after a crash.
    """
    def __init__(self, project, data_root, *, runtime_factory):
        self.project, self.data_root = Path(project), Path(data_root)
        self.runtime_factory = runtime_factory
        self.owner = None
        self._start_lock = asyncio.Lock()

    async def ensure(self):
        async with self._start_lock:
            if self.owner is not None and self.owner.closed:
                self.owner = None
            if self.owner is not None:
                return
            root = self.data_root / 'channels'
            root.mkdir(parents=True, exist_ok=True)
            try:
                reservation = json.loads((root / 'main_handoff.json').read_text(encoding='utf-8'))
                if reservation.get('expires', 0) > time.time():
                    return
            except (OSError, ValueError, TypeError):
                pass
            lock = portalocker.Lock(str(root / 'main.lock'), timeout=0)
            try:
                lock.acquire()
            except portalocker.exceptions.LockException:
                return  # The authenticated endpoint belongs to the lock owner.
            queue = bundle = None
            try:
                from raccoon.interfaces.qq.session import prepare_main_session
                session_id, queue = prepare_main_session(self.data_root)
                assembly = asyncio.create_task(asyncio.to_thread(self.runtime_factory, project_root=self.project, data_root=self.data_root,
                    session_id=session_id, resume_session=(self.data_root / 'sessions' / f'{session_id}.jsonl').exists()))
                try:
                    bundle = await asyncio.shield(assembly)
                except asyncio.CancelledError:
                    bundle = await assembly  # Keep the ownership lock until the worker actually exits.
                    raise
                from raccoon.bootstrap.attachments import create_chat_attachments
                host = create_personal_chat_host(self.data_root, session_id, queue, bundle, lock=lock,
                    chat_attachments=create_chat_attachments(self.data_root, 'personal-main', session_id, personal=True))
                await host.start()
                self.owner = host
            except BaseException:
                if bundle is not None:
                    await asyncio.to_thread(bundle.close)
                if queue is not None:
                    await queue.close()
                lock.release()
                raise

    async def call(self, operation, body=None):
        if operation not in {'subagents_list', 'subagents_detail'}:
            await self.ensure()
        elif self.owner is not None and self.owner.closed:
            raise MainHostUnavailable('主会话宿主已关闭，请重新同步。')
        if self.owner is not None:
            return await self.owner.dispatch(operation, body)
        return await call_main_host(self.data_root, operation, body)

    async def close(self):
        if self.owner is not None:
            owner, self.owner = self.owner, None
            await owner.close()
