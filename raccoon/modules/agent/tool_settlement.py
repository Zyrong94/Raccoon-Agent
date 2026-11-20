"""工具调用结果的持久化收尾。"""

from __future__ import annotations

from dataclasses import dataclass

from raccoon.modules.agent.session import AgentSession
from raccoon.runtime.model_types import ToolCall
from raccoon.modules.tools.types import ToolResult, make_error_result


@dataclass(frozen=True, slots=True)
class SettledToolCall:
    tool_call: ToolCall
    result: ToolResult


def append_interrupted_tail(session: AgentSession) -> list[SettledToolCall]:
    calls = session.append_interrupted_tool_results()
    return [SettledToolCall(tool_call=call, result=interrupted_result(call)) for call in calls]


def interrupted_result(tool_call: ToolCall) -> ToolResult:
    return make_error_result(
        tool_call.name,
        "工具执行被用户中断；结果未知，操作可能尚未执行、部分执行，或已在后台继续。",
        interrupted=True,
        execution_outcome="unknown",
    )
