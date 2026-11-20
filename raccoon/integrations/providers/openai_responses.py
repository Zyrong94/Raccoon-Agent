"""OpenAI Responses adapter with local, stateless reasoning replay."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from raccoon.runtime.model_provider import ChatProvider
from raccoon.integrations.providers.errors import ProviderError, ProviderErrorKind, classify_response_error, classify_provider_exception
from raccoon.integrations.providers.response_helpers import read_field, token_usage
from raccoon.runtime.model_types import (
    ChatMessage, ChatRequest, ChatResponse, ProviderCapabilities,
    ProviderDiagnostics, ToolCall, ToolChoiceFunction,
)
from raccoon.utils.json_utils import dumps_json, loads_json_object


RESPONSES_CAPABILITIES = ProviderCapabilities(
    supports_parallel_tool_calls=True,
    supports_vision=True, supports_pdf_input=True,
    supports_async_tools=True,
    supports_tool_additions=True,
)


class OpenAIResponsesProvider(ChatProvider):
    """Keep Responses-specific output inside opaque provider_state, not assistant text."""

    # 保存模型配置和客户端参数，暂不发送请求。
    def __init__(
        self, *, name: str, model: str, api_key: str, base_url: str | None = None,
        capabilities: ProviderCapabilities | None = None,
        extra_headers: dict[str, str] | None = None, extra_body: dict[str, Any] | None = None,
    ) -> None:
        self._name = name
        self._model = model
        self._base_url = base_url
        self._capabilities = capabilities or RESPONSES_CAPABILITIES
        self._extra_headers = dict(extra_headers or {})
        self._extra_body = deepcopy(extra_body or {})
        self._api_key = api_key

    # 返回服务配置名称。
    @property
    def name(self) -> str:
        return self._name

    # 返回当前模型名称。
    @property
    def model(self) -> str:
        return self._model

    # 返回模型服务地址。
    @property
    def base_url(self) -> str | None:
        return self._base_url

    # 返回当前模型的能力配置。
    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    # 返回额外请求头的副本。
    @property
    def extra_headers(self) -> dict[str, str]:
        return dict(self._extra_headers)

    # 返回额外请求参数的副本。
    @property
    def extra_body(self) -> dict[str, Any]:
        return deepcopy(self._extra_body)

    # 估算历史推理数据回放占用的 token 数。
    def estimate_replay_tokens(self, messages: list[ChatMessage]) -> int:
        tokens = 0
        for message in messages:
            state = message.provider_state
            if message.role != "assistant" or not self._can_replay(message, state):
                continue
            has_encrypted_reasoning = False
            for item in state["output"]:
                if item.get("type") == "reasoning" and item.get("encrypted_content"):
                    has_encrypted_reasoning = True
                    break
            if not has_encrypted_reasoning:
                continue
            hint = state.get("reasoning_tokens")
            if isinstance(hint, int) and not isinstance(hint, bool) and hint > 0:
                # shortcut: generation usage approximates replay size; calibrate with input usage if needed.
                tokens += hint
        return tokens


    async def acomplete(self, request: ChatRequest) -> ChatResponse:
        """等待完整响应；临时网络错误由官方 SDK 最多额外重试两次。"""
        from openai import AsyncOpenAI

        parameters = self._build_response_params(request)
        try:
            # 每次请求结束后关闭连接，避免跨事件循环复用客户端。
            async with AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                default_headers=self._extra_headers,
                max_retries=2,
            ) as client:
                response = await client.responses.create(**parameters, stream=False)
        except Exception as error:
            error_kind = classify_provider_exception(error)
            raise ProviderError(error_kind, str(error)) from error
        return self._parse_response(response)

    # 校验请求选项并组装 Responses 参数。
    def _build_response_params(self, request: ChatRequest) -> dict[str, Any]:
        all_tools = list(request.tools)
        for message in request.messages:
            all_tools.extend(message.additional_tools)

        has_async_tools = any(tool.async_execution for tool in request.tools)
        if has_async_tools and not self.capabilities.supports_async_tools:
            raise ProviderError(ProviderErrorKind.UNSUPPORTED, "provider does not support native async tools")
        if all_tools and not self.capabilities.supports_tools:
            raise ProviderError(ProviderErrorKind.CONFIG_ERROR, f"provider {self.name} 不支持 tool calling")
        extra = deepcopy({**self._extra_body, **request.extra_body})
        reserved = {
            "model", "input", "messages", "tools", "tool_choice", "stream", "store",
            "previous_response_id", "conversation", "background", "truncation",
            "max_output_tokens",
        }
        overridden_fields = reserved.intersection(extra)
        if overridden_fields:
            field_names = ", ".join(sorted(overridden_fields))
            raise ProviderError(ProviderErrorKind.CONFIG_ERROR, f"Responses extra_body 不得覆盖：{field_names}")
        reasoning = extra.pop("reasoning", {})
        if not isinstance(reasoning, dict):
            raise ProviderError(ProviderErrorKind.CONFIG_ERROR, "Responses reasoning 必须是对象")
        effort = extra.pop("reasoning_effort", None)
        if effort is not None:
            if "effort" in reasoning and reasoning["effort"] != effort:
                raise ProviderError(ProviderErrorKind.CONFIG_ERROR, "reasoning_effort 与 reasoning.effort 冲突")
            reasoning["effort"] = effort
        include = extra.pop("include", [])
        if not isinstance(include, list) or not all(isinstance(value, str) for value in include):
            raise ProviderError(ProviderErrorKind.CONFIG_ERROR, "Responses include 必须是字符串列表")
        included_fields = list(dict.fromkeys(include))
        if "reasoning.encrypted_content" not in included_fields:
            included_fields.append("reasoning.encrypted_content")
        params: dict[str, Any] = {
            "model": self.model,
            "input": self._to_response_input(request.messages),
            "store": False,
            "include": included_fields,
        }
        if reasoning:
            params["reasoning"] = reasoning
        if request.temperature is not None:
            params["temperature"] = request.temperature
        if request.max_tokens is not None:
            params["max_output_tokens"] = request.max_tokens
        if request.tools:
            tools = []
            for tool in request.tools:
                definition = {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                    "strict": False,
                }
                if tool.async_execution:
                    definition["async"] = True
                tools.append(definition)
            params["tools"] = tools
            if isinstance(request.tool_choice, ToolChoiceFunction):
                params["tool_choice"] = {"type": "function", "name": request.tool_choice.name}
            elif request.tool_choice is None or request.tool_choice in {"auto", "none", "required"}:
                if request.tool_choice is not None:
                    params["tool_choice"] = request.tool_choice
            else:
                raise ProviderError(ProviderErrorKind.CONFIG_ERROR, f"不支持的 tool_choice：{request.tool_choice!r}")
            if self.capabilities.supports_parallel_tool_calls:
                params["parallel_tool_calls"] = True
        if extra:
            params["extra_body"] = extra
        return params

    # 将历史消息和工具结果转换为 Responses 输入。
    def _to_response_input(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for message in messages:
            if message.additional_tools and self.capabilities.supports_tool_additions:
                additional_tools = []
                for tool in message.additional_tools:
                    definition = {
                        "type": "function",
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                        "strict": False,
                    }
                    if tool.async_execution and self.capabilities.supports_async_tools:
                        definition["async"] = True
                    additional_tools.append(definition)
                result.append({
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": additional_tools,
                })
            state = message.provider_state
            if message.role == "assistant" and self._can_replay(message, state):
                result.extend(_canonical_response_item(item) for item in state["output"])
            elif message.role == "assistant":
                if message.content:
                    result.append({"role": "assistant", "content": message.content})
                for call in message.tool_calls:
                    arguments = call.arguments
                    if not isinstance(arguments, str):
                        arguments = dumps_json(arguments)
                    call_item = {
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": arguments,
                    }
                    if call.async_execution:
                        call_item["async"] = True
                    result.append(call_item)
            elif message.role == "tool":
                result.append({"type": "function_call_output", "call_id": message.tool_call_id, "output": _input_content(message)})
            else:
                result.append({"role": message.role, "content": _input_content(message)})
        return result

    # 检查历史响应数据是否仍可用于当前消息回放。
    def _can_replay(self, message: ChatMessage, state: dict[str, Any]) -> bool:
        if not state or state.get("protocol") != "openai-responses":
            return False
        if state.get("provider") != self.name or state.get("model") != self.model:
            return False
        # Compaction or edits must not resurrect old visible content from raw output.
        content, calls, _, warnings, _ = _read_output(state.get("output", []))
        return not warnings and content == message.content and calls == message.tool_calls

    # 解析响应状态、正文、工具调用和用量。
    def _parse_response(self, response: Any) -> ChatResponse:
        status = read_field(response, "status")
        if status == "failed":
            error = read_field(response, "error") or {"message": "Responses request failed"}
            _raise_response_error(error)
        if status not in {"completed", "incomplete"}:
            raise ProviderError(ProviderErrorKind.API_ERROR, f"Unexpected Responses status: {status!r}")
        output = [_item_dict(item) for item in read_field(response, "output", [])]
        content, calls, reasoning, warnings, refused = _read_output(output)
        diagnostics = ProviderDiagnostics(reasoning=reasoning or None, raw_finish_reason=status, warnings=warnings)
        finish_reason = "tool_calls" if calls else "stop"
        if status == "incomplete":
            incomplete_details = read_field(response, "incomplete_details", {})
            reason = read_field(incomplete_details, "reason")
            finish_reason = "content_filter" if reason == "content_filter" else "length"
            diagnostics.warnings.append(f"Responses incomplete ({reason})，丢弃不可执行的 tool calls")
            calls = []
        elif refused:
            finish_reason = "content_filter"
            calls = []
        state: dict[str, Any] = {}
        if status == "completed" and not warnings and not refused:
            state = {"protocol": "openai-responses", "provider": self.name, "model": self.model, "output": output}
        usage = read_field(response, "usage")
        output_details = read_field(usage, "output_tokens_details")
        reasoning_tokens = read_field(output_details, "reasoning_tokens")
        valid_reasoning_tokens = (
            isinstance(reasoning_tokens, int)
            and not isinstance(reasoning_tokens, bool)
            and reasoning_tokens >= 0
        )
        if state and valid_reasoning_tokens:
            state["reasoning_tokens"] = reasoning_tokens
        usage_summary = token_usage(
            read_field(usage, "input_tokens"),
            read_field(usage, "output_tokens"),
            read_field(usage, "total_tokens"),
        )
        return ChatResponse(
            provider=self.name, model=read_field(response, "model", self.model), content=content,
            tool_calls=calls, finish_reason=finish_reason,
            usage=usage_summary,
            diagnostics=diagnostics, raw=response, provider_state=state,
        )


# 将响应项转换为字段规范一致的字典。
def _item_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        value = deepcopy(item)
    else:
        value = item.model_dump(mode="json", exclude_none=True)
    return _canonical_response_item(value)


# 将 SDK 的 async_ 字段统一为协议字段 async。
def _canonical_response_item(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize SDK aliases while keeping Responses wire names in persisted state."""

    value = deepcopy(item)
    if value.get("type") == "function_call" and "async_" in value:
        async_execution = value.pop("async_")
        value.setdefault("async", async_execution)
    return value


# 提取正文和推理摘要，并校验工具调用。
def _read_output(output: list[dict[str, Any]]) -> tuple[str, list[ToolCall], str, list[str], bool]:
    texts: list[str] = []
    reasoning: list[str] = []
    calls: list[ToolCall] = []
    warnings: list[str] = []
    refused = False
    call_ids: set[str] = set()
    for item in output:
        kind = item.get("type")
        if kind == "message":
            text_parts = []
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") == "refusal":
                    refused = True
                    text_parts.append(part.get("refusal", ""))
            if text_parts:
                texts.append("".join(text_parts))
        elif kind == "reasoning":
            reasoning.extend(part.get("text", "") for part in item.get("summary", []))
        elif kind == "function_call":
            raw_arguments = item.get("arguments")
            arguments = None
            if isinstance(raw_arguments, str) and raw_arguments.strip():
                arguments = loads_json_object(raw_arguments)
            call_id = item.get("call_id")
            name = item.get("name")
            invalid_call = (
                not call_id
                or not name
                or call_id in call_ids
                or not isinstance(arguments, dict)
                or item.get("status") not in {None, "completed"}
            )
            if invalid_call:
                warnings.append("Responses function_call 缺少标识、重复或参数不是完整 JSON object，已丢弃整组工具调用")
                continue
            call_ids.add(call_id)
            calls.append(ToolCall(
                id=call_id, name=name, arguments=arguments,
                async_execution=item.get("async", item.get("async_")) is True,
            ))
        else:
            warnings.append(f"Unsupported Responses output item: {kind}")
    if warnings:
        calls = []
    content = "\n".join(texts)
    reasoning_summary = "".join(reasoning)
    return content, calls, reasoning_summary, warnings, refused


# 将文本、图片和 PDF 转换为接口输入内容。
def _input_content(message: ChatMessage) -> str | list[dict[str, Any]]:
    if message.content_parts is None:
        return message.content
    parts: list[dict[str, Any]] = []
    for part in message.content_parts:
        if part.type == "text" and part.text is not None:
            parts.append({"type": "input_text", "text": part.text})
        elif part.type == "image" and part.media_type and part.data_base64:
            image_url = f"data:{part.media_type};base64,{part.data_base64}"
            parts.append({"type": "input_image", "image_url": image_url})
        elif part.type == "file" and part.media_type == "application/pdf" and part.data_base64:
            file_data = f"data:{part.media_type};base64,{part.data_base64}"
            parts.append({
                "type": "input_file",
                "filename": part.filename or "document.pdf",
                "file_data": file_data,
            })
        else:
            raise ProviderError(ProviderErrorKind.UNSUPPORTED, f"Responses 不支持附件：{part.type}/{part.media_type}")
    return parts


# 将失败响应转换为保留原始信息的项目异常。
def _raise_response_error(error: Any) -> None:
    message = read_field(error, "message", "Responses API error")
    code = read_field(error, "code", "")
    text = f"{code}: {message}" if code else message
    kind = classify_response_error(code)
    raise ProviderError(kind, text)
