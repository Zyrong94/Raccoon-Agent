"""Event records shared by authenticated sources and application dispatch."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from raccoon.modules.sessions.models import utc_now_iso


class EventError(ValueError):
    """An event or queue state transition is invalid."""


@dataclass(slots=True)
class Event:
    id: str
    type: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    created_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def create(cls, *, type: str, source: str, payload: dict[str, Any] | None = None) -> Event:
        return cls(id=str(uuid4()), type=type, source=source, payload=payload or {})

    def validate(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (self.id, self.type, self.source)):
            raise EventError("event id, type and source must be non-empty")
        if not isinstance(self.payload, dict):
            raise EventError("event payload must be a JSON object")
        try:
            json.dumps(self.payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise EventError("event payload must be JSON serializable") from exc

    @property
    def task_id(self) -> str | None:
        return self.payload.get('origin', {}).get('timer_id') if self.type == 'timer.trigger' else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "payload": self.payload,
            "session_id": self.session_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Event:
        if not isinstance(value, dict):
            raise EventError("事件必须是对象")
        try:
            event = cls(**value)
        except TypeError as error:
            raise EventError("事件字段无效") from error
        event.validate()
        return event
