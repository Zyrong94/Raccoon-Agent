"""provider 层共享的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
import json

MessageRole = Literal["system", "user", "assistant", "tool"]
PartKind = Literal["text", "reasoning", "tool_call", "tool_result", "image", "file"]
FinishReason = Literal[
    "stop",
    "tool_calls",
    "length",
    "content_filter",
    "error",
    "unknown",
    "interrupted",
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
class ContentPart:
    """Provider-agnostic rich content within one chat message."""

    type: Literal["text", "image", "file"]
    text: str | None = None
    media_type: str | None = None
    data_base64: str | None = None
    filename: str | None = None
    estimated_tokens: int | None = None


@dataclass(slots=True)
class MessagePart:
    """有序内容的唯一存储；写入会话时补上本地 id 和 message_id。"""

    id: str = ""
    message_id: str = ""
    kind: PartKind | str = "text"
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_tool_call(cls, call: ToolCall) -> MessagePart:
        return cls(kind="tool_call", metadata={
            "tool_call_id": call.id, "tool_name": call.name,
            "arguments": call.arguments, "async_execution": call.async_execution,
        })

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MessagePart":
        return cls(
            id=str(value["id"]),
            message_id=str(value["message_id"]),
            kind=str(value["kind"]),
            content=str(value.get("content", "")),
            metadata=dict(value.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "kind": self.kind,
            "content": self.content,
            "metadata": self.metadata,
        }


def message_text(parts: list[MessagePart]) -> str:
    groups: list[str] = []
    previous_index = None
    for part in parts:
        if part.kind != "text":
            continue
        index = part.metadata.get("output_index")
        if index is not None and index == previous_index and groups:
            groups[-1] += part.content
        else:
            groups.append(part.content)
        previous_index = index
    return "\n".join(groups)


def message_tool_calls(parts: list[MessagePart]) -> list[ToolCall]:
    calls = []
    for part in parts:
        if part.kind == "tool_call":
            arguments = part.metadata["arguments"]
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            calls.append(ToolCall(
                id=part.metadata["tool_call_id"], name=part.metadata["tool_name"],
                arguments=arguments,
                async_execution=part.metadata.get("async_execution", False),
            ))
    return calls


@dataclass(slots=True)
class ChatMessage:
    """agent 内部使用的统一消息结构。

    不同厂商的消息格式不完全一致，所以项目内部先使用自己的结构。
    provider 负责把这个结构转换成各家 SDK 需要的请求格式。
    """

    role: MessageRole
    # 用户、系统和工具结果使用 text；助手只使用有序 parts。
    text: str = ""
    content_parts: list[ContentPart] | None = None
    name: str | None = None
    tool_call_id: str | None = None
    parts: list[MessagePart] = field(default_factory=list)
    additional_tools: list[ToolDefinition] = field(default_factory=list)

    @property
    def content(self) -> str:
        return message_text(self.parts) if self.role == "assistant" else self.text

    @property
    def tool_calls(self) -> list[ToolCall]:
        return message_tool_calls(self.parts)


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


@dataclass(slots=True)
class ChatRequest:
    """发送给 provider 的统一请求结构。"""

    messages: list[ChatMessage]
    tools: list[ToolDefinition] = field(default_factory=list)
    tool_choice: ToolChoice | None = "auto"
    reasoning_effort: str | None = None


@dataclass(slots=True)
class ChatResponse:
    """provider 返回给 agent 主循环的统一响应结构。"""

    provider: str
    model: str
    parts: list[MessagePart] = field(default_factory=list)
    finish_reason: FinishReason | None = None
    usage: TokenUsage | None = None

    @property
    def content(self) -> str:
        return message_text(self.parts)

    @property
    def tool_calls(self) -> list[ToolCall]:
        return message_tool_calls(self.parts)
