"""上下文 token 预算的集中估算。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from raccoon.modules.context.budget_defaults import DEFAULT_CONTEXT_WINDOW, DEFAULT_OUTPUT_RESERVE
from raccoon.runtime.model_types import ChatMessage, ToolDefinition


# 与 Pi 的切点估算一致：每张图片按 4800 字符 / 4 近似。
IMAGE_INPUT_TOKEN_ESTIMATE = 1_200


@dataclass(frozen=True, slots=True)
class ContextBudget:
    context_window: int
    output_reserve: int
    input_capacity: int
    fixed_tokens: int
    history_tokens: int
    input_tokens: int
    source: Literal["configured", "assumed"]


def estimate_text_tokens(text: str) -> int:
    """第一版使用字符数近似 token。

    这里有意不绑定具体 tokenizer，避免 context 层过早依赖 provider。
    """

    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def build_context_budget(
    *,
    messages: list[ChatMessage],
    tools: list[ToolDefinition],
    context_window: int | None,
    output_reserve: int = DEFAULT_OUTPUT_RESERVE,
    history_overhead_tokens: int = 0,
) -> ContextBudget:
    if isinstance(history_overhead_tokens, bool) or not isinstance(history_overhead_tokens, int) or history_overhead_tokens < 0:
        raise ValueError("history overhead must be a non-negative integer")
    resolved_window = DEFAULT_CONTEXT_WINDOW if context_window is None else context_window
    if resolved_window <= 0 or output_reserve < 0:
        raise ValueError("context window must be positive and output reserve must be non-negative")

    input_capacity = resolved_window - output_reserve
    if input_capacity <= 0:
        raise ValueError("output reserve must be smaller than usable context window")

    # Compaction boundaries retain each tool's latest definition and can fold older versions.
    latest_additions = {
        tool.name: tool for message in messages for tool in message.additional_tools
    }
    active_schema_tokens = _estimate_tool_definition_tokens(list(latest_additions.values()))
    fixed_tokens = sum(_estimate_chat_message_tokens(message) for message in messages if message.role == "system" and not message.additional_tools)
    fixed_tokens += _estimate_tool_definition_tokens(tools)
    fixed_tokens += active_schema_tokens
    history_tokens = sum(_estimate_chat_message_tokens(message) for message in messages if message.role != "system" or message.additional_tools)
    history_tokens -= active_schema_tokens
    history_tokens += history_overhead_tokens
    return ContextBudget(
        context_window=resolved_window,
        output_reserve=output_reserve,
        input_capacity=input_capacity,
        fixed_tokens=fixed_tokens,
        history_tokens=history_tokens,
        input_tokens=fixed_tokens + history_tokens,
        source="configured" if context_window is not None else "assumed",
    )


def _estimate_chat_message_tokens(message: ChatMessage) -> int:
    tokens = estimate_text_tokens(message.content)
    tokens += _estimate_tool_definition_tokens(message.additional_tools)
    tokens += estimate_text_tokens(message.name or "")
    tokens += estimate_text_tokens(message.tool_call_id or "")
    tokens += sum(
        estimate_text_tokens(call.name + json.dumps(call.arguments, ensure_ascii=False, sort_keys=True))
        for call in message.tool_calls
    )
    tokens += sum(
        IMAGE_INPUT_TOKEN_ESTIMATE
        for part in message.content_parts or []
        if part.type == "image"
    )
    tokens += sum(part.estimated_tokens or 4096 for part in message.content_parts or [] if part.type == "file")
    return tokens


def _estimate_tool_definition_tokens(tools: list[ToolDefinition]) -> int:
    return sum(
        estimate_text_tokens(
            json.dumps(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        for tool in tools
    )
