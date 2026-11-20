"""Read the daily channel session without creating a runtime or writing facts."""

from __future__ import annotations

from pathlib import Path

from raccoon.modules.sessions.store import SessionStore
from raccoon.application.workspaces.catalog import main_session_id
from raccoon.application.delivery.messages import display_messages


def main_session_snapshot(data_root: Path) -> dict:
    empty = {"available": False, "session": None, "messages": []}
    identity = main_session_id(data_root)
    if identity is None:
        return empty
    path = data_root / "sessions" / f"{identity}.jsonl"
    if not path.exists():
        return empty  # The channel host may still be creating its first session.
    view = SessionStore(data_root).load(identity)
    return {"available": True, "session": {"id": identity, "title": "主会话"},
            "messages": display_messages(view)}
