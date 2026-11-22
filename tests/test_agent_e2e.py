"""Exercise the live event and tool loop with a fake model."""

import asyncio

from raccoon.app.conversations import ConversationService
from raccoon.app.session_runtime import SessionRuntime
from raccoon.bootstrap.agent_session import new_session, open_session
from raccoon.modules.events import EventSourceAdapter
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.view import create_view_tool
from raccoon.runtime.model_types import ToolCall
from tests.scripted_providers import FakeProvider, response


def test_agent_reads_a_file_and_resumes_the_same_history(tmp_path):
    (tmp_path / "README.md").write_text("Project documentation", encoding="utf-8")
    store = SessionStore(tmp_path / ".raccoon")
    tool = create_view_tool(tmp_path)
    session = new_session(store=store, project_root=tmp_path, tools=[tool])

    async def send(session, provider, message_id, text):
        service = ConversationService()
        key = service.register(SessionRuntime(session, provider, tools=[tool]))
        source = EventSourceAdapter(main_session_id=session.session_id)
        event = source.user_message(
            source="local", message_id=message_id, conversation_id=session.session_id, text=text,
        )
        try:
            receipt = service.submit_event(event, key)
            return await asyncio.wait_for(service.wait_for_event(receipt), timeout=5)
        finally:
            await service.close()

    first = FakeProvider([
        response("", calls=[ToolCall("read", "view", {"path": "README.md"})]),
        response("Read the file"),
    ])
    assert asyncio.run(send(session, first, "first", "Read README")).content == "Read the file"
    read_result = next(message for message in first.requests[1].messages if message.tool_call_id == "read")
    assert "Project documentation" in read_result.content

    reopened = open_session(store=store, project_root=tmp_path, session_id=session.session_id, tools=[tool])
    second = FakeProvider([response("Continued")])
    assert asyncio.run(send(reopened, second, "second", "Continue")).content == "Continued"
    roles = [
        message.role for message in store.load(session.session_id).messages
        if message.metadata.get("source_kind") != "agent_status"
    ]
    assert roles == ["user", "assistant", "tool", "assistant", "user", "assistant"]
    assert any(message.content == "Read the file" for message in second.requests[0].messages)
