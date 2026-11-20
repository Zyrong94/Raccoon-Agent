"""Construct and connect a session execution container."""

from __future__ import annotations

from pathlib import Path
from raccoon.config.subagents_config import SubagentConfig, discover_subagent_configs
from raccoon.modules.sessions.thread_data import ThreadDataState, copy_thread_data, default_thread_data
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.sessions.models import SessionData
from raccoon.modules.permissions.grants import FilePermissionGrantStore
from raccoon.modules.permissions.manager import PermissionManager
from raccoon.modules.permissions.policy import DefaultPermissionPolicy
from raccoon.modules.tools.session_registry import create_session_tool_registry
from raccoon.modules.context.temporal import resolve_user_timezone
from raccoon.modules.tools.types import Tool
from raccoon.modules.permissions.sandbox_access import SandboxAccess
from raccoon.modules.skills.discovery import discover_all_skills
from raccoon.modules.skills.models import SkillCatalog
from raccoon.modules.agent.session import AgentSession
from raccoon.bootstrap.project_instructions import read_agents_md

from raccoon.config.settings import load_config


def new_session(
    *, store: SessionStore, session_id: str | None = None, title: str | None = None, **environment
) -> AgentSession:
    data = store.create(session_id=session_id, title=title)
    session = prepare_session(store=store, data=data, **environment)
    store.append_session_metadata_updated(data, thread_data=dict(session.thread_data))
    return session


def open_session(*, store: SessionStore, session_id: str, **environment) -> AgentSession:
    data = store.load(session_id)
    session = prepare_session(store=store, data=data, **environment)
    session.append_interrupted_tool_results()
    return session


def prepare_session(
    *,
    store: SessionStore,
    data: SessionData,
    project_root: str | Path | None = None,
    agents_md: str = "",
    skill_catalog: SkillCatalog | None = None,
    tools: list[Tool] | None = None,
    permission_manager: PermissionManager | None = None,
    sandbox_access: SandboxAccess | None = None,
    subagent_config: SubagentConfig | None = None,
    data_root: str | Path | None = None,
    thread_data: ThreadDataState | None = None,
    user_timezone: str | None = None,
    include_session_tools: bool = True,
) -> "AgentSession":
    """准备当前执行依赖，不恢复已经删除的旧运行状态。"""
    data_root = Path(data_root) if data_root is not None else store.root
    if project_root is not None:
        if user_timezone is None:
            user_timezone = load_config(project_root=project_root).get_config_value("user_timezone")
        agents_md = read_agents_md(project_root)
        skill_catalog = discover_all_skills(project_root)
        if permission_manager is None:
            permission_manager = PermissionManager(
                policy=DefaultPermissionPolicy(project_root),
                grants=FilePermissionGrantStore(data_root / "permissions.json"),
            )

    user_timezone = resolve_user_timezone(user_timezone)
    config = subagent_config
    available_catalog = (skill_catalog or SkillCatalog()).resolved()
    catalog = available_catalog
    if config is not None and config.skills is not None:
        catalog = SkillCatalog(
            skills=[skill for skill in available_catalog.skills if skill.name in config.skills]
        )
    registry = create_session_tool_registry(
        data=data,
        tools=tools,
        user_timezone=user_timezone,
        store=store,
        skill_catalog=catalog,
        include_session_tools=include_session_tools,
    )
    saved_directories = thread_data if thread_data is not None else data.metadata.get("thread_data")
    directories = (
        copy_thread_data(saved_directories)
        if saved_directories is not None
        else default_thread_data(
            store_root=store.root,
            session_id=data.session_id,
            project_root=permission_manager.policy.project_root if permission_manager is not None else None,
        )
    )
    if permission_manager is not None:
        directories["workspace_path"] = str(permission_manager.policy.project_root.resolve())
    session = AgentSession(
        data=data,
        store=store,
        tool_registry=registry,
        allowed_tool_names=(
            set(config.tools if config.tools is not None else [*registry.names(), "tool_search"])
            - {"delegate"}
            if config is not None
            else None
        ),
        allow_background_execution=config is None,
        agents_md=agents_md,
        skill_catalog=catalog,
        available_skill_catalog=available_catalog,
        subagent_configs=discover_subagent_configs(data_root=data_root or store.root) if config is None else {},
        subagent_config=subagent_config,
        data_root=Path(data_root) if data_root is not None else store.root,
        thread_data=directories,
        permission_manager=permission_manager,
        sandbox_access=sandbox_access or SandboxAccess(),
        user_timezone=user_timezone,
        mode=permission_manager.mode.value if permission_manager is not None else "default",
    )
    if config is None and not include_session_tools:
        session.allowed_tool_names = {tool.name for tool in tools or []}
    session._sync_sandbox_access_with_mode()
    return session
