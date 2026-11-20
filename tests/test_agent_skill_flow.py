"""The model sees skill instructions after requesting the skill."""

import asyncio

from raccoon.app.payloads import MessageInput
from raccoon.app.session_runtime import SessionRuntime
from raccoon.bootstrap.agent_session import new_session, open_session
from raccoon.modules.sessions.store import SessionStore
from raccoon.runtime.model_types import ToolCall
from tests.scripted_providers import FakeProvider, response


def test_loading_a_skill_returns_its_body_and_keeps_it_in_message_history(tmp_path):
    folder = tmp_path / ".agents" / "skills" / "review"
    folder.mkdir(parents=True)
    skill = folder / "SKILL.md"
    skill.write_text("---\nname: review\ndescription: Review changes\n---\nInspect file boundaries.", encoding="utf-8")
    store = SessionStore(tmp_path / ".raccoon")
    session = new_session(store=store, project_root=tmp_path)
    provider = FakeProvider([
        response("", calls=[ToolCall("load", "load_skill", {"name": "review"})]),
        response("Reviewed"),
    ])
    runtime = SessionRuntime(session, provider)
    try:
        asyncio.run(runtime.run_turn(MessageInput("first", "Use the review skill"), lambda: ()))
    finally:
        runtime.close()
    assert all("Inspect file boundaries." not in message.content for message in provider.requests[0].messages)
    result = next(message for message in provider.requests[1].messages if message.tool_call_id == "load")
    assert "Inspect file boundaries." in result.content
    audit = [event.type for event in store.list_events(session.session_id) if event.type.startswith("skill_")]
    assert audit == ["skill_selected", "skill_loaded"]

    skill.unlink()
    restored = open_session(store=store, project_root=tmp_path, session_id=session.session_id)
    follow_up = FakeProvider([response("Continued")])
    runtime = SessionRuntime(restored, follow_up)
    try:
        asyncio.run(runtime.run_turn(MessageInput("next", "Continue reviewing"), lambda: ()))
    finally:
        runtime.close()
    assert any(message.tool_call_id == "load" and "Inspect file boundaries." in message.content
               for message in follow_up.requests[0].messages)
