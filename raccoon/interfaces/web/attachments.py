"""Web 附件地址和下载头；存储与聊天操作由独立能力提供。"""

from functools import partial
from pathlib import Path
from urllib.parse import urlencode, quote

from raccoon.application.delivery.attachments import ChatAttachments
from raccoon.modules.sessions.attachments import (
    MAX_ATTACHMENTS, MAX_FILE_BYTES, MAX_MESSAGE_BYTES, SessionAttachmentStore, validate_upload,
)


def attachment_urls(project_id: str, session_id: str, identity: str, *, personal=False) -> dict[str, str]:
    query = urlencode({"session_id": session_id} if personal else
                      {"project_id": project_id, "session_id": session_id})
    base = f"/api/main-session/attachments/{identity}" if personal else f"/api/attachments/{identity}"
    return {"content_url": f"{base}/content?{query}", "download_url": f"{base}/download?{query}"}


class WebAttachments(ChatAttachments):
    """保留 Web 调用构造方式，连接实际存储和聊天展示。"""

    def __init__(self, root: Path, project_id: str, session_id: str, *, personal=False):
        super().__init__(SessionAttachmentStore(root, session_id),
            urls=partial(attachment_urls, project_id, session_id, personal=personal))

    @property
    def root(self):
        return self.store.root


def download_disposition(name: str) -> str:
    return "attachment; filename*=UTF-8''" + quote(name, safe="")
