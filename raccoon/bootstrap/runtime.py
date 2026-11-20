"""Assemble shared conversation runtimes and dependency lifecycles."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from raccoon.application.conversations.model_switching import ROUTING_MODEL_REF, create_routing_model, model_request_options
from raccoon.runtime.processes import ProcessManager
from raccoon.modules.agent.runtime_capabilities import AgentRuntimeCapabilities
from raccoon.modules.memory.config import load_memory_config
from raccoon.modules.memory.service import acquire_memory
from raccoon.app.session_runtime import SessionRuntime
from raccoon.config.models import ModelCatalog, ModelCatalogError, ModelProfile
from raccoon.config.settings import AppConfig, load_config
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.context.temporal import resolve_user_timezone
from raccoon.integrations.mcp.adapter import adapt_mcp_tool
from raccoon.integrations.mcp.config import load_mcp_configs
from raccoon.integrations.mcp.manager import McpManager
from raccoon.integrations.mcp.models import McpServerStatus, McpToolDescription
from raccoon.runtime.model_provider import ChatProvider
from raccoon.integrations.providers.factory import ProviderConfigError, create_provider_for_model
from raccoon.bootstrap.agent_session import new_session, open_session
from raccoon.modules.sessions.catalog import SessionCatalog
from raccoon.modules.tools.builtin import create_builtin_registry
from raccoon.modules.agent.background import BackgroundJobManager
from raccoon.modules.tools.types import Tool
from raccoon.modules.permissions.sandbox_access import SandboxAccess


class McpManagerLike(Protocol):
    """Factory-level MCP lifecycle and discovery boundary."""

    def connect_all(self) -> None: ...

    def connect_all_in_background(self) -> None: ...

    def tools(self) -> tuple[tuple[str, McpToolDescription], ...]: ...

    def statuses(self) -> tuple[McpServerStatus, ...]: ...

    def doctor(self, name: str) -> McpServerStatus | None: ...

    def reconnect(self, name: str | None = None) -> bool: ...

    def close(self) -> None: ...


class McpToolProvider:
    """Merge a stable base tool set with the manager's current MCP catalog."""

    def __init__(self, base_tools: list[Tool], manager: McpManagerLike, *, include_mcp: bool) -> None:
        self._base_tools = list(base_tools)
        self._manager = manager
        self._include_mcp = include_mcp
        self._catalog_errors: list[str] = []

    def status_text(self) -> str:
        if not self._include_mcp:
            return ""
        statuses = self._manager.statuses()
        lines = [f"MCP {status.name}: {status.state}" for status in statuses if status.state != "connected"]
        return "\n".join([*lines, *self._catalog_errors])

    def __call__(self) -> list[Tool]:
        tools = list(self._base_tools)
        self._catalog_errors = []
        if not self._include_mcp:
            return tools
        names = {tool.name for tool in tools}
        catalog = self._manager.tools()
        for server, discovered_tool in catalog:
            try:
                tool = adapt_mcp_tool(self._manager, server, discovered_tool, existing_names=names)
            except ValueError as error:
                self._catalog_errors.append(f"MCP {server}: {error}")
                continue
            tools.append(tool)
            names.add(tool.name)
        return tools


def create_runtime(
    *,
    project_root: str | Path = ".",
    data_root: str | Path | None = None,
    provider: ChatProvider | None = None,
    session_id: str | None = None,
    resume_session: bool = False,
    tools: list[Tool] | None = None,
    app_config: AppConfig | None = None,
    mcp_manager_factory: Callable[[tuple], McpManagerLike] | None = None,
    model_spec: str | None = None,
    allow_user_input: bool | None = None,
    runtime_capabilities: AgentRuntimeCapabilities | None = None,
) -> SessionRuntime:
    """组装本机入口共用的会话、工具与执行引擎。

    `data_root` 默认是 `<project_root>/.raccoon`，并传给 context/session 各组件作为
    统一数据根。
    """

    project_path = Path(project_root)
    resolved_data_root = Path(data_root) if data_root is not None else project_path / ".raccoon"
    from raccoon.config.management import ManagedConfigStore

    managed_store = ManagedConfigStore()
    managed_store.register_source(project_path, resolved_data_root)
    resolved_app_config = app_config or load_config(project_root=project_path) #加载配置
    user_timezone = resolve_user_timezone(resolved_app_config.get_config_value('user_timezone'))
    model_catalog = resolved_app_config.model_catalog() #你配置文件里 [models] 声明的所有可用模型，列成一张目录
    selected_profile: ModelProfile | None = None
    saved_model_settings = {}
    if resume_session and session_id is not None:
        saved_model_settings = SessionStore(resolved_data_root).load(session_id).metadata
        if model_spec is None and saved_model_settings.get("model_ref"):
            model_spec = saved_model_settings["model_ref"]
    if provider is None: #全项目唯一的“和模型 API 说话的人”。OpenAI 兼容、Anthropic 各有一个适配器实现，工厂按配置挑一个
        if not model_catalog.models:
            raise ValueError("模型目录为空；请配置 default_model、[providers] 和 [models]")
        selected_profile = _initial_model_profile(
            model_catalog,
            model_spec=model_spec,
        )
        try:
            provider = create_provider_for_model(resolved_app_config, selected_profile)
        except ProviderConfigError as error:
            raise ValueError(str(error)) from error
    routing_provider, routing_request_options = create_routing_model(
        resolved_app_config, model_catalog.models.get(ROUTING_MODEL_REF),
    )
    routing_timeout_seconds: float | None = None
    routing_timeout_raw = resolved_app_config.get_config_value("event_routing_timeout_seconds")
    if routing_timeout_raw is not None:
        try:
            routing_timeout_seconds = float(routing_timeout_raw)
        except (TypeError, ValueError) as error:
            raise ValueError(f"event_routing_timeout_seconds 必须是正数：{routing_timeout_raw!r}") from error
        if not (routing_timeout_seconds > 0):
            raise ValueError(f"event_routing_timeout_seconds 必须是正数：{routing_timeout_raw!r}")
    store = SessionStore(resolved_data_root) #会话事实仓库 所有对话记录按行追加写到 .raccoon/sessions/<id>.jsonl
    sandbox_access = SandboxAccess() #工具碰文件的门禁，防止 agent 跑出项目目录乱写
    background_manager = BackgroundJobManager() #管 agent 丢到后台跑的命令
    resolved_capabilities = runtime_capabilities or AgentRuntimeCapabilities.interactive()
    if allow_user_input is not None:
        resolved_capabilities = replace(
            resolved_capabilities,
            allow_user_input=allow_user_input,
        )
    process_manager = ProcessManager(log_root=resolved_data_root / "processes") #管子进程的日志和回收
    resolved_tools = (   #造内置工具：read、write、edit、shell、grep、glob
        tools
        if tools is not None
        else create_builtin_registry(
            project_path,
            include_mutation_tools=True,
            include_execution_tools=True,
            include_network_tools=True,
            access=sandbox_access,
            include_ask_user=resolved_capabilities.allow_user_input,
            include_think=resolved_capabilities.expose_think_tool,
            include_web_search=resolved_capabilities.expose_web_search_tool,
            process_manager=(
                process_manager if resolved_capabilities.enable_process_tools else None
            ),
        ).tools()
    )
    mcp_manager = (mcp_manager_factory or McpManager)(load_mcp_configs(resolved_app_config))
    try:
        mcp_manager.connect_all_in_background()
    except Exception:
        pass
    memory_service = None
    try:
        memory_config = load_memory_config(resolved_app_config, project_root=project_path, data_root=resolved_data_root)
        if memory_config is not None:
            memory_service = acquire_memory(memory_config)
        tool_provider = McpToolProvider(resolved_tools, mcp_manager, include_mcp=tools is None)
        current_tools = tool_provider()
        resolved_provider = provider

        session_options = {
            "store": store, "project_root": project_path, "data_root": resolved_data_root,
            "tools": current_tools, "sandbox_access": sandbox_access, "user_timezone": user_timezone,
        }
        if resume_session and session_id is not None:
            session = open_session(session_id=session_id, **session_options)
        else:
            session = new_session(session_id=session_id, **session_options)
        if selected_profile is not None and not resume_session:
            session.store.append_session_metadata_updated(session.data, model_ref=selected_profile.ref)
        compaction_settings = resolved_app_config.compaction_settings()
        catalog = SessionCatalog(resolved_data_root)
        context = SessionRuntime(
            session=session,
            provider=resolved_provider,
            tools=current_tools,
            tools_provider=tool_provider,
            tool_catalog_status=tool_provider.status_text,
            compaction_settings=compaction_settings,
            limits=None,
            memory_service=memory_service,
            request_options=model_request_options(selected_profile),
            routing_provider=routing_provider,
            routing_request_options=routing_request_options,
            routing_timeout_seconds=routing_timeout_seconds,
            context_window=selected_profile.context_window if selected_profile is not None else None,
            background_manager=background_manager,
            native_async_tools=(
                resolved_capabilities.enable_native_async_tools
                and bool(getattr(getattr(resolved_provider, "capabilities", None), "supports_async_tools", False))
            ),
            runtime_capabilities=resolved_capabilities,
        )
        saved_effort = saved_model_settings.get("reasoning_effort") or None
        if selected_profile is not None and saved_model_settings.get("model_ref") != selected_profile.ref:
            saved_effort = None
        if saved_effort is not None and selected_profile is not None:
            if saved_effort not in selected_profile.supported_reasoning_efforts:
                raise ModelCatalogError("会话的推理强度已不受当前模型支持，请重新选择")
            extra_body = dict(context.request_options.extra_body)
            extra_body["reasoning_effort"] = saved_effort
            context.request_options = replace(context.request_options, extra_body=extra_body)
        context.execution_root = resolved_data_root
        def mcp_status():
            """只读快照：MCP 运行状态与已发现工具，供配置页展示。"""

            catalog = mcp_manager.tools()
            return {"servers": [
                {"name": status.name, "state": status.state, "tool_count": status.tool_count,
                 "error": status.error, "tools": [tool.name for server, tool in catalog if server == status.name]}
                for status in mcp_manager.statuses()
            ]}

        context.catalog = catalog
        context.model_catalog = model_catalog
        context.model_ref = selected_profile.ref if selected_profile else f"{provider.name}/{provider.model}"
        context.project_path = project_path.resolve()
        context.app_config = resolved_app_config
        context.on_shutdown = lambda: _shutdown_runtime(
            mcp_manager=mcp_manager, background_manager=background_manager,
            process_manager=process_manager, memory_service=memory_service,
        )
        context.mcp_status = mcp_status
        return context
    except BaseException:
        _shutdown_runtime(mcp_manager=mcp_manager, background_manager=background_manager,
                          process_manager=process_manager, memory_service=memory_service)
        raise


def _shutdown_runtime(*, mcp_manager, background_manager, process_manager, memory_service=None) -> None:
    """按本地进程、后台线程、外部连接的顺序清理运行时。"""

    try:
        process_manager.shutdown()
        background_manager.shutdown()
        mcp_manager.close()
    finally:
        if memory_service is not None:
            memory_service.close()


def _initial_model_profile(
    catalog: ModelCatalog,
    *,
    model_spec: str | None,
) -> ModelProfile:
    selected_ref = model_spec
    if selected_ref is None:
        selected_ref = catalog.default_ref
    if selected_ref is None:
        raise ModelCatalogError("请在全局模型目录中设置默认模型，或为会话选择模型")
    if selected_ref not in catalog.models:
        raise ModelCatalogError(f"未配置模型：{selected_ref}，请重新选择模型")
    return catalog.models[selected_ref]
