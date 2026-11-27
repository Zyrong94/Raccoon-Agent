"""模型 provider 抽象和实现入口。"""

from raccoon.runtime.model_provider import ChatProvider
from raccoon.integrations.providers.factory import ProviderConfigError, create_provider_for_model
from raccoon.integrations.providers.openai_responses import OpenAIResponsesProvider
from raccoon.runtime.model_types import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ProviderCapabilities,
    TokenUsage,
    ToolChoiceFunction,
    ToolCall,
    ToolDefinition,
)

__all__ = [
    "ChatMessage",
    "ChatProvider",
    "ChatRequest",
    "ChatResponse",
    "OpenAIResponsesProvider",
    "ProviderCapabilities",
    "ProviderConfigError",
    "TokenUsage",
    "ToolChoiceFunction",
    "ToolCall",
    "ToolDefinition",
    "create_provider_for_model",
]
