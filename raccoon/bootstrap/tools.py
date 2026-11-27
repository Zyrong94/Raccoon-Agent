"""组装依赖当前会话与运行服务的工具；主循环不维护工具名称名单。"""
from __future__ import annotations

from raccoon.modules.tools.background import create_wait_for_tasks_tool
from raccoon.modules.tools.delegate import create_delegate_tool
from raccoon.modules.tools.search.discovery import create_tool_search


def register_runtime_tools(loop) -> None:
    if loop.background_manager is not None and loop.session.allow_background_execution:
        loop.session.tool_registry.unregister("wait_for_tasks")
        loop.session.tool_registry.register(create_wait_for_tasks_tool(loop._wait_for_tasks))
    _ensure_delegate_tool(loop)
    registry = loop.session.tool_registry
    def loaded_for_search():
        definitions = loop.session.loaded_tool_definitions()
        for definition in loop.session.pending_tool_definitions:
            definitions[definition.name] = definition
        return definitions

    registry.unregister("tool_search")
    registry.register(create_tool_search(
        lambda: [definition for definition in loop._discoverable_tool_definitions()
                 if registry.get(definition.name).exposure == "deferred"],
        loaded_for_search,
        loop.session.queue_tool_definitions,
        catalog_status=loop.tool_catalog_status,
    ))


def _ensure_delegate_tool(loop) -> None:
    """Bind one parent runtime; subagent execution reuses its conversation service."""
    session = loop.session
    if not loop.enable_delegate_tool or session.subagent_config is not None:
        return
    if session.allowed_tool_names is not None and "delegate" not in session.allowed_tool_names:
        return
    if session.tool_registry.get('delegate') is not None:
        return
    if loop.application is None:
        return
    parent = loop.application.get_loaded_session(loop.application.session_key(loop))
    if parent is None:
        return
    session.tool_registry.register(create_delegate_tool(parent))
