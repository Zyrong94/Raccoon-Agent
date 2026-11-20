"""Group execution lifecycle; QQ identities and transport stay in their adapter."""
import asyncio
from dataclasses import dataclass, field
import logging
from pathlib import Path
from raccoon.app.conversations import ConversationService
log = logging.getLogger(__name__)

@dataclass
class GroupConversation:
    group_id: str
    session_id: str
    root: Path
    state: object
    bundle: object
    runtime: ConversationService
    channel: object
    delivery_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False

    async def close(self):
        async with self.delivery_lock:
            await self._close_locked()

    async def _close_locked(self):
        if self.closed:
            return
        self.closed = True
        try:
            await self.runtime.close()
        finally:
            try:
                await asyncio.to_thread(self.bundle.close)
            finally:
                self.state.close()

class GroupChatManager:
    def __init__(self, data_root, *, create_context):
        self.data_root = data_root
        self.create_context = create_context
        self.contexts = {}
        self.failed = set()

    def _root(self,key):
        root = self.data_root/'channels'/'groups'/key
        if root.resolve()!=self.data_root.resolve()/'channels'/'groups'/key:
            raise ValueError('Group directory may not redirect outside the data root')
        root.mkdir(parents=True,exist_ok=True)
        return root

    async def get(self,row) -> GroupConversation:
        key = row['group_key']
        if key in self.contexts:
            return self.contexts[key]
        if key in self.failed:
            raise RuntimeError('Group recovery required')
        root = self._root(key)
        context = await self.create_context(row, root)
        self.contexts[key] = context
        return context

    async def close(self):
        contexts = list(self.contexts.values())
        self.contexts.clear()
        results = await asyncio.gather(*(context.close() for context in contexts),return_exceptions=True)
        for result in results:
            if isinstance(result,BaseException):
                log.warning('QQ group cleanup failed (%s)',type(result).__name__)
