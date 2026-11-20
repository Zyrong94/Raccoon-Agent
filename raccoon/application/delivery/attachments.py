"""将会话附件引用转换为聊天展示数据，访问地址由接入层提供。"""

from collections.abc import Callable

from raccoon.modules.sessions.attachments import SessionAttachmentStore
from raccoon.app.payloads import AttachmentRef


class ChatAttachments:
    """组合附件存储和地址生成函数，保持聊天操作的返回格式。"""

    def __init__(self, store: SessionAttachmentStore, *, urls: Callable[[str], dict[str, str]]):
        self.store = store
        self.urls = urls

    def descriptor(self, identity: str, ref: AttachmentRef) -> dict:
        return {"id": identity, "kind": ref.kind, "name": ref.filename,
                "content_type": ref.media_type, "size": ref.size_bytes,
                **self.urls(identity)}

    def stage(self, raw: bytes, name: str, media_type: str) -> dict:
        identity, ref = self.store.stage(raw, name, media_type)
        return self.descriptor(identity, ref)

    def for_queued(self, refs: tuple[AttachmentRef, ...]) -> list[dict]:
        return [self.descriptor(identity, ref) for identity, ref in self.store.references_for_queued(refs)]

    def read(self, identity: str):
        raw, ref = self.store.read(identity)
        return raw, self.descriptor(identity, ref)

    def for_parts(self, parts):
        return [self.descriptor(identity, ref) for identity, ref in self.store.references_for_parts(parts)]
