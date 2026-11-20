"""Cross-cutting runtime primitives shared by agent, tools, permissions, and utils.

These types intentionally live outside `agent/` so lower layers do not import
upward into the orchestration package.
"""

from raccoon.runtime.cancellation import (
    AgentCancelledError,
    CancellationToken,
    cancellation_context,
    current_cancellation_token,
)
from raccoon.runtime.user_input import (
    UserInputOption,
    UserInputRequest,
    user_input_request_from_tool_result,
)
from raccoon.app.payloads import AttachmentRef, CompletionInput, MessageInput, TimerInput, SubagentInput

__all__ = [
    "AgentCancelledError",
    "CancellationToken",
    "MessageInput",
    "AttachmentRef",
    "CompletionInput",
    "TimerInput",
    "SubagentInput",
    "UserInputOption",
    "UserInputRequest",
    "cancellation_context",
    "current_cancellation_token",
    "user_input_request_from_tool_result",
]
