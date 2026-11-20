"""Offline source adapters: bind sessions from transport context, never from message content.

Session selection follows OpenClaw's main-session / explicit-session approach.
Connectors must authenticate input before calling these methods. No network I/O,
session creation, queue consumption or model calls happen here.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from collections.abc import Sequence
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from raccoon.modules.events.models import Event, EventError
from raccoon.app.payloads import AttachmentRef, MessageInput, TurnSettings


class EventSourceAdapter:
    def __init__(self, *, main_session_id: str) -> None:
        self.main_session_id = _identifier(main_session_id, "main_session_id")

    def user_message(
        self, *, source: str, message_id: str, conversation_id: str, text: str, session_id: str | None = None,
        attachments: Sequence[AttachmentRef] = (), settings: TurnSettings | None = None,
    ) -> Event:
        """Convert a message from an authenticated owner-facing connector."""
        message_id = _identifier(message_id, "message_id")
        conversation_id = _identifier(conversation_id, "conversation_id")
        try:
            message = MessageInput(message_id, text, tuple(attachments))
        except (TypeError, ValueError) as exc:
            raise EventError(str(exc)) from exc
        content = {'text': text}
        if message.attachments:
            content['attachments'] = [asdict(item) for item in message.attachments]
        if settings is not None:
            content['settings'] = asdict(settings)
        return self._build(
            type="user.message", source=source, record_key=(conversation_id, message_id),
            origin={"kind": "user", "message_id": message_id, "conversation_id": conversation_id},
            content=content, session_id=self._session(session_id),
        )

    def email_received(
        self, *, source: str, message_id: str, sender: str, subject: str, body: str, session_id: str | None = None
    ) -> Event:
        message_id = _identifier(message_id, "message_id")
        sender = _identifier(sender, "sender")
        if not isinstance(subject, str) or not isinstance(body, str):
            raise EventError("email subject and body must be strings")
        return self._build(
            type="email.received", source=source, record_key=(message_id,),
            origin={"kind": "external", "message_id": message_id, "sender": sender},
            content={"subject": subject, "body": body}, session_id=self._session(session_id),
        )

    def webhook_received(
        self, *, source: str, delivery_id: str, event_name: str, payload: dict[str, Any], session_id: str | None = None
    ) -> Event:
        delivery_id = _identifier(delivery_id, "delivery_id")
        event_name = _identifier(event_name, "event_name")
        return self._build(
            type=f"webhook.{event_name}", source=source, record_key=(delivery_id,),
            origin={"kind": "external", "delivery_id": delivery_id},
            content=_json_object(payload), session_id=self._session(session_id),
        )

    def timer_triggered(
        self, *, timer_id: str, occurrence_id: str, payload: dict[str, Any], session_id: str | None = None
    ) -> Event:
        timer_id = _identifier(timer_id, "timer_id")
        occurrence_id = _identifier(occurrence_id, "occurrence_id")
        return self._build(
            type="timer.trigger", source="timer", record_key=(timer_id, occurrence_id),
            origin={"kind": "system", "timer_id": timer_id, "occurrence_id": occurrence_id},
            content=_json_object(payload), session_id=self._session(session_id),
        )

    def background_completed(self, *, run_id: str, job_id: str, session_id: str, ok: bool, result: dict[str, Any]) -> Event:
        """A completion must carry its captured owner; never fall back to main."""
        run_id = _identifier(run_id, "run_id")
        job_id = _identifier(job_id, "job_id")
        session_id = _identifier(session_id, "session_id")
        if not isinstance(ok, bool):
            raise EventError("ok must be a boolean")
        return self._build(
            type="task.completed" if ok else "task.failed", source="background", record_key=(session_id, run_id, job_id),
            origin={"kind": "system", "run_id": run_id, "job_id": job_id},
            content={"ok": ok, "result": _json_object(result)}, session_id=session_id,
        )

    def _session(self, session_id: str | None) -> str:
        return self.main_session_id if session_id is None else _identifier(session_id, "session_id")

    @staticmethod
    def _build(
        *, type: str, source: str, record_key: tuple[str, ...], origin: dict[str, Any], content: dict[str, Any], session_id: str
    ) -> Event:
        source = _identifier(source, "source")
        identity = json.dumps(["raccoon.events", type, source, *record_key], ensure_ascii=False, separators=(",", ":"))
        return Event(
            id=str(uuid5(NAMESPACE_URL, identity)), type=type, source=source,
            session_id=session_id, payload={"origin": origin, "content": content},
        )


def _identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventError(f"{field} must be a non-empty string")
    return value.strip()


def _json_object(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EventError("content must be a JSON object")
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError, RecursionError) as exc:
        raise EventError("content must be JSON serializable") from exc
