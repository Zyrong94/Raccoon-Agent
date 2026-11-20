"""provider 层共享的数据结构。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

MessageRole = Literal["system", "user", "assistant", "tool"]
FinishReason = Literal[
    "stop",
    "tool_calls",
    "length",
    "content_filter",
    "error",
    "unknown",
    "tool_round_limit",
    "waiting_for_user_input",
]
ToolChoiceMode = Literal["auto", "none", "required"]


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """provider 静态能力。

    这些字段描述“请求应该怎么发”和“上层可以期待什么能力”，避免 agent loop
    为不同厂商写分支。真实 capability discovery 还没做之前，先由适配器与配置提供。
    """

    supports_tools: bool = True
    supports_parallel_tool_calls: bool = False
    supports_vision: bool = False
    supports_pdf_input: bool = False
    supports_async_tools: bool = False
    supports_tool_additions: bool = False


@dataclass(slots=True)
class TokenUsage:
    """provider 返回的 token 用量。

    不同 Responses 服务返回字段可能不完整，所以这里允许局部为空。
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(slots=True)
class ProviderDiagnostics:
    """不会进入模型可见正文的 provider 诊断信息。"""

    reasoning: str | None = None
    raw_finish_reason: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ContentPart:
    """Provider-agnostic rich content within one chat message."""

    type: Literal["text", "image", "file"]
    text: str | None = None
    media_type: str | None = None
    data_base64: str | None = None
    filename: str | None = None
    estimated_tokens: int | None = None


@dataclass(slots=True)
class ChatMessage:
    """agent 内部使用的统一消息结构。

    不同厂商的消息格式不完全一致，所以项目内部先使用自己的结构。
    provider 负责把这个结构转换成各家 SDK 需要的请求格式。
    """

    role: MessageRole
    content: str
    content_parts: list[ContentPart] | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Opaque protocol data; only the originating provider may replay it.
    provider_state: dict[str, Any] = field(default_factory=dict)
    additional_tools: list[ToolDefinition] = field(default_factory=list)


@dataclass(slots=True)
class ToolDefinition:
    """模型可调用工具的统一描述。

    `parameters` 使用 JSON Schema 风格，方便转换到 OpenAI tool calling、
    Anthropic tool use，以及后续其他 provider 的工具格式。
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    async_execution: bool = False


@dataclass(slots=True)
class ToolCall:
    """模型返回的一次工具调用请求。"""

    id: str
    name: str
    arguments: dict[str, Any] | str
    async_execution: bool = False


@dataclass(frozen=True, slots=True)
class ToolChoiceFunction:
    """强制模型调用指定工具。

    Responses wire format 由适配器生成；内部只保留工具名，让 agent 不需要
    知道 provider 原始 schema。
    """

    name: str


ToolChoice = ToolChoiceMode | ToolChoiceFunction


@dataclass(frozen=True, slots=True)
class MainRequestOptions:
    """Selected model options shared by agent and research requests."""

    temperature: float | None = None
    max_tokens: int | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra_body", deepcopy(dict(self.extra_body)))

    def as_chat_request_kwargs(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "extra_body": deepcopy(self.extra_body),
        }


@dataclass(slots=True)
class ChatRequest:
    """发送给 provider 的统一请求结构。"""

    messages: list[ChatMessage]
    tools: list[ToolDefinition] = field(default_factory=list)
    tool_choice: ToolChoice | None = "auto"
    temperature: float | None = None
    max_tokens: int | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChatResponse:
    """provider 返回给 agent 主循环的统一响应结构。"""

    provider: str
    model: str
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: FinishReason | None = None
    usage: TokenUsage | None = None
    diagnostics: ProviderDiagnostics = field(default_factory=ProviderDiagnostics)
    raw: Any | None = None
    provider_state: dict[str, Any] = field(default_factory=dict)
