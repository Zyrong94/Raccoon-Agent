"""Open the session through the current bootstrap entry."""

from raccoon.runtime.model_types import MessagePart
from raccoon.bootstrap.agent_session import open_session
from raccoon.modules.sessions.store import SessionStore
from raccoon.runtime.model_types import ChatResponse, ToolCall


def test_open_session_reads_current_project_rules_and_closes_an_unfinished_tool_call(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Current project rules", encoding="utf-8")
    store = SessionStore(tmp_path / ".raccoon")
    data = store.create()
    store.append_user_message(data, "Read a file", user_timezone="UTC")
    store.append_assistant_response(data, ChatResponse(
        provider="fake",
        model="fake",
        parts=[MessagePart.from_tool_call(call) for call in ([ToolCall("read", "view", {"path": "README.md"})])],
    ))

    session = open_session(store=store, project_root=tmp_path, session_id=data.session_id)
    assert "Current project rules" in session.agents_md
    assert session.data.messages[0].parts[0].content == "Read a file"

    # Opening a second time leaves the original call with just one result.
    open_session(store=store, project_root=tmp_path, session_id=data.session_id)
    results = [part for message in store.load(data.session_id).messages for part in message.parts
               if part.kind == "tool_result"]
    assert len(results) == 1
    assert results[0].metadata["tool_call_id"] == "read"
    assert results[0].metadata["ok"] is False
