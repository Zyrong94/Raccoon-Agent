import asyncio
import threading

from raccoon.app.conversations import ConversationService
from raccoon.app.session_runtime import SessionRuntime
from raccoon.bootstrap.agent_session import new_session
from raccoon.modules.agent.background import BackgroundJobManager
from raccoon.modules.events import EventSourceAdapter
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.types import Tool, make_text_result
from raccoon.runtime.model_types import ChatMessage, ChatRequest, ToolCall, ToolDefinition
from tests.scripted_providers import AsyncProvider, response
from tests.test_responses_provider import CALL, MESSAGE, provider, response as api_response


def test_responses_async_definition_call_and_stateless_replay():
    call = {**CALL, "async": True}
    p, endpoint = provider(api_response([call]), api_response([MESSAGE]))
    first = asyncio.run(p.acomplete(ChatRequest(messages=[ChatMessage("user", "start")], tools=[
        ToolDefinition("echo", "Echo", {"type": "object"}, async_execution=True),
    ])))
    assert endpoint.requests[0]["tools"][0]["async"] is True
    assert first.tool_calls[0].async_execution is True
    asyncio.run(p.acomplete(ChatRequest(messages=[
        ChatMessage("assistant", parts=first.parts),
        ChatMessage("user", "another question"),
        ChatMessage("tool", "actual output", tool_call_id="call_1"),
    ])))
    assert endpoint.requests[1]["input"][0] == call
    assert endpoint.requests[1]["input"][-1] == {
        "type": "function_call_output", "call_id": "call_1", "output": "actual output",
    }


def test_responses_sdk_async_call_is_replayed_with_wire_field_name():
    from openai.types.responses import ResponseFunctionToolCall

    call = ResponseFunctionToolCall(
        id="fc_async", call_id="call_async", name="echo", arguments='{"text":"hello"}',
        type="function_call", status="completed", **{"async": True},
    )
    p, endpoint = provider(api_response([call]), api_response([MESSAGE]))

    first = asyncio.run(p.acomplete(ChatRequest(messages=[ChatMessage("user", "start")])))
    assert first.tool_calls[0].async_execution is True
    assert first.parts[0].metadata["async_execution"] is True
    assert "async_" not in first.parts[0].metadata.get("api_fields", {})

    asyncio.run(p.acomplete(ChatRequest(messages=[
        ChatMessage("user", "start"),
        ChatMessage("assistant", parts=first.parts),
        ChatMessage("tool", "actual output", tool_call_id="call_async"),
    ])))

    replayed = next(item for item in endpoint.requests[1]["input"] if item.get("type") == "function_call")
    assert replayed["async"] is True
    assert "async_" not in replayed



def test_native_tool_result_reaches_the_model_before_final_reply(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def work():
        entered.set()
        assert release.wait(5)
        return make_text_result("view", "Native evidence")

    class Model(AsyncProvider):
        async def acomplete(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return response("", calls=[ToolCall("native", "view", {}, async_execution=True)])
            if any(message.tool_call_id == "native" for message in request.messages):
                return response("Final reply")
            return response("Candidate without evidence")

    async def scenario():
        manager = BackgroundJobManager()
        service = ConversationService()
        tool = Tool(ToolDefinition("view", "Read", {"type": "object"}), work, supports_background=True)
        store = SessionStore(tmp_path)
        session = new_session(store=store, tools=[tool], background_manager=manager)
        model = Model([])
        key = service.register(SessionRuntime(
            session, model, tools=[tool], background_manager=manager, native_async_tools=True,
        ))
        event = EventSourceAdapter(main_session_id=key.session_id).user_message(
            source="local", message_id="native-turn", conversation_id=key.session_id, text="Read this",
        )
        try:
            receipt = service.submit_event(event, key)
            assert await asyncio.to_thread(entered.wait, 5)
            assert not receipt.finished.is_set()
            release.set()
            result = await asyncio.wait_for(service.wait_for_event(receipt), timeout=5)
            assert result.content == "Final reply"
            result_messages = [message for message in model.requests[-1].messages if message.tool_call_id == "native"]
            assert len(result_messages) == 1 and result_messages[0].content == "Native evidence"
            history = store.load(session.session_id).messages
            assert sum(part.kind == "tool_result" for message in history for part in message.parts) == 1
        finally:
            release.set()
            await service.close()
            manager.shutdown()

    asyncio.run(scenario())
