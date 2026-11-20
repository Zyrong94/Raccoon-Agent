"""Deferred tools become callable after discovery and use the live tool catalog."""

import asyncio

from raccoon.app.payloads import MessageInput
from raccoon.app.session_runtime import SessionRuntime
from raccoon.bootstrap.agent_session import new_session
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.types import Tool, make_text_result
from raccoon.runtime.model_types import ToolCall, ToolDefinition
from tests.scripted_providers import FakeProvider, response


def test_tool_discovery_does_not_authorize_a_call_in_the_same_batch(tmp_path):
    executed = []

    def lookup():
        executed.append("lookup")
        return make_text_result("lookup_extra", "Found it")

    definition = ToolDefinition("lookup_extra", "Find an extra value", {"type": "object"})
    tool = Tool(definition, lookup, exposure="deferred")
    store = SessionStore(tmp_path)
    session = new_session(store=store, tools=[tool])
    provider = FakeProvider([
        response("", calls=[ToolCall("discover", "tool_search", {"query": "lookup_extra"}),
                            ToolCall("guessed", tool.name, {})]),
        response("", calls=[ToolCall("loaded", tool.name, {})]),
        response("Done"),
    ])
    runtime = SessionRuntime(session, provider, tools=[tool])
    try:
        asyncio.run(runtime.run_turn(MessageInput("start", "Look up the value"), lambda: ()))
    finally:
        runtime.close()

    assert executed == ["lookup"]
    assert tool.name not in {item.name for item in provider.requests[0].tools}
    history = store.load(session.session_id)
    assert [entry["definitions"][0]["name"] for entry in history.tool_loads] == [tool.name]
    results = {part.metadata["tool_call_id"]: part for message in history.messages for part in message.parts
               if part.kind == "tool_result"}
    assert results["guessed"].metadata["data"]["tool_activation_required"]
    assert results["loaded"].metadata["ok"]


def test_restored_tool_definition_does_not_authorize_a_removed_tool(tmp_path):
    definition = ToolDefinition("lookup_extra", "Look up a value", {"type": "object"})
    store = SessionStore(tmp_path)
    session = new_session(store=store, tools=[])
    store.append_tool_definitions_loaded(session.data, [definition], after_message_id=None)
    provider = FakeProvider([response("", calls=[ToolCall("removed", definition.name, {})]), response("Unavailable")])
    runtime = SessionRuntime(session, provider, tools=[])
    try:
        asyncio.run(runtime.run_turn(MessageInput("start", "Use the old tool"), lambda: ()))
    finally:
        runtime.close()
    data = store.load(session.session_id)
    result = next(part for message in data.messages for part in message.parts if part.kind == "tool_result")
    assert result.metadata["tool_call_id"] == "removed"
    assert result.metadata["data"]["tool_not_routed"]
    assert data.tool_loads



def test_tool_argument_validation_does_not_fetch_an_external_schema():
    from raccoon.modules.tools.execution import validate_arguments

    definition = ToolDefinition("external", "External schema", {"$ref": "https://invalid.example/schema.json"})
    tool = Tool(definition, lambda: make_text_result("external", "unused"))
    invalid = validate_arguments(tool, {})
    assert invalid is not None and not invalid.ok
