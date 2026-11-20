"""A loaded tool is declared to Responses after the matching tool transaction."""

import asyncio

from raccoon.modules.context.context_builder import ContextBuilder
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.types import make_text_result
from raccoon.runtime.model_types import ChatRequest, ChatResponse, ProviderCapabilities, ToolCall, ToolDefinition
from tests.test_responses_provider import MESSAGE, provider, response


def test_loaded_tools_follow_their_tool_results_in_the_wire_request(tmp_path):
    store = SessionStore(tmp_path)
    data = store.create()
    calls = [ToolCall("search", "tool_search", {"query": "lookup"}), ToolCall("view", "view", {})]
    anchor = store.append_assistant_response(
        data, ChatResponse(provider="fake", model="fake", content="", tool_calls=calls),
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
