"""Create and open restricted subagent sessions for the shared event application."""

from __future__ import annotations

from raccoon.bootstrap.agent_session import new_session, open_session

from dataclasses import asdict, dataclass, field, replace
from copy import copy, deepcopy
from pathlib import Path
from typing import Any

from raccoon.modules.agent.session import AgentSession
from raccoon.modules.sessions.thread_data import ThreadDataState, copy_thread_data, default_thread_data
from raccoon.modules.collaboration.worktree import Worktree, WorktreeDiff, WorktreeManager
from raccoon.config.subagents_config import SubagentConfig, remove_obsolete_loop_limits
from raccoon.modules.sessions.identity import new_session_id
from raccoon.modules.permissions.delegation import DelegatedPermissionManager
from raccoon.modules.permissions.manager import PermissionManager
from raccoon.modules.permissions.policy import DefaultPermissionPolicy
from raccoon.modules.tools.types import Tool
from raccoon.modules.permissions.sandbox_access import SandboxAccess, SandboxAccessMode

@dataclass(slots=True)
class SubagentRequest:
    agent_name: str
    task: str
    parent_summary: str | None = None
    isolate_worktree: bool = False


@dataclass(slots=True)
class SubagentResult:
    ok: bool
    agent_name: str
    subagent_session_id: str
    summary: str
    files_changed: list[str] = field(default_factory=list)
    error: str | None = None
    worktree_path: str | None = None
    worktree_branch: str | None = None
    diff_summary: str | None = None

    def to_data(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "agent_name": self.agent_name,
            "subagent_session_id": self.subagent_session_id,
            "summary": self.summary,
            "files_changed": list(self.files_changed),
            "error": self.error,
            "worktree_path": self.worktree_path,
            "worktree_branch": self.worktree_branch,
            "diff_summary": self.diff_summary,
        }


def _project_root(parent: AgentSession) -> Path | None:
    if parent.permission_manager is not None:
        return Path(parent.permission_manager.policy.project_root).resolve()
    path = parent.thread_data.get('workspace_path')
    return Path(path).resolve() if path else None


def create_subagent_session(parent, request: SubagentRequest) -> AgentSession:
    """Create once and persist the exact role and execution boundary for later sends."""
    config = parent.subagent_configs.get(request.agent_name)
    if config is None:
        raise ValueError('Unknown or unavailable subagent')
    worktree = None
    if request.isolate_worktree:
        if _project_root(parent) is None or not WorktreeManager(_project_root(parent)).available():
            raise ValueError('worktree_unavailable')
        identity = new_session_id()
        worktree = WorktreeManager(_project_root(parent)).create(identity)
        subagent = _create_isolated_subagent(parent, request, config=config, worktree=worktree, session_id=identity)
    else:
        subagent = _create_inline_subagent(parent, request, config=config)
    registered = set(subagent.tool_registry.names()) | {"tool_search"}
    allowed = subagent.allowed_tool_names if subagent.allowed_tool_names is not None else registered
    frozen = replace(config, tools=tuple(sorted(registered & allowed)))
    subagent.subagent_config = frozen
    subagent.allowed_tool_names = set(frozen.tools)
    subagent.store.append_session_metadata_updated(subagent.data, subagent_snapshot={
        'config': asdict(frozen), 'agents_md': subagent.agents_md,
        'project_root': str(_project_root(parent)) if _project_root(parent) else None,
        'sandbox_mode': subagent.sandbox_access.mode.value,
        'permission_mode': subagent.mode,
        'permissions': subagent.permission_manager.snapshot(),
        'user_timezone': subagent.user_timezone,
        'tool_definitions': [asdict(tool.definition) for tool in subagent.tool_registry.tools(supplied_only=True)],
        'tool_exposures': {tool.name: tool.exposure for tool in subagent.tool_registry.tools(supplied_only=True)},
    })
    return subagent


def load_subagent_session(parent, subagent_session_id: str) -> AgentSession:
    metadata = parent.store.load(subagent_session_id).metadata
    if metadata.get('parent_session_id') != parent.session_id:
        raise ValueError('subagent does not belong to this parent')
    if metadata.get('subagent_execution_scope') != parent.subagent_scope:
        raise ValueError('子会话授权作用域不匹配')
    snapshot = metadata.get('subagent_snapshot')
    if not isinstance(snapshot, dict):
        raise ValueError('subagent has no collaboration snapshot')
    config_values = dict(snapshot['config'])
    remove_obsolete_loop_limits(config_values)
    # Old session snapshots stored the redundant inheritance field.
    if config_values.pop('model', None) not in (None, 'inherit'):
        raise ValueError('saved subagent model override is unsupported')
    config = SubagentConfig(**config_values)
    access = SandboxAccess(mode=SandboxAccessMode(snapshot['sandbox_mode']))
    root = metadata.get('worktree_path') or snapshot.get('project_root')
    if metadata.get('worktree_path'):
        worktree_path = Path(root)
        if not worktree_path.is_dir() or not (worktree_path / '.git').exists():
            raise ValueError('saved worktree is missing; refusing parent-root fallback')
        if not WorktreeManager(Path(snapshot['project_root'])).validate_saved(worktree_path, metadata['worktree_branch']):
            raise ValueError('saved worktree identity or branch changed; refusing execution')
        tools = _worktree_subagent_tools(worktree_path, config=config, access=access)
        permissions = DelegatedPermissionManager.restore(snapshot.get('permissions'), worktree_path)
    else:
        if _project_root(parent) is not None and str(_project_root(parent)) != snapshot.get('project_root'):
            raise ValueError('saved subagent project root differs from the current runtime')
        reserved = {'delegate', 'session_status', 'task_create', 'task_update', 'task_revise', 'task_list', 'load_skill', 'tool_search'}
        tools = [tool for tool in parent.tool_registry.tools(supplied_only=True) if tool.name in config.tools and tool.name not in reserved]
        permissions = DelegatedPermissionManager.restore(snapshot.get('permissions'), root or Path.cwd())
    # A removed custom/MCP tool must not silently broaden or change the surface.
    available = {tool.name for tool in tools} | {'session_status', 'load_skill', 'task_create', 'task_update', 'task_revise', 'task_list', 'tool_search'}
    if set(config.tools) - available:
        raise ValueError('saved subagent tools are unavailable in this runtime')
    tools = _freeze_inline_tools(tools, root=root, access=access) if not metadata.get('worktree_path') else tools
    saved_exposures = snapshot.get('tool_exposures')
    if saved_exposures is not None and saved_exposures != {tool.name: tool.exposure for tool in tools}:
        raise ValueError('saved subagent tool exposure differs from the current runtime')
    if {tool.name: asdict(tool.definition) for tool in tools} != {tool['name']: tool for tool in snapshot['tool_definitions']}:
        raise ValueError('saved subagent tool definitions differ from the current runtime')
    subagent = open_session(
        store=parent.store, session_id=subagent_session_id, subagent_config=config,
        agents_md=snapshot['agents_md'], skill_catalog=parent.available_skill_catalog,
        tools=tools, permission_manager=permissions, sandbox_access=access,
        data_root=(parent.data_root or parent.store.root), user_timezone=snapshot['user_timezone'],
    )
    if metadata.get('worktree_path'):
        subagent.require_prewrite_review = False
    return subagent


def validate_subagent_boundary(subagent: AgentSession) -> None:
    metadata = subagent.data.metadata
    if metadata.get('worktree_path'):
        project_root = metadata['subagent_snapshot'].get('project_root')
        if not project_root:
            raise ValueError('saved worktree has no original project root; refusing execution')
        manager = WorktreeManager(Path(project_root))
        if not manager.validate_saved(Path(metadata['worktree_path']), metadata['worktree_branch']):
            raise ValueError('saved worktree is missing or its identity/branch changed; refusing execution')


def build_subagent_result(subagent: AgentSession, *, ok: bool, summary: str, error: str | None = None) -> SubagentResult:
    metadata = subagent.data.metadata
    result = SubagentResult(ok, subagent.subagent_config.name, subagent.session_id, summary, error=error)
    if metadata.get('worktree_path'):
        worktree = Worktree(name=subagent.session_id, path=Path(metadata['worktree_path']), branch=metadata['worktree_branch'], base_ref='HEAD')
        diff = WorktreeManager(Path(subagent.data.metadata['subagent_snapshot']['project_root'])).diff(worktree)
        result.worktree_path, result.worktree_branch = str(worktree.path), worktree.branch
        result.files_changed, result.diff_summary = diff.files_changed, diff.render()
        result.summary = _compose_isolated_summary(summary, worktree=worktree, diff=diff)
    return result


def _subagent_thread_data(parent) -> ThreadDataState:
    if parent.thread_data is not None:
        return copy_thread_data(parent.thread_data)
    return default_thread_data(
        store_root=parent.store.root, session_id=parent.session_id,
        project_root=_project_root(parent),
    )


def _create_inline_subagent(parent, request: SubagentRequest, *, config: SubagentConfig) -> AgentSession:
    session_id = new_session_id()
    access = SandboxAccess(mode=parent.sandbox_access.mode)
    permissions = _subagent_permissions(parent, _project_root(parent) or Path.cwd())
    supplied_tools = []
    for tool in parent.tool_registry.tools(supplied_only=True):
        if tool.name in {'delegate', 'session_status'}:
            continue
        if config.tools is None or tool.name in config.tools:
            supplied_tools.append(tool)
    subagent = new_session(
        user_timezone=parent.user_timezone,
        store=parent.store,
        session_id=session_id,
        agents_md=parent.agents_md,
        skill_catalog=parent.available_skill_catalog,
        tools=_freeze_inline_tools(supplied_tools, root=_project_root(parent), access=access),
        permission_manager=permissions,
        sandbox_access=access,
        subagent_config=config,
        data_root=(parent.data_root or parent.store.root),
        thread_data=_subagent_thread_data(parent),
    )
    subagent.store.append_session_metadata_updated(subagent.data,
        parent_session_id=parent.session_id,
        delegate_role=config.name,
        delegate_task=request.task,
        delegate_agent_name=request.agent_name,
    )
    return subagent


def _freeze_inline_tools(tools, *, root, access):
    if root is None:
        return [replace(tool, definition=deepcopy(tool.definition), permission=copy(tool.permission)) for tool in tools]
    from raccoon.modules.tools.builtin import create_builtin_tools
    builtin_tools = create_builtin_tools(
        root,
        include_mutation_tools=True,
        include_execution_tools=True,
        include_network_tools=True,
        access=access,
    )
    builtins_by_name = {tool.name: tool for tool in builtin_tools}
    rebound = []
    for tool in tools:
        builtin = builtins_by_name.get(tool.name)
        if getattr(tool.executor, '__module__', '').startswith('raccoon.modules.tools.') and builtin is not None:
            tool = builtin
        rebound.append(tool)
    return [replace(tool, definition=deepcopy(tool.definition), permission=copy(tool.permission)) for tool in rebound]


def _create_isolated_subagent(
    parent,
    request: SubagentRequest,
    *,
    config: SubagentConfig,
    worktree: Worktree,
    session_id: str,
) -> AgentSession:
    """Create a subagent session whose permissions/tools are rooted at the worktree.

    The subagent gets its own ``PermissionManager`` (or a private clone of the
    parent's) whose ``project_root`` is the worktree path, so every path,
    shell, and git decision is evaluated against the isolated tree instead of
    the parent working directory.
    """

    permission_manager = _subagent_permissions(parent, worktree.path)
    # PROJECT sandbox confines file tools to the worktree; permissions are
    # evaluated separately against the frozen parent boundary.
    sandbox_access = SandboxAccess(mode=SandboxAccessMode.PROJECT)
    subagent = new_session(
        user_timezone=parent.user_timezone,
        store=parent.store,
        session_id=session_id,
        agents_md=parent.agents_md,
        skill_catalog=parent.available_skill_catalog,
        tools=_worktree_subagent_tools(worktree.path, config=config, access=sandbox_access),
        permission_manager=permission_manager,
        sandbox_access=sandbox_access,
        subagent_config=config,
        data_root=(parent.data_root or parent.store.root),
        thread_data={**_subagent_thread_data(parent), "workspace_path": str(worktree.path.resolve())},
    )
    # Background isolated coder has no interactive user, so per-write review
    # confirmations would deadlock the job.  The worktree diff is reviewed by the
    # parent instead, so disable the pausing prewrite-review path here.
    subagent.require_prewrite_review = False
    subagent.store.append_session_metadata_updated(subagent.data,
        parent_session_id=parent.session_id,
        delegate_role=config.name,
        delegate_task=request.task,
        delegate_agent_name=request.agent_name,
        worktree_path=str(worktree.path),
        worktree_branch=worktree.branch,
    )
    return subagent


def _subagent_permissions(parent, root) -> PermissionManager:
    """Keep the parent's frozen boundary, mapping worktree paths back to its project."""
    permissions = parent.permission_manager or PermissionManager(
        policy=DefaultPermissionPolicy(_project_root(parent) or root))
    return DelegatedPermissionManager(permissions, Path(root))


def _worktree_subagent_tools(
    root,
    *,
    config: SubagentConfig,
    access: SandboxAccess,
) -> list[Tool]:
    """Build fresh tools rooted at the worktree for the subagent's role.

    Tools are rebuilt (not reused from the parent) so their internal path
    sandboxes point at the worktree, not the parent cwd.  The session registry
    excludes session-reserved tool names when passing into ``AgentSession``.
    """

    from raccoon.modules.tools.builtin import create_builtin_tools

    builtin_tools = create_builtin_tools(
        root,
        include_mutation_tools=True,
        include_execution_tools=True,
        include_network_tools=True,
        access=access,
    )
    allowed = config.tools
    tools = [tool for tool in builtin_tools if (allowed is None or tool.name in allowed) and tool.name != "delegate"]
    return tools


def build_subagent_prompt(
    parent,
    request: SubagentRequest,
    *,
    config: SubagentConfig,
    worktree: Worktree | None = None,
) -> str:
    summary = request.parent_summary.strip() if request.parent_summary else "(none provided)"
    if worktree is not None:
        root = str(worktree.path)
        isolation = (
            "You are running inside an ISOLATED git worktree. All edits stay on branch "
            f"{worktree.branch} and never touch the parent working tree. Implement the task, "
            "then summarize what you changed. Do not attempt to merge or push.\n"
        )
    else:
        root = str(_project_root(parent)) if _project_root(parent) is not None else "(current project root)"
        isolation = ""
    report_instruction = "Follow your SOUL instructions for the final response; report missing evidence and blockers.\n\n"
    return (
        f"You are a Raccoon subagent with role: {config.name}.\n"
        f"Role scope: {config.description}\n"
        f"Project root: {root}\n"
        f"{isolation}"
        "Do not call delegate or spawn nested subagents.\n"
        "Your permissions are fixed at delegation. Operations requiring human approval are denied; "
        "report the limitation to the parent instead of retrying or requesting escalation.\n"
        f"{report_instruction}"
        f"Parent summary:\n{summary}\n\n"
        f"Task:\n{request.task}"
    )


def _compose_isolated_summary(content: str, *, worktree: Worktree, diff: "WorktreeDiff") -> str:
    parts = [
        content,
        "",
        "--- isolated worktree ---",
        f"path: {worktree.path}",
        f"branch: {worktree.branch}",
        "diff:",
        diff.render(),
    ]
    return "\n".join(parts)


