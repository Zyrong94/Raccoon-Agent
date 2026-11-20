"""会话列表摘要和文本脱敏选项。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


SessionStatus = Literal["ok", "empty", "corrupt"]


@dataclass(slots=True)
class SessionRecord:
    """resume 列表中展示的一条 session 摘要。"""

    session_id: str
    title: str
    created_at: str | None = None
    updated_at: str | None = None
    workspace: str | None = None
    provider: str | None = None
    model: str | None = None
    message_count: int = 0
    user_turn_count: int = 0
    compaction_count: int = 0
    latest_user_input: str | None = None
    latest_assistant_output: str | None = None
    latest_compaction_id: str | None = None
    status: SessionStatus | str = "ok"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RedactionOptions:
    """控制文本中的路径和密钥是否需要脱敏。"""

    redact_paths: bool = True
    redact_secrets: bool = True
