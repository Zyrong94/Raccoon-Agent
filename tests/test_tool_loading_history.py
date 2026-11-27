"""A loaded tool is declared to Responses after the matching tool transaction."""

import asyncio
import pytest

from raccoon.bootstrap.agent_session import new_session

from raccoon.runtime.model_types import MessagePart
from raccoon.modules.context.context_builder import ContextBuilder
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.types import make_text_result
from raccoon.runtime.model_types import ChatRequest, ChatResponse, ProviderCapabilities, ToolCall, ToolDefinition
from tests.test_responses_provider import MESSAGE, provider, response


@pytest.mark.parametrize("native", [False, True])
def test_pending_definitions_wait_only_for_ordinary_calls_and_are_saved_once(tmp_path, native):
    store = SessionStore(tmp_path)
    session = new_session(store=store)
    call = ToolCall("pending", "view", {}, async_execution=native)
    store.append_assistant_response(
        session.data, ChatResponse(provider="fake", model="fake", parts=[MessagePart.from_tool_call(call)]),
    )
    definition = ToolDefinition("lookup", "Look up", {"type": "object"})
    session.queue_tool_definitions([definition, definition])
    session.save_pending_tool_definitions()
    if not native:
        assert not session.data.tool_loads
        assert len(session.pending_tool_definitions) == 2
        session.settle_tool_result(tool_call=call, result=make_text_result("view", "done"))
    assert len(session.data.tool_loads) == 1
    assert len(session.data.tool_loads[0]["definitions"]) == 1
    assert session.data.tool_loads[0]["after_message_id"] == session.data.messages[-1].id
    assert not session.pending_tool_definitions
    session.queue_tool_definitions([definition])
    session.save_pending_tool_definitions()
    assert not session.pending_tool_definitions
    assert len(store.load(session.session_id).tool_loads) == 1


def test_failed_definition_save_retains_pending_definitions_for_retry(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    session = new_session(store=store)
    definition = ToolDefinition("lookup", "Look up", {"type": "object"})
    session.queue_tool_definitions([definition])
    original = store.append_tool_definitions_loaded

    def fail(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(store, "append_tool_definitions_loaded", fail)
    with pytest.raises(OSError, match="simulated"):
        session.save_pending_tool_definitions()
    assert session.pending_tool_definitions == [definition]
    assert not session.data.tool_loads
    monkeypatch.setattr(store, "append_tool_definitions_loaded", original)
    session.save_pending_tool_definitions()
    assert not session.pending_tool_definitions
    assert len(store.load(session.session_id).tool_loads) == 1


def test_loaded_tools_follow_their_tool_results_in_the_wire_request(tmp_path):
    store = SessionStore(tmp_path)
    data = store.create()
    calls = [ToolCall("search", "tool_search", {"query": "lookup"}), ToolCall("view", "view", {})]
    anchor = store.append_assistant_response(
        data, ChatResponse(
            provider="fake",
            model="fake",
            parts=[MessagePart.from_tool_call(call) for call in (calls)],
        ),
    )
    definition = ToolDefinition("lookup_extra", "Look up a value", {"type": "object"})
    store.append_tool_definitions_loaded(data, [definition], after_message_id=anchor)
    for call in calls:
        store.append_tool_result(data, tool_call=call, result=make_text_result(call.name, "ok"))

    history = store.load(data.session_id)
    messages = ContextBuilder().build_provider_messages(history)
    assert [message.role for message in messages] == ["assistant", "tool", "tool", "system"]
    model, endpoint = provider(response([MESSAGE]), capabilities=ProviderCapabilities(supports_tool_additions=True))
    asyncio.run(model.acomplete(ChatRequest(messages=messages)))
    payload = endpoint.requests[0]["input"]
    additions = next(index for index, item in enumerate(payload) if item.get("type") == "additional_tools")
    assert [item["call_id"] for item in payload[:additions] if item.get("type") == "function_call_output"] == [
        "search", "view",
    ]
    assert [item["name"] for item in payload[additions]["tools"]] == ["lookup_extra"]
