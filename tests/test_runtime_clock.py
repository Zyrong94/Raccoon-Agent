"""Saved turn dates are stable and the status tool reads live local time."""

from datetime import UTC, datetime

from raccoon.modules.context.context_builder import ContextBuilder
from raccoon.modules.context import temporal
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.session_status import create_session_status_tool


def test_model_context_keeps_each_turn_date_when_the_session_crosses_midnight(tmp_path, monkeypatch):
    current = datetime(2026, 10, 27, 14, 59, tzinfo=UTC)
    monkeypatch.setattr(temporal, "current_time", lambda: current)
    store = SessionStore(tmp_path)
    data = store.create()
    store.append_user_message(data, "First", user_timezone="Asia/Tokyo")
    current = datetime(2026, 10, 27, 15, 1, tzinfo=UTC)
    store.append_user_message(data, "Second", user_timezone="Asia/Tokyo")

    restored = store.load(data.session_id)
    projected = ContextBuilder().build_provider_messages(restored)
    assert "Current date: 2026-10-27" in projected[0].content
    assert "Current date: 2026-10-28" in projected[1].content
    assert [message.parts[0].content for message in restored.messages] == ["First", "Second"]


def test_session_status_reads_the_current_time_in_the_session_timezone(monkeypatch):
    current = datetime(2026, 10, 27, 14, 59, tzinfo=UTC)
    monkeypatch.setattr(temporal, "current_time", lambda: current)
    status = create_session_status_tool("chat", "Asia/Tokyo")
    first = status.executor()
    assert first.data["current_time"].startswith("2026-10-27T23:59:")
    current = datetime(2026, 10, 27, 15, 1, tzinfo=UTC)
    second = status.executor()
    assert second.data["current_time"].startswith("2026-10-28T00:01:")
    assert second.data["session_id"] == "chat"
