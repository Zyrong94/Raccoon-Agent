from __future__ import annotations

import asyncio
import json
from raccoon.runtime.model_types import MessagePart
from copy import deepcopy
from unittest.mock import patch

import pytest
from openai.types.responses import Response

from raccoon.config import AppConfig
from raccoon.modules.context.context_builder import ContextBuilder
from raccoon.modules.sessions.store import SessionStore
from raccoon.runtime.model_errors import ProviderError, ProviderErrorKind
from raccoon.integrations.providers.factory import create_provider_for_model
from raccoon.integrations.providers.openai_responses import OpenAIResponsesProvider
from raccoon.runtime.model_types import (
    ChatMessage, ChatRequest, ContentPart, TokenUsage,
    ToolCall, ToolChoiceFunction, ToolDefinition,
)
from raccoon.modules.tools.types import Tool, ToolResult


REASONING = {"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "opaque-state"}
CALL = {
    "id": "fc_1", "type": "function_call", "call_id": "call_1",
    "name": "echo", "arguments": '{"text":"hello"}', "status": "completed",
}
MESSAGE = {
    "id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
    "phase": "commentary",
    "content": [{"type": "output_text", "text": "Checking.", "annotations": []}],
}


def response(output=None, **overrides):
    return {
        "id": "resp_1", "model": "gpt-6-luna", "status": "completed",
        "output": deepcopy(output if output is not None else [REASONING, MESSAGE, CALL]),
        "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
        **overrides,
    }


class FakeResponses:
    def __init__(self, results):
        self.results = list(results)
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return Response.model_construct(**result)


class FakeClient:
    def __init__(self, endpoint):
        self.responses = endpoint

    async def __aenter__(self):
        return self

    async def __aexit__(self, error_type, error, traceback):
        pass


class OfflineResponsesProvider(OpenAIResponsesProvider):
    """只在测试期间替换 SDK；实际请求与解析仍执行产品代码。"""

    async def acomplete(self, request):
        with patch("openai.AsyncOpenAI", return_value=FakeClient(self.endpoint)):
            return await super().acomplete(request)


def provider(*results, **kwargs):
    endpoint = FakeResponses(results)
    model = kwargs.pop("model", "gpt-6-luna")
    model_provider = OfflineResponsesProvider(
        name="official", model=model, api_key="test-key", **kwargs,
    )
    model_provider.endpoint = endpoint
    return model_provider, endpoint


def test_maps_request_options_tools_and_forced_choice():
    model_provider, endpoint = provider(response([MESSAGE]))
    request = ChatRequest(
        messages=[ChatMessage(
            role="system",
            text="Be helpful.",
        ), ChatMessage(
                      role="user",
                      text="hello",
                  )],
        tools=[ToolDefinition(name="echo", description="Echo text", parameters={"type": "object"})],
        tool_choice=ToolChoiceFunction(name="echo"),
        reasoning_effort="high",
    )
    asyncio.run(model_provider.acomplete(request))
    parameters = endpoint.requests[0]
    assert parameters["model"] == "gpt-6-luna"
    assert parameters["reasoning"] == {"effort": "high"}
    assert "max_output_tokens" not in parameters
    assert "temperature" not in parameters
    assert "extra_body" not in parameters
    assert parameters["store"] is False
    assert parameters["stream"] is False
    assert "reasoning.encrypted_content" in parameters["include"]
    assert parameters["tools"] == [{
        "type": "function", "name": "echo", "description": "Echo text",
        "parameters": {"type": "object"}, "strict": False,
    }]
    assert parameters["tool_choice"] == {"type": "function", "name": "echo"}
    assert "messages" not in parameters and "reasoning_effort" not in parameters
    assert request.reasoning_effort == "high"


def test_provider_usage_normalizes_to_shared_token_usage():
    model_provider, _ = provider(response([MESSAGE]))
    result = asyncio.run(model_provider.acomplete(ChatRequest(messages=[ChatMessage(
        role="user",
        text="hello",
    )])))
    assert result.usage == TokenUsage(input_tokens=20, output_tokens=10, total_tokens=30)


def test_parses_response_and_replays_items_in_original_order_after_reload(tmp_path):
    model_provider, endpoint = provider(response(), response([MESSAGE]))
    result = asyncio.run(model_provider.acomplete(ChatRequest(messages=[ChatMessage(
        role="user",
        text="hello",
    )])))
    assert result.content == "Checking."
    assert result.tool_calls == [ToolCall(id="call_1", name="echo", arguments={"text": "hello"})]
    assert result.finish_reason == "tool_calls"
    assert result.usage.total_tokens == 30

    store = SessionStore(tmp_path)
    data = store.create(session_id="replay")
    store.append_user_message(data, "hello", user_timezone="UTC")
    store.append_assistant_response(data, result)
    store.append_tool_result(data, tool_call=result.tool_calls[0], result=ToolResult(name="echo", ok=True, content="hello"))
    # Reload from disk, rather than reuse mutable provider/client state.
    view = SessionStore(tmp_path).load("replay")
    messages = ContextBuilder().build_provider_messages(view)
    asyncio.run(model_provider.acomplete(ChatRequest(messages=messages)))
    assert endpoint.requests[-1]["input"][1:4] == [REASONING, MESSAGE, CALL]
    assert endpoint.requests[-1]["input"][4] == {"type": "function_call_output", "call_id": "call_1", "output": "hello"}


@pytest.mark.parametrize("change", ["model", "provider"])
def test_does_not_replay_state_after_model_or_visible_history_changes(change):
    model_provider, _ = provider(response())
    result = asyncio.run(model_provider.acomplete(ChatRequest(messages=[])))
    message = ChatMessage(role="assistant", parts=result.parts)
    if change == "model":
        model_provider, _ = provider(response(), model="other-model")
    elif change == "provider":
        message.parts[0].metadata["provider"] = "other-provider"
    model_provider, endpoint = (model_provider, model_provider.endpoint)
    if change != "model":
        endpoint.results.append(response([MESSAGE]))
    asyncio.run(model_provider.acomplete(ChatRequest(messages=[message])))
    sent = endpoint.requests[-1]["input"]
    assert all(item.get("type") != "reasoning" for item in sent)


@pytest.mark.parametrize("status,details,expected", [
    ("incomplete", {"reason": "max_output_tokens"}, "length"),
    ("incomplete", {"reason": "content_filter"}, "content_filter"),
])
def test_incomplete_responses_never_expose_executable_tool_calls(status, details, expected):
    model_provider, _ = provider(response(status=status, incomplete_details=details))
    result = asyncio.run(model_provider.acomplete(ChatRequest(messages=[])))
    assert result.finish_reason == expected
    assert result.tool_calls == []
    assert not any(part.kind == "reasoning" for part in result.parts)


@pytest.mark.parametrize("bad_call", [
    {**CALL, "arguments": '{"text":'}, {**CALL, "arguments": "[]"},
    {**CALL, "call_id": ""}, {**CALL, "name": ""}, {**CALL, "status": "in_progress"},
    {**CALL, "arguments": ""}, {**CALL, "arguments": "   "},
    {key: value for key, value in CALL.items() if key != "arguments"},
])
def test_bad_tool_calls_discard_entire_group(bad_call):
    if bad_call.get("call_id"):
        bad_call = {**bad_call, "call_id": "call_bad"}
    model_provider, _ = provider(response([REASONING, CALL, bad_call]))
    result = asyncio.run(model_provider.acomplete(ChatRequest(messages=[])))
    assert not result.tool_calls and not any(part.kind == "reasoning" for part in result.parts)


def test_duplicate_call_ids_discard_entire_group():
    model_provider, _ = provider(response([CALL, {**CALL, "id": "fc_2"}]))
    result = asyncio.run(model_provider.acomplete(ChatRequest(messages=[])))
    assert not result.tool_calls and not any(part.kind == "reasoning" for part in result.parts)


def test_explicit_empty_json_object_is_valid_for_no_argument_tools():
    model_provider, _ = provider(response([{**CALL, "arguments": "{}"}]))
    result = asyncio.run(model_provider.acomplete(ChatRequest(messages=[])))
    assert result.tool_calls == [ToolCall(id="call_1", name="echo", arguments={})]
    assert result.parts[0].kind == "tool_call"


def test_sdk_http_context_error_requests_compaction():
    async def scenario():
        import httpx
        from openai import AsyncOpenAI

        def handle(request):
            return httpx.Response(400, json={"error": {
                "type": "invalid_request_error", "code": "context_length_exceeded",
                "message": "The input exceeds the context window.",
            }})
        def create_client(**options):
            return AsyncOpenAI(
                **options,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
            )

        model_provider = OpenAIResponsesProvider(name="official", model="gpt-6-luna", api_key="test-key")
        with patch("openai.AsyncOpenAI", side_effect=create_client):
            with pytest.raises(ProviderError) as exc:
                await model_provider.acomplete(ChatRequest(messages=[]))
            assert exc.value.requires_compaction

    asyncio.run(scenario())


def test_maps_image_and_pdf_input_and_tool_results():
    model_provider, endpoint = provider(response([MESSAGE]))
    asyncio.run(model_provider.acomplete(ChatRequest(messages=[
        ChatMessage(
            role="user",
            content_parts=[
            ContentPart(type="text", text="Read"),
            ContentPart(type="image", media_type="image/png", data_base64="aGVsbG8="),
            ContentPart(type="file", media_type="application/pdf", filename="a.pdf", data_base64="cGRm"),
        ],
            text="Read",
        ),
        ChatMessage(
            role="assistant",
            parts=[MessagePart.from_tool_call(call) for call in [ToolCall(id="call_old", name="echo", arguments={"text": "old"})]],
        ),
        ChatMessage(
            role="tool",
            tool_call_id="call_old",
            text="old",
        ),
    ])))
    items = endpoint.requests[0]["input"]
    assert items[0]["content"] == [
        {"type": "input_text", "text": "Read"},
        {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="},
        {"type": "input_file", "filename": "a.pdf", "file_data": "data:application/pdf;base64,cGRm"},
    ]
    assert items[1]["type"] == "function_call" and items[1]["call_id"] == "call_old"
    assert items[2] == {"type": "function_call_output", "call_id": "call_old", "output": "old"}


def test_factory_selects_responses_provider_and_keeps_effort_configuration():
    config = AppConfig(env={"OPENAI_API_KEY": "test-key"}, model_config={
        "providers": {"official": {"api_key_env": "OPENAI_API_KEY"}},
        "models": {"official/gpt-6-luna": {"supported_reasoning_efforts": ["high"], "reasoning_effort": "high"}},
    })
    profile = config.model_catalog().models["official/gpt-6-luna"]
    model_provider = create_provider_for_model(config, profile)
    assert isinstance(model_provider, OpenAIResponsesProvider)
    assert profile.reasoning_effort == "high"


def test_actual_sdk_transport_serializes_request_and_replays_encrypted_reasoning():
    async def scenario():
        import httpx
        from openai import AsyncOpenAI

        sent = []
        def handle(request):
            sent.append(json.loads(request.content))
            assert request.url.path == "/v1/responses"
            payload = response(object="response", created_at=1)
            return httpx.Response(200, json=payload)

        def create_client(**options):
            return AsyncOpenAI(
                **options,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
            )

        model_provider = OpenAIResponsesProvider(name="official", model="gpt-6-luna", api_key="test-key")
        with patch("openai.AsyncOpenAI", side_effect=create_client):
            request = ChatRequest(messages=[ChatMessage(
                role="user",
                text="hello",
            )], reasoning_effort="high")
            result = await model_provider.acomplete(request)
            await model_provider.acomplete(ChatRequest(messages=[
                ChatMessage(role="assistant", parts=result.parts),
                ChatMessage(
                    role="tool",
                    tool_call_id="call_1",
                    text="hello",
                ),
            ]))
            assert sent[0]["reasoning"] == {"effort": "high"}
            assert sent[1]["input"][0]["encrypted_content"] == "opaque-state"
            assert sent[1]["input"][1]["phase"] == "commentary"
            assert sent[1]["input"][2]["call_id"] == "call_1"

    asyncio.run(scenario())


def test_actual_sdk_transport_replays_async_call_using_responses_wire_key():
    async def scenario():
        import httpx
        from openai import AsyncOpenAI

        sent = []
        outputs = [
            [{"id": "fc_async", "type": "function_call", "call_id": "call_async",
              "name": "shell", "arguments": "{}", "async": True, "status": "completed"}],
            [{"id": "msg_done", "type": "message", "role": "assistant", "status": "completed",
              "content": [{"type": "output_text", "text": "done", "annotations": []}]}],
        ]

        def handle(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={
                "id": f"resp_{len(sent)}", "model": "gpt-6-luna", "status": "completed",
                "output": outputs.pop(0),
            })

        def create_client(**options):
            return AsyncOpenAI(
                **options,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
            )

        model_provider = OpenAIResponsesProvider(name="official", model="gpt-6-luna", api_key="test-key")
        with patch("openai.AsyncOpenAI", side_effect=create_client):
            first = await model_provider.acomplete(ChatRequest(messages=[ChatMessage(
                role="user",
                text="start",
            )]))
            assert first.tool_calls[0].async_execution is True
            await model_provider.acomplete(ChatRequest(messages=[
                ChatMessage(
                    role="user",
                    text="start",
                ),
                ChatMessage(role="assistant", parts=first.parts),
                ChatMessage(
                    role="tool",
                    tool_call_id="call_async",
                    text="tool result",
                ),
            ]))

            replayed = next(item for item in sent[1]["input"] if item.get("type") == "function_call")
            assert replayed["async"] is True
            assert "async_" not in replayed

    asyncio.run(scenario())


def test_actual_sdk_additions_keep_base_tools_and_history_position_with_reasoning():
    async def scenario():
        import httpx
        from openai import AsyncOpenAI

        sent = []
        def handle(request):
            body = json.loads(request.content)
            sent.append(body)
            payload = response(object="response", created_at=1)
            return httpx.Response(200, json=payload)

        def create_client(**options):
            return AsyncOpenAI(
                **options,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
            )

        model_provider = OpenAIResponsesProvider(name="official", model="gpt-6-luna", api_key="test-key")
        base = [ToolDefinition("view", "Read", {"type": "object"})]
        added = ToolDefinition("present", "Form", {"type": "object"}, async_execution=True)
        first = ChatRequest([ChatMessage("user", "start")], tools=base)
        with patch("openai.AsyncOpenAI", side_effect=create_client):
            result = await model_provider.acomplete(first)
            history = [*first.messages,
                       ChatMessage("assistant", parts=result.parts),
                       ChatMessage("tool", "done", tool_call_id="call_1"),
                       ChatMessage("system", "Incremental update", additional_tools=[added])]
            second = ChatRequest(history, tools=base)
            await model_provider.acomplete(second)
            await model_provider.acomplete(ChatRequest([*history, ChatMessage("user", "next turn")], tools=base))
            assert sent[0]["tools"] == sent[1]["tools"] == sent[2]["tools"]
            assert not any(item.get("type") == "additional_tools" for item in sent[0]["input"])
            for body in sent[1:]:
                assert body["store"] is False
                assert body["input"][1] == REASONING
                assert body["input"][3]["name"] == "echo"
                assert body["input"][4]["type"] == "function_call_output"
                assert body["input"][5] == {"type": "additional_tools", "role": "developer", "tools": [
                    {"type": "function", "name": "present", "description": "Form", "parameters": {"type": "object"}, "strict": False, "async": True}]}

    asyncio.run(scenario())


@pytest.mark.parametrize("code,expected", [
    ("rate_limit_exceeded", ProviderErrorKind.RATE_LIMIT),
    ("context_length_exceeded", ProviderErrorKind.PROMPT_TOO_LONG),
    ("server_error", ProviderErrorKind.SERVER_ERROR),
    ("unknown_code", ProviderErrorKind.API_ERROR),
])
def test_failed_response_uses_existing_error_classification(code, expected):
    failed = response([], status="failed", error={"code": code, "message": "Request failed"})
    model_provider, _ = provider(failed)
    with pytest.raises(ProviderError) as exc:
        asyncio.run(model_provider.acomplete(ChatRequest(messages=[])))
    assert exc.value.kind == expected
    assert exc.value.message == f"{code}: Request failed"


def test_compaction_excludes_old_reasoning_and_preserves_the_remaining_tail(tmp_path):
    from raccoon.modules.sessions.compaction import CompactionRecord

    model_provider, endpoint = provider(response(), response([MESSAGE]), response([MESSAGE]))
    store = SessionStore(tmp_path)
    data = store.create(session_id="checkpoint")
    store.append_user_message(data, "old question", user_timezone="UTC")
    store.append_assistant_response(data, asyncio.run(model_provider.acomplete(ChatRequest(messages=[]))))
    store.append_tool_result(data, tool_call=ToolCall(id="call_1", name="echo", arguments={}), result=ToolResult(name="echo", ok=True, content="hello"))
    tail = store.append_user_message(data, "new question", user_timezone="UTC")
    store.append_assistant_response(data, asyncio.run(model_provider.acomplete(ChatRequest(messages=[]))))
    view = store.load("checkpoint")
    compaction = CompactionRecord(id="ck", session_id="checkpoint", created_at="2026-11-16T00:00:00Z", summary="Old task complete", first_kept_message_id=tail, tokens_before=100, reasoning_cutoff_message_id=view.messages[-1].id)
    messages = ContextBuilder().build_provider_messages(view, compaction=compaction)
    asyncio.run(model_provider.acomplete(ChatRequest(messages=messages)))
    assert "opaque-state" not in str(endpoint.requests[-1]["input"])
    assert "Old task complete" in str(endpoint.requests[-1]["input"])
    assert endpoint.requests[-1]["input"][-1]["phase"] == "commentary"


def test_unspecified_reasoning_uses_service_defaults():
    model_provider, endpoint = provider(response([MESSAGE]))
    asyncio.run(model_provider.acomplete(ChatRequest(messages=[])))
    sent = endpoint.requests[0]
    assert "reasoning" not in sent
    assert "max_output_tokens" not in sent
    assert "temperature" not in sent
    assert "extra_body" not in sent


@pytest.mark.parametrize("change", [None, "model", "provider", "no_encrypted_reasoning"])
def test_budget_only_counts_reasoning_that_can_actually_replay(change):
    model_provider, _ = provider(response(usage={"output_tokens_details": {"reasoning_tokens": 1234}}))
    result = asyncio.run(model_provider.acomplete(ChatRequest(messages=[])))
    message = ChatMessage(role="assistant", parts=result.parts)
    if change == "model":
        model_provider, _ = provider(model="other-model")
    elif change == "provider":
        message.parts[0].metadata["provider"] = "other"
    elif change == "no_encrypted_reasoning":
        message.parts[0].metadata["reasoning_item"].pop("encrypted_content")
    assert model_provider.estimate_replay_tokens([message]) == (1234 if change is None else 0)


@pytest.mark.parametrize("hint", [None, True, -1, "1000"])
def test_missing_or_invalid_reasoning_usage_does_not_measure_ciphertext(hint):
    encrypted = {**REASONING, "encrypted_content": "x" * 100_000}
    model_provider, _ = provider(response([encrypted, MESSAGE], usage={"output_tokens_details": {"reasoning_tokens": hint}}))
    result = model_provider._parse_response_params(Response.model_construct(**response([encrypted, MESSAGE], usage={"output_tokens_details": {"reasoning_tokens": hint}})))
    assert model_provider.estimate_replay_tokens([ChatMessage(role="assistant", parts=result.parts)]) == 0


def test_responses_summary_uses_its_own_effort_and_budget_without_replaying_state(tmp_path):
    from raccoon.modules.context.summary import summarize


    model_provider, main_endpoint = provider(
        response(), response([MESSAGE]),
    )
    summary_provider, summary_endpoint = provider(response([{
        **MESSAGE, "phase": "final_answer",
        "content": [{"type": "output_text", "text": "已读取文件，下一步运行测试。"}],
    }]), model="deepseek-flash")
    store = SessionStore(tmp_path)
    data = store.create(session_id="summary_options")
    store.append_user_message(data, "Read files then run tests", user_timezone="UTC")
    result = asyncio.run(model_provider.acomplete(ChatRequest(messages=[], reasoning_effort="high")))
    store.append_assistant_response(data, result)
    store.append_tool_result(data, tool_call=result.tool_calls[0], result=ToolResult(name="echo", ok=True, content="hello"))
    effort = "low"
    summary, _ = asyncio.run(summarize(
        store.load("summary_options").messages, provider=summary_provider, reasoning_effort=effort,
    ))
    sent = summary_endpoint.requests[-1]
    assert sent["model"] == "deepseek-flash"
    assert sent["reasoning"]["effort"] == "low"
    assert "max_output_tokens" not in sent
    assert "opaque-state" not in str(sent["input"])
    assert "Read files then run tests" in str(sent["input"])
    assert "下一步运行测试" in summary
    # Compaction options must not change subsequent main-agent requests.
    asyncio.run(model_provider.acomplete(ChatRequest(messages=[], reasoning_effort="high")))
    assert main_endpoint.requests[-1]["reasoning"]["effort"] == "high"


@pytest.mark.parametrize("outcome", ["success", "error", "cancelled"])
def test_request_owned_client_closes_after_success_error_or_cancellation(monkeypatch, outcome):
    async def scenario():
        entered = asyncio.Event()
        clients = []
        options = []

        class RequestClient:
            def __init__(self, **client_options):
                options.append(client_options)
                clients.append(self)
                self.responses = self
                self.closed = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, error_type, error, traceback):
                self.closed = True

            async def create(self, **parameters):
                assert parameters["stream"] is False
                entered.set()
                if outcome == "error":
                    raise RuntimeError("request failed")
                if outcome == "cancelled":
                    await asyncio.Event().wait()
                return Response.model_construct(**response([MESSAGE]))

        monkeypatch.setattr("openai.AsyncOpenAI", RequestClient)
        model_provider = OpenAIResponsesProvider(
            name="official", model="gpt-6-luna", api_key="test-key",
        )
        request = ChatRequest(messages=[ChatMessage("user", "hello")])
        task = asyncio.create_task(model_provider.acomplete(request))
        await entered.wait()
        if outcome == "cancelled":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome == "error":
            with pytest.raises(ProviderError, match="request failed"):
                await task
        else:
            result = await task
            assert result.content == "Checking."
            await model_provider.acomplete(request)
            assert len(clients) == 2
        assert all(client.closed for client in clients)
        assert all(client_options["max_retries"] == 2 for client_options in options)

    asyncio.run(scenario())
