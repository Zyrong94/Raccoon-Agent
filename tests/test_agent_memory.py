"""Memory augments a model request and learns only from a completed user turn."""

import asyncio

from raccoon.app.payloads import MessageInput
from raccoon.app.session_runtime import SessionRuntime
from raccoon.bootstrap.agent_session import new_session
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.types import Tool, make_text_result
from raccoon.runtime.model_types import ToolCall, ToolDefinition
from tests.scripted_providers import FakeProvider, response


def test_memory_is_recalled_once_across_the_tool_round_trip(tmp_path):
    reads, submissions = [], []

    class Memory:
        def search(self, query):
            reads.append(query)
            return "Recalled fact"

        def submit_turn(self, question, answer, **origin):
            submissions.append((question, answer, origin["message_id"]))

    tool = Tool(ToolDefinition("echo", "Echo", {"type": "object"}), lambda: make_text_result("echo", "tool output"))
    session = new_session(store=SessionStore(tmp_path), tools=[tool])
    provider = FakeProvider([response("", calls=[ToolCall("echo", "echo", {})]), response("Done")])
    runtime = SessionRuntime(session, provider, tools=[tool], memory_service=Memory())
    try:
        asyncio.run(runtime.run_turn(MessageInput("input", "User question"), lambda: ()))
    finally:
        runtime.close()

    assert reads == ["User question"]
    assert all(sum("Recalled fact" in message.content for message in request.messages) == 1
               for request in provider.requests)
    history = session.store.load(session.session_id).messages
    assert history[0].parts[0].content == "User question"
    assert submissions == [("User question", "Done", history[0].id)]
