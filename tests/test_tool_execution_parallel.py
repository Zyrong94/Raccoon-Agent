"""Independent read tools can overlap and results keep model call order."""

import asyncio
import threading

from raccoon.app.payloads import MessageInput
from raccoon.app.session_runtime import SessionRuntime
from raccoon.bootstrap.agent_session import new_session
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.types import Tool, make_text_result
from raccoon.runtime.model_types import ToolCall, ToolDefinition
from tests.scripted_providers import FakeProvider, response


def test_parallel_read_tools_overlap_and_preserve_call_order(tmp_path):
    both_entered = threading.Barrier(2)

    def execute(name):
        both_entered.wait(timeout=5)
        return make_text_result(name, name)

    names = ("grep", "glob")
    tools = [Tool(ToolDefinition(name, name, {"type": "object"}), lambda name=name: execute(name))
             for name in names]
    store = SessionStore(tmp_path)
    session = new_session(store=store, tools=tools)
    provider = FakeProvider([response("", calls=[ToolCall(name, name, {}) for name in names]), response("Done")])
    runtime = SessionRuntime(session, provider, tools=tools)
    try:
        result = asyncio.run(runtime.run_turn(MessageInput("start", "Search"), lambda: ()))
    finally:
        runtime.close()
    assert result.content == "Done"
    facts = [part for message in store.load(session.session_id).messages for part in message.parts
             if part.kind == "tool_result"]
    assert [part.metadata["tool_call_id"] for part in facts] == list(names)
    assert all(part.metadata["ok"] for part in facts)
