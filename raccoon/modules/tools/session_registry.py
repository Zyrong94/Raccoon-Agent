"""统一组装会话工具目录。"""

from __future__ import annotations


from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.sessions.models import SessionData
from raccoon.modules.planning.service import TaskPlanService
from raccoon.modules.skills.models import SkillCatalog
from raccoon.modules.tools.load_skill import create_load_skill_tool
from raccoon.modules.tools.session_status import create_session_status_tool
from raccoon.modules.context.temporal import resolve_user_timezone
from raccoon.modules.tools.registry import ToolRegistry
from raccoon.modules.tools.task_create import create_task_create_tool
from raccoon.modules.tools.task_list import create_task_list_tool
from raccoon.modules.tools.task_revise import create_task_revise_tool
from raccoon.modules.tools.task_update import create_task_update_tool
from raccoon.modules.tools.types import Tool
from raccoon.modules.tools.background import create_background_cancel_tool, create_background_status_tool
from raccoon.runtime.background import BackgroundJobManager


def build_tool_registry(
    *,
    data: SessionData,
    store: SessionStore,
    tools: list[Tool] | None = None,
    skill_catalog: SkillCatalog | None = None,
    user_timezone: str | None = None,
    include_session_tools: bool = True,
    background_manager: BackgroundJobManager | None = None,
) -> ToolRegistry:
    """将传入工具与会话专属工具组装到一个注册表。

    传入工具参与外部目录刷新，会话专属工具单独注册以保留其来源。
    授权由 AgentSession 检查。
    """

    supplied_tools = tools or []
    registry = ToolRegistry(supplied_tools)
    if include_session_tools:
        reserved_names = {
            "task_create",
            "task_update",
            "task_revise",
            "task_list",
            "load_skill",
            "session_status",
        }
        conflicting = next((tool.name for tool in supplied_tools if tool.name in reserved_names), None)
        if conflicting is not None:
            raise ValueError(f"{conflicting} is a reserved session-scoped tool name")
        registry.register(create_session_status_tool(data.session_id, resolve_user_timezone(user_timezone)))
        service = TaskPlanService(store=store, data=data)
        for tool in (
            create_task_create_tool(service),
            create_task_update_tool(service),
            create_task_revise_tool(service),
            create_task_list_tool(service),
            create_load_skill_tool(skill_catalog or SkillCatalog(), store, data),
        ):
            registry.register(tool)
    if background_manager is not None:
        if registry.get("background_status") is None:
            registry.register(create_background_status_tool(background_manager, session_id=data.session_id))
        if registry.get("background_cancel") is None:
            registry.register(create_background_cancel_tool(background_manager, session_id=data.session_id))
    return registry
