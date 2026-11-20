"""装配会话附件存储、聊天展示与 Web 访问地址。"""

from functools import partial
from pathlib import Path

from raccoon.application.delivery.attachments import ChatAttachments
from raccoon.modules.sessions.attachments import SessionAttachmentStore
from raccoon.interfaces.web.attachments import attachment_urls


def create_chat_attachments(root: Path, project_id: str, session_id: str, *, personal=False) -> ChatAttachments:
    return ChatAttachments(SessionAttachmentStore(root, session_id),
        urls=partial(attachment_urls, project_id, session_id, personal=personal))
