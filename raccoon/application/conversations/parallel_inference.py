"""Independent read-only inference branches for Web conversations.

A ``user.message`` routed as ``parallel`` is answered by its own child
``AgentSession``.  The child never shares the main runner, its history, its
tools, or its cancellation token.  Results are persisted in the parent session
metadata under ``parallel_branches`` and exposed read-only through ``replies()``.
"""

from __future__ import annotations

from raccoon.bootstrap.agent_session import new_session

from dataclasses import replace

import asyncio
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from contextlib import nullcontext
from typing import Any

from raccoon.modules.agent.runtime_capabilities import AgentRuntimeCapabilities
from raccoon.modules.agent.session import AgentSession
from raccoon.app.session_runtime import SessionRuntime
from raccoon.application.workspaces.catalog import catalog_record
from raccoon.modules.sessions.identity import new_session_id
from raccoon.modules.sessions.models import utc_now_iso
from raccoon.modules.sessions.store import SessionStore
from raccoon.app.conversations import ActiveRun
from raccoon.modules.permissions.delegation import DelegatedPermissionManager
from raccoon.modules.permissions.manager import PermissionManager
from raccoon.modules.permissions.policy import DefaultPermissionPolicy
from raccoon.modules.permissions.types import PermissionMode
from raccoon.runtime.model_provider import AuxiliaryModels, ChatProvider
from raccoon.modules.tools.builtin import create_builtin_tools
from raccoon.modules.tools.types import Tool
from raccoon.modules.permissions.sandbox_access import SandboxAccess, SandboxAccessMode

# First version exposes an explicit read-only surface.  Writes, commands,
# planning, UI actions, delegation, process control and skill loading are not
# part of any branch, even when the parent session runs in bypass mode.
# ``diagnostics`` is deliberately absent: it takes an arbitrary command and runs
# it through a shell, so it is an execution tool despite the name.
PARALLEL_TOOL_ALLOWLIST = frozenset({
    "ls", "view", "grep", "glob", "tree", "git_status", "git_diff", "git_log",
    "read_multi", "view_image", "think",
})

# Network tools are added only when the parent runner actually exposes them.
PARALLEL_NETWORK_TOOLS = frozenset({"web_fetch", "web_search", "search_papers"})

def parallel_replies_from_metadata(metadata: Mapping[str, Any]) -> list[dict]:
    branches = metadata.get("parallel_branches")
    if not isinstance(branches, list):
        return []
    return sorted(
        (dict(item) for item in branches if isinstance(item, dict)),
        key=lambda item: (str(item.get("created_at") or ""), str(item.get("source_event_id") or "")),
    )


class ParallelBranchRunner:
    """Answer one parallel user event in an isolated, read-only child session."""

    def __init__(
        self,
        *,
        session_id: str,
        store: SessionStore,
        project_root: str | Path,
        provider: ChatProvider,
        reasoning_effort: str | None,
        permission_manager: PermissionManager | None,
        sandbox_access: SandboxAccess | None,
        parent_tool_names: Iterable[str],
        user_timezone: str | None = None,
        provider_resolver: Any | None = None,
        auxiliary_models: AuxiliaryModels = AuxiliaryModels(),
    ) -> None:
        self.session_id = session_id
        self.store = store
        self.project_root = Path(project_root).resolve()
        self.provider = provider
        self.reasoning_effort = reasoning_effort
        self.provider_resolver = provider_resolver
        self.auxiliary_models = auxiliary_models
        self.permission_manager = permission_manager or PermissionManager(
            policy=DefaultPermissionPolicy(self.project_root),
        )
        self.sandbox_access = sandbox_access or SandboxAccess()
        self.user_timezone = user_timezone
        self.network_tools = PARALLEL_NETWORK_TOOLS & {str(name) for name in parent_tool_names}
        self.allowed_tool_names = PARALLEL_TOOL_ALLOWLIST | self.network_tools
        self._children: dict[str, AgentSession] = {}

    # ------------------------------------------------------------------ public
    async def execute(self, turn: ActiveRun, *, on_running: Callable[[], None] | None = None) -> None:
        event = turn.initial_event
        question = self._question(event)
        existing = self._find(event.id, self._read_branches())
        if existing is not None:
            if existing.get("status") == "running":
                # A branch left running by a previous process must be surfaced as
                # interrupted, never replayed automatically.
                self._update_branch(event.id, status="interrupted", error=self._interrupted_error())
            return
        if not question:
            self._append_branch(
                source_event_id=event.id,
                child_session_id=None,
                question="",
                status="failed",
                answer=None,
                error="parallel branch requires a non-empty question",
            )
            return

        settings = event.input.settings
        try:
            provider, reasoning_effort = self._resolve_config()
            context_window = None
            permission_scope = nullcontext()
            sandbox_mode = self.sandbox_access.mode
            if settings is not None:
                provider, reasoning_effort, context_window = turn.binding.context.model_for_turn(settings.model_ref)
                mode = PermissionMode(settings.permission_mode)
                permission_scope = self.permission_manager.use_mode(mode)
                sandbox_mode = SandboxAccessMode.UNRESTRICTED if mode == PermissionMode.BYPASS else SandboxAccessMode.PROJECT
            with permission_scope:
                child = self._create_child(event, question, SandboxAccess(mode=sandbox_mode))
        except Exception as exc:
            self._append_branch(
                source_event_id=event.id,
                child_session_id=None,
                question=question,
                status="failed",
                answer=None,
                error=str(exc) or type(exc).__name__,
            )
            raise

        self._append_branch(
            source_event_id=event.id,
            child_session_id=child.session_id,
            question=question,
            status="running",
            answer=None,
            error=None,
        )
        runner = self._runner(child, provider, reasoning_effort)
        if settings is not None:
            runner.model_ref = settings.model_ref
            runner.context_window = context_window
        try:
            if on_running is not None:
                on_running()
            from dataclasses import replace
            application = turn.binding.context.application
            key = application.register(runner)
            child_event = replace(event, id=event.id + ':parallel', session_id=child.session_id,
                                  payload={**event.payload, 'mode': 'follow_up'})
            receipt = application.submit_event(child_event, key)
            response = await application.wait_for_event(receipt)
            if receipt.status == 'cancelled':
                raise asyncio.CancelledError
        except asyncio.CancelledError:
            application.stop_session(key)
            active = application.runs.get(key)
            if active is not None and active.task is not None:
                await asyncio.shield(active.task)
            self._update_branch(event.id, status="cancelled", error="parallel branch was cancelled")
            raise
        except Exception as exc:
            self._update_branch(event.id, status="failed", error=str(exc) or type(exc).__name__)
            raise

        content = (response.content or "").strip()
        if response.finish_reason in (None, "stop") and content:
            self._update_branch(event.id, status="succeeded", answer=response.content, error=None)
            return
        reason = response.finish_reason or ("no answer content" if not content else None)
        self._update_branch(
            event.id,
            status="failed",
            error=f"parallel branch did not complete normally: {reason}",
        )

    def replies(self) -> list[dict]:
        """Return a stable copy of persisted branch results without touching the model."""
        return self._safe_read_branches()


    # ------------------------------------------------------------------ internals
    @staticmethod
    def _question(event) -> str:
        if event.type != "user.message":
            return ""
        content = event.payload.get("content")
        if not isinstance(content, dict):
            return ""
        text = content.get("text")
        return text.strip() if isinstance(text, str) else ""

    @staticmethod
    def _interrupted_error() -> str:
        return "parallel branch was interrupted by a host restart"

    @staticmethod
    def _find(source_event_id: str, branches: list[dict]) -> dict | None:
        return next((item for item in branches if item.get("source_event_id") == source_event_id), None)

    def _read_branches(self) -> list[dict]:
        record = catalog_record(self.store.sessions_dir / f"{self.session_id}.jsonl")
        return deepcopy(parallel_replies_from_metadata(record.metadata))

    def _safe_read_branches(self) -> list[dict]:
        try:
            return self._read_branches()
        except Exception:
            return []

    def _write_branches(self, branches: list[dict]) -> None:
        data = self.store.load(self.session_id)
        self.store.append_session_metadata_updated(data,
            parallel_branches=[deepcopy(item) for item in branches],
        )

    def _append_branch(self, **fields: Any) -> None:
        now = utc_now_iso()
        entry = {
            "source_event_id": fields["source_event_id"],
            "child_session_id": fields.get("child_session_id"),
            "question": fields.get("question", ""),
            "status": fields["status"],
            "answer": fields.get("answer"),
            "error": fields.get("error"),
            "created_at": now,
            "updated_at": now,
        }
        branches = self._read_branches()
        branches.append(entry)
        self._write_branches(branches)

    def _update_branch(self, source_event_id: str, **fields: Any) -> None:
        branches = self._read_branches()
        target = self._find(source_event_id, branches)
        if target is None:
            return
        target.update(fields)
        target["updated_at"] = utc_now_iso()
        self._write_branches(branches)

    def _build_tools(self, access: SandboxAccess) -> list[Tool]:
        tools = create_builtin_tools(
            self.project_root,
            include_mutation_tools=False,
            include_execution_tools=False,
            include_network_tools=bool(self.network_tools),
            access=access,
            include_ask_user=False,
        )
        return [replace(tool, exposure="direct") for tool in tools if tool.name in self.allowed_tool_names]

    def _create_child(self, event, question: str, access: SandboxAccess) -> AgentSession:
        tools = self._build_tools(access)
        permissions = DelegatedPermissionManager(self.permission_manager, self.project_root)
        child = new_session(
            store=self.store,
            session_id=new_session_id(),
            tools=tools,
            permission_manager=permissions,
            sandbox_access=access,
            data_root=self.store.root,
            user_timezone=self.user_timezone,
            include_session_tools=False,
        )
        child.allowed_tool_names = set(self.allowed_tool_names)
        child.allow_background_execution = False
        child.store.append_session_metadata_updated(child.data,
            parallel_parent_session_id=self.session_id,
            parallel_source_event_id=event.id,
            parallel_question=question,
        )
        self._children[child.session_id] = child
        return child

    def _runner(self, child: AgentSession, provider: ChatProvider,
                reasoning_effort: str | None) -> SessionRuntime:
        capabilities = AgentRuntimeCapabilities(
            allow_user_input=False,
            enable_delegate_tool=False,
            expose_planning_tools=False,
            expose_think_tool="think" in self.allowed_tool_names,
            expose_web_search_tool="web_search" in self.network_tools,
            enable_process_tools=False,
            enable_native_async_tools=False,
        )
        return SessionRuntime(
            session=child,
            auxiliary_models=self.auxiliary_models,
            provider=provider,
            tools=list(child.tool_registry.tools()),
            reasoning_effort=reasoning_effort,
            runtime_capabilities=capabilities,
        )

    def _resolve_config(self) -> tuple[ChatProvider, str | None]:
        """Freeze the model and reasoning effort that are current when a branch starts.

        The host may switch models between branches; a branch that is already
        running keeps the provider it started with.
        """
        resolver = self.provider_resolver
        if resolver is not None:
            try:
                resolved = resolver()
            except Exception:
                resolved = None
            if isinstance(resolved, tuple) and len(resolved) == 2 and resolved[0] is not None:
                return resolved[0], resolved[1]
        return self.provider, self.reasoning_effort
