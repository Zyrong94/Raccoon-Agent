"""OpenAI Responses adapter with local, stateless reasoning replay."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from openai.types.responses import Response, ResponseOutputItem

from raccoon.runtime.model_provider import ChatProvider
from raccoon.integrations.providers.errors import ProviderError, ProviderErrorKind, classify_response_error, classify_provider_exception
from raccoon.integrations.providers.response_helpers import token_usage
from raccoon.runtime.model_types import (
    ChatMessage, ChatRequest, ChatResponse, ProviderCapabilities,
    MessagePart, ToolChoiceFunction,
)
from raccoon.utils.json_utils import dumps_json


RESPONSES_CAPABILITIES = ProviderCapabilities(
    supports_parallel_tool_calls=True,
    supports_vision=True, supports_pdf_input=True,
    supports_async_tools=True,
    supports_tool_additions=True,
)


class OpenAIResponsesProvider(ChatProvider):
    """Store assistant output once as ordered text, reasoning and tool-call parts."""

    # 保存模型配置和客户端参数，暂不发送请求。
    def __init__(
        self, *, name: str, model: str, api_key: str, base_url: str | None = None,
        capabilities: ProviderCapabilities | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._name = name
        self._model = model
        self._base_url = base_url
        self._capabilities = capabilities or RESPONSES_CAPABILITIES
        self._extra_headers = dict(extra_headers or {})
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

    # 估算历史推理数据回放占用的 token 数。
    def estimate_replay_tokens(self, messages: list[ChatMessage]) -> int:
        tokens = 0
        for message in messages:
            for part in message.parts:
                if part.kind != "reasoning":
                    continue
                if part.metadata.get("provider") != self.name or part.metadata.get("model") != self.model:
                    continue
                if not part.metadata["reasoning_item"].get("encrypted_content"):
                    continue
                hint = part.metadata.get("reasoning_tokens")
                if isinstance(hint, int) and not isinstance(hint, bool) and hint > 0:
                    tokens += hint
        return tokens


    async def acomplete(self, request: ChatRequest) -> ChatResponse:
        """等待完整响应；临时网络错误由官方 SDK 最多额外重试两次。"""
        from openai import AsyncOpenAI

        parameters = self._build_request_params(request)
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
        return self._parse_response_params(response)

    # 将内部请求转换为 Responses 参数。
    def _build_request_params(self, request: ChatRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "input": self._build_input_items(request.messages),
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if request.reasoning_effort is not None:
            params["reasoning"] = {"effort": request.reasoning_effort}
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
            elif request.tool_choice is not None:
                params["tool_choice"] = request.tool_choice
            if self.capabilities.supports_parallel_tool_calls:
                params["parallel_tool_calls"] = True
        return params

    # 将历史消息和工具结果转换为 Responses 输入。
    def _build_input_items(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
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
            if message.role == "assistant":
                previous_text_index = None
                for part in message.parts:
                    if part.kind == "reasoning":
                        previous_text_index = None
                        if part.metadata.get("provider") == self.name and part.metadata.get("model") == self.model:
                            result.append(deepcopy(part.metadata["reasoning_item"]))
                    elif part.kind == "text":
                        index = part.metadata.get("output_index")
                        content_item = deepcopy(part.metadata.get("content_fields", {"type": "output_text"}))
                        if content_item is not None:
                            key = "refusal" if content_item["type"] == "refusal" else "text"
                            content_item[key] = part.content
                        if index is not None and index == previous_text_index:
                            if content_item is not None:
                                result[-1]["content"].append(content_item)
                        else:
                            item = deepcopy(part.metadata.get("message_fields", {"type": "message", "role": "assistant"}))
                            item["content"] = [] if content_item is None else [content_item]
                            result.append(item)
                        previous_text_index = index
                    elif part.kind == "tool_call":
                        previous_text_index = None
                        arguments = part.metadata["arguments"]
                        if not isinstance(arguments, str):
                            arguments = dumps_json(arguments)
                        item = deepcopy(part.metadata.get("api_fields", {}))
                        item.update(type="function_call", call_id=part.metadata["tool_call_id"],
                                    name=part.metadata["tool_name"], arguments=arguments)
                        if "async_execution" in part.metadata:
                            item["async"] = part.metadata["async_execution"]
                        result.append(item)
                continue
            content = _build_message_content(message)
            if message.role == "tool":
                result.append({"type": "function_call_output", "call_id": message.tool_call_id, "output": content})
            else:
                result.append({"role": message.role, "content": content})
        return result

    # 解析响应状态、正文、工具调用和用量。
    def _parse_response_params(self, response: Response) -> ChatResponse:
        status = response.status
        if status == "failed":
            error = response.error
            message = error.message if error is not None else "Responses request failed"
            code = error.code if error is not None else None
            text = f"{code}: {message}" if code else message
            raise ProviderError(classify_response_error(code), text)
        if status not in {"completed", "incomplete"}:
            raise ProviderError(ProviderErrorKind.API_ERROR, f"Unexpected Responses status: {status!r}")
        parts, invalid_output, refused = _parse_output_items(response.output)
        has_calls = any(part.kind == "tool_call" for part in parts)
        finish_reason = "tool_calls" if has_calls else "stop"
        if status == "incomplete":
            incomplete_details = response.incomplete_details
            reason = incomplete_details.reason if incomplete_details is not None else None
            finish_reason = "content_filter" if reason == "content_filter" else "length"
        elif refused:
            finish_reason = "content_filter"
        if status != "completed" or invalid_output or refused:
            parts = [part for part in parts if part.kind == "text"]
            if finish_reason == "tool_calls":
                finish_reason = "stop"
        usage = response.usage
        reasoning_tokens = None
        if usage is not None and usage.output_tokens_details is not None:
            reasoning_tokens = usage.output_tokens_details.reasoning_tokens
        for part in parts:
            if part.kind == "reasoning":
                part.metadata.update(provider=self.name, model=self.model)
        # Usage is per response, so count it once even if reasoning is interleaved.
        if isinstance(reasoning_tokens, int) and not isinstance(reasoning_tokens, bool) and reasoning_tokens >= 0:
            for part in parts:
                if part.kind == "reasoning" and part.metadata["reasoning_item"].get("encrypted_content"):
                    part.metadata["reasoning_tokens"] = reasoning_tokens
                    break
        usage_summary = None
        if usage is not None:
            usage_summary = token_usage(usage.input_tokens, usage.output_tokens, usage.total_tokens)
        return ChatResponse(
            provider=self.name,
            model=response.model,
            parts=parts,
            finish_reason=finish_reason,
            usage=usage_summary,
        )


def _parse_output_items(output: list[ResponseOutputItem]) -> tuple[list[MessagePart], bool, bool]:
    """按 API 顺序保存内容，每个正文片段只存一次；元数据保留消息边界。"""
    parts = []
    invalid_output = False
    refused = False
    call_ids = set()
    for index, item in enumerate(output):
        kind = item.type
        if kind == "reasoning":
            reasoning_item = item.model_dump(mode="json", by_alias=True, exclude_none=True)
            parts.append(MessagePart(kind="reasoning", metadata={"reasoning_item": reasoning_item}))
        elif kind == "message":
            message_fields = item.model_dump(mode="json", by_alias=True, exclude_none=True, exclude={"content"})
            contents = item.content
            if not contents:
                parts.append(MessagePart(kind="text", metadata={
                    "output_index": index, "message_fields": message_fields, "content_fields": None,
                }))
            for content in contents:
                content_type = content.type
                if content_type == "output_text":
                    text = content.text
                elif content_type == "refusal":
                    text = content.refusal
                    refused = True
                else:
                    invalid_output = True
                    continue
                content_fields = content.model_dump(mode="json", by_alias=True, exclude_none=True, exclude={"text", "refusal"})
                parts.append(MessagePart(kind="text", content=text, metadata={
                    "output_index": index, "message_fields": message_fields, "content_fields": content_fields,
                }))
        elif kind == "function_call":
            arguments = getattr(item, "arguments", None)
            parsed_arguments = None
            if isinstance(arguments, str):
                try:
                    parsed_arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    pass
            call_id = getattr(item, "call_id", None)
            name = getattr(item, "name", None)
            invalid_call = (
                not call_id
                or not name
                or call_id in call_ids
                or not isinstance(parsed_arguments, dict)
                or item.status not in {None, "completed"}
            )
            if invalid_call:
                invalid_output = True
                continue
            call_ids.add(call_id)
            fields = item.model_dump(
                mode="json", by_alias=True, exclude_none=True,
                exclude={"type", "call_id", "name", "arguments", "async_", "async"},
            )
            metadata = {"tool_call_id": call_id, "tool_name": name, "arguments": arguments, "api_fields": fields}
            if item.async_ is not None:
                metadata["async_execution"] = item.async_ is True
            parts.append(MessagePart(kind="tool_call", metadata=metadata))
        else:
            invalid_output = True
    return parts, invalid_output, refused


# 将文本、图片和 PDF 转换为接口输入内容。
def _build_message_content(message: ChatMessage) -> str | list[dict[str, Any]]:
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
