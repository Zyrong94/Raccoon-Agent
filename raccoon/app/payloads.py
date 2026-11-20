"""Neutral queued user-input shapes; no scheduler, agent, or UI dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """A durable local reference; never a remote URL or inline binary."""

    kind: str
    relative_path: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    source: str = 'path'

    def __post_init__(self) -> None:
        path = self.relative_path
        if (not isinstance(path, str) or not path or '\\' in path or ':' in path
                or PurePosixPath(path).is_absolute() or '..' in path.split('/')
                or PurePosixPath(path).as_posix() != path):
            raise ValueError('invalid queued attachment path')
        if self.kind not in {'image', 'file'}:
            raise ValueError('invalid queued attachment kind')
        if (not isinstance(self.filename, str) or not self.filename or len(self.filename) > 256
                or any(c in self.filename for c in '/\\') or any(ord(c) < 32 for c in self.filename)):
            raise ValueError('invalid queued attachment filename')
        if (not isinstance(self.media_type, str) or not self.media_type
                or len(self.media_type) > 128 or self.media_type.startswith('image/') != (self.kind == 'image')):
            raise ValueError('invalid queued attachment media type')
        if (type(self.size_bytes) is not int or self.size_bytes < 0
                or (self.kind == 'image' and self.size_bytes > 20 * 1024 * 1024)):
            raise ValueError('queued attachment size exceeds 20 MiB limit')
        if not isinstance(self.sha256, str) or re.fullmatch('[a-f0-9]{64}', self.sha256) is None:
            raise ValueError('invalid queued attachment digest')
        if not isinstance(self.source, str) or not self.source or len(self.source) > 64:
            raise ValueError('invalid queued attachment source')


@dataclass(frozen=True, slots=True)
class TurnSettings:
    model_ref: str
    permission_mode: str

    def __post_init__(self) -> None:
        if not isinstance(self.model_ref, str) or not self.model_ref or any(c.isspace() for c in self.model_ref):
            raise ValueError('无效的模型标识')
        if self.permission_mode not in ('ask', 'auto', 'bypass'):
            raise ValueError('无效的权限模式')


@dataclass(frozen=True, slots=True)
class MessageInput:
    event_id: str
    text: str
    attachments: tuple[AttachmentRef, ...] = ()
    settings: TurnSettings | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if (not isinstance(self.attachments, tuple) or len(self.attachments) > 16
                or any(not isinstance(item, AttachmentRef) for item in self.attachments)):
            raise ValueError('invalid queued attachments (max 16)')
        if not isinstance(self.text, str) or (not self.text.strip() and not self.attachments):
            raise ValueError("queued user text must be non-empty")


@dataclass(frozen=True, slots=True)
class CompletionInput:
    event_id: str
    run_id: str
    job_id: str
    tool_name: str
    status: str
    summary: str
    label: str | None = None
    task_id: str | None = None
    observed_revision: int | None = None
    task_plan_completion: str | None = None
    kind: str = "tool"
    tool_call_id: str | None = None
    result: dict[str, Any] | None = None
    handle: str | None = None

    def __post_init__(self) -> None:
        if self.tool_call_id is not None:
            if not isinstance(self.tool_call_id, str) or not self.tool_call_id.strip() or not isinstance(self.result, dict):
                raise ValueError("native completion requires call identity and a result snapshot")
        elif self.result is not None:
            raise ValueError("tool result snapshot requires original call identity")
        for value in (self.event_id, self.run_id, self.job_id, self.tool_name, self.kind):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("background identity fields must be non-empty strings")
        if self.status not in ("completed", "failed", "cancelled") or not isinstance(self.summary, str):
            raise ValueError("invalid background result status or summary")
        for value in (self.label, self.task_id, self.task_plan_completion):
            if value is not None and not isinstance(value, str):
                raise ValueError("background optional text fields must be strings")
        if self.observed_revision is not None and (isinstance(self.observed_revision, bool) or not isinstance(self.observed_revision, int) or self.observed_revision < 0):
            raise ValueError("background observed_revision must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class TimerInput:
    """Host-authenticated scheduled instructions, never personal user input."""
    event_id: str
    task_id: str
    text: str

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() for v in (self.event_id, self.task_id, self.text)):
            raise ValueError('timer identity and instructions must be non-empty')


@dataclass(frozen=True, slots=True)
class SubagentInput:
    """Parent instructions are provenance, never human permission or approval."""
    event_id: str
    parent_session_id: str
    text: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (self.event_id, self.parent_session_id, self.text)):
            raise ValueError('subagent message identity and text must be non-empty')


EventInput = MessageInput | CompletionInput | TimerInput | SubagentInput
