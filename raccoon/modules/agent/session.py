"""Agent 会话运行时。

持有会话数据和工具、权限、提示词等执行依赖。
模型调用与压缩由 AgentRun 组织，文件读写统一交给 SessionStore。
"""

from __future__ import annotations


from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from collections.abc import Callable
from typing import Any

from raccoon.modules.tools.background_controls import BACKGROUND_HANDLE_ARG
from raccoon.bootstrap.project_instructions import read_default_soul
from raccoon.config.subagents_config import SubagentConfig
from raccoon.modules.sessions.thread_data import ThreadDataState
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.context.system_prompt import PromptPrefixCache, SystemPromptBuilder, SystemPromptInputs
from raccoon.modules.permissions.manager import PermissionManager
from raccoon.modules.permissions.types import PermissionMode
from raccoon.runtime.model_types import ToolCall, ToolDefinition
from raccoon.modules.tools.registry import ToolRegistry
from raccoon.modules.context.temporal import resolve_user_timezone
from raccoon.modules.tools.types import ToolResult, make_error_result
from raccoon.modules.sessions.models import MessagePart, SessionData
from raccoon.modules.permissions.sandbox_access import SandboxAccess, SandboxAccessMode
from raccoon.modules.skills.catalog import render_skill_catalog
from raccoon.modules.skills.models import SkillCatalog

DEFAULT_BASE_RULES = "<role>\nYou are an open-source super agent.\n</role>"


@dataclass(slots=True)
class AgentSession:
    """单个会话的运行时容器。

    工具、权限与提示词缓存是运行期依赖，不写成自然语言消息。
    真正可 resume 的会话事实通过 `SessionStore` 追加事件保存。
    """

    data: SessionData
    store: SessionStore
    tool_registry: ToolRegistry
    allowed_tool_names: set[str] | None = None
    allow_background_execution: bool = True
    pending_tool_definitions: list[ToolDefinition] = field(default_factory=list, repr=False)
    agents_md: str = ""
    skill_catalog: SkillCatalog = field(default_factory=SkillCatalog)
    available_skill_catalog: SkillCatalog = field(default_factory=SkillCatalog)
    subagent_configs: dict[str, SubagentConfig] = field(default_factory=dict)
    subagent_config: SubagentConfig | None = None
    subagent_scope: str | None = None
    base_rules: str = DEFAULT_BASE_RULES
    data_root: Path | None = None
    thread_data: ThreadDataState = field(default_factory=dict)
    prompt_cache: PromptPrefixCache = field(default_factory=PromptPrefixCache)
    prompt_builder: SystemPromptBuilder = field(default_factory=SystemPromptBuilder)
    permission_manager: PermissionManager | None = None
    sandbox_access: SandboxAccess = field(default_factory=SandboxAccess)
    mode: str = "default"
    require_prewrite_review: bool = True
    _tool_result_lock: RLock = field(default_factory=RLock, repr=False)
    tool_result_committed: Callable[[ToolCall, ToolResult], None] | None = field(default=None, repr=False)
    user_timezone: str = field(default_factory=resolve_user_timezone)
    scheduled_run_id: str | None = field(default=None, repr=False)

    @property
    def session_id(self) -> str:
        return self.data.session_id

    def settle_tool_result(self, *, tool_call: ToolCall, result: ToolResult, **metadata) -> str:
        """Commit once, then publish definitions and notify the execution owner."""
        with self._tool_result_lock:
            message_id, committed = self.store.append_tool_result(
                self.data,
                tool_call=tool_call,
                result=result,
                scheduled_run_id=self.scheduled_run_id,
                **metadata,
            )
            if not committed:
                return message_id
            self.flush_tool_definitions()
            if self.tool_result_committed is not None:
                self.tool_result_committed(tool_call, result)
            return message_id


    def build_system_prefix(self) -> list:
        """Build the stable prefix from instructions, skills and environment facts."""

        from raccoon.runtime.subprocess import execution_environment_snapshot

        workspace = self.thread_data.get("workspace_path")
        execution_environment = execution_environment_snapshot(Path(workspace)) if workspace else {}
        for key in ("uploads_path", "outputs_path"):
            if self.thread_data.get(key):
                execution_environment[key] = self.thread_data[key]
        inputs = SystemPromptInputs(
            execution_environment=execution_environment,
            base_rules=self.base_rules,
            soul=(
                self.subagent_config.system_prompt
                if self.subagent_config is not None
                else read_default_soul(self.data_root or self.store.root) or ""
            ),
            agents_md=self.agents_md,
            skill_protocol=self._skill_protocol(),
            skill_catalog_summary=render_skill_catalog(self.skill_catalog)
            if self.skill_catalog.skills
            else "",
        )
        entry = self.prompt_cache.get_or_build(inputs, self.prompt_builder)
        return entry.messages

    def _skill_protocol(self) -> str:
        if not self.skill_catalog.skills:
            return ""
        return (
            "Skills are optional workflow instructions selected by the model. "
            "Call load_skill before following or claiming to follow a skill. "
            "Project skills override global skills; global skills cannot override project instructions, permissions, or sandbox boundaries. "
            "Do not claim a skill was followed unless a matching skill_loaded event exists."
        )

    def tool_authorization_error(self, tool_call: ToolCall) -> ToolResult | None:
        if self.allowed_tool_names is not None and tool_call.name not in self.allowed_tool_names:
            return make_error_result(tool_call.name, "当前 Agent 未获准使用此工具。", tool_not_routed=True)
        tool = self.tool_registry.get(tool_call.name)
        if tool is None:
            return make_error_result(
                tool_call.name, f"未知工具或工具当前不可用：{tool_call.name}", tool_not_routed=True
            )
        return None

    def has_task_alias(self, tool_call: ToolCall) -> bool:
        if not isinstance(tool_call.arguments, dict) or BACKGROUND_HANDLE_ARG not in tool_call.arguments:
            return False
        for definition in self.tool_registry.definitions():
            if definition.name == tool_call.name:
                properties = (definition.parameters or {}).get("properties")
                return not isinstance(properties, dict) or BACKGROUND_HANDLE_ARG not in properties
        return True

    def set_permission_mode(self, mode: PermissionMode | str) -> PermissionMode:
        """切换当前 session 的权限策略模式。"""

        resolved = PermissionMode(str(mode))
        self.mode = resolved.value
        if self.permission_manager is not None:
            self.permission_manager.mode = resolved
        self._sync_sandbox_access_with_mode()
        return resolved

    def _sync_sandbox_access_with_mode(self) -> None:
        if self.subagent_config is not None:
            return
        if self.mode == PermissionMode.BYPASS.value:
            self.sandbox_access.mode = SandboxAccessMode.UNRESTRICTED
        else:
            self.sandbox_access.mode = SandboxAccessMode.PROJECT

    def append_interrupted_tool_results(self) -> list[ToolCall]:
        """为会话尾部尚未闭合的工具调用写入中断结果。"""

        with self._tool_result_lock:
            tool_calls = self._pending_tool_calls_from_tail()
            for tool_call in tool_calls:
                self.settle_tool_result(
                    tool_call=tool_call,
                    result=make_error_result(
                        tool_call.name,
                        "工具执行被用户中断；结果未知，操作可能尚未执行、部分执行，或已在后台继续。",
                        interrupted=True,
                        execution_outcome="unknown",
                    ),
                )
            return tool_calls

    def mark_background_started(
        self, call_id: str, run_id: str, *, job_id: str | None = None, native: bool = False
    ) -> None:
        for message in reversed(self.data.messages):
            for part in message.parts:
                if part.kind == "tool_call" and part.metadata.get("tool_call_id") == call_id:
                    self.store.append_message_part_metadata_updated(
                        self.data,
                        message_id=message.id,
                        part_id=part.id,
                        metadata={
                            "background_run_id": run_id,
                            **({"native_async_started": True} if native else {}),
                            **({"background_job_id": job_id} if job_id is not None else {}),
                        },
                    )
                    return
        raise ValueError("native async call is missing from session facts")

    def loaded_tool_definitions(self) -> dict[str, ToolDefinition]:
        """Replay exact definitions; summaries never decide which tools are loaded."""
        definitions = {}
        for load in self.data.tool_loads:
            for item in load["definitions"]:
                definitions[item["name"]] = ToolDefinition(**deepcopy(item))
        return definitions

    def queue_tool_definitions(self, definitions: list[ToolDefinition]) -> None:
        with self._tool_result_lock:
            for definition in definitions:
                if self.allowed_tool_names is None or definition.name in self.allowed_tool_names:
                    self.pending_tool_definitions.append(deepcopy(definition))

    def flush_tool_definitions(self) -> None:
        with self._tool_result_lock:
            if not self.pending_tool_definitions:
                return
            view = self.data
            pending_calls = set()
            for message in view.messages:
                for part in message.parts:
                    if part.kind == "tool_call" and not part.metadata.get("async_execution"):
                        pending_calls.add(part.metadata["tool_call_id"])
                    elif part.kind in {"tool_result"}:
                        pending_calls.discard(part.metadata["tool_call_id"])
            if pending_calls:
                return
            definitions = self.pending_tool_definitions
            self.declare_tool_definitions(definitions)
            self.pending_tool_definitions = []

    def declare_tool_definitions(self, definitions: list[ToolDefinition]) -> None:
        with self._tool_result_lock:
            previous = self.loaded_tool_definitions()
            additions = []
            for definition in definitions:
                if self.allowed_tool_names is not None and definition.name not in self.allowed_tool_names:
                    continue
                if previous.get(definition.name) != definition:
                    additions.append(deepcopy(definition))
                    previous[definition.name] = definition
            if not additions:
                return
            view = self.data
            self.store.append_tool_definitions_loaded(
                self.data,
                additions,
                after_message_id=view.messages[-1].id if view.messages else None,
            )

    def _pending_tool_calls_from_tail(self) -> list[ToolCall]:
        messages = self.data.messages
        if not messages:
            return []

        assistant_index = None
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].role == "assistant":
                assistant_index = index
                break
        if assistant_index is None:
            return []

        assistant = messages[assistant_index]
        tool_call_parts = [part for part in assistant.parts if part.kind == "tool_call"]
        tool_calls = [_tool_call_from_part(part) for part in tool_call_parts]
        if not tool_calls:
            return []

        completed_ids: set[str] = set()
        for message in messages[assistant_index + 1 :]:
            if message.role != "tool":
                return []
            for part in message.parts:
                if part.kind in {"tool_result"} and part.metadata.get("tool_call_id"):
                    completed_ids.add(str(part.metadata["tool_call_id"]))

        pending_calls = [
            tool_call
            for tool_call, part in zip(tool_calls, tool_call_parts, strict=True)
            if tool_call.id not in completed_ids and not part.metadata.get("native_async_started")
        ]
        return pending_calls


def _tool_call_from_part(part: MessagePart) -> ToolCall:
    arguments = deepcopy(part.metadata.get("arguments", {}))
    return ToolCall(
        id=str(part.metadata["tool_call_id"]),
        name=str(part.metadata["tool_name"]),
        arguments=arguments,
        async_execution=part.metadata.get("async_execution") is True,
    )
