"""Agent 运行能力配置。"""

from __future__ import annotations

from dataclasses import dataclass

PLANNING_TOOL_NAMES = frozenset(
    {"task_create", "task_update", "task_revise", "task_list"}
)


@dataclass(frozen=True, slots=True)
class AgentRuntimeCapabilities:
    """由 bootstrap/runtime 统一装配的运行能力。"""

    allow_user_input: bool = True
    enable_delegate_tool: bool = True
    expose_planning_tools: bool = True
    expose_think_tool: bool = True
    expose_web_search_tool: bool = True
    enable_process_tools: bool = True
    enable_native_async_tools: bool = True

    @classmethod
    def interactive(cls) -> "AgentRuntimeCapabilities":
        return cls()
