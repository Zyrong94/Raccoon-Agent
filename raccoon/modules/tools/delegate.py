"""Delegate tool for running restricted Raccoon subagents."""

from __future__ import annotations

from typing import Any

from raccoon.modules.collaboration.sessions import SubagentRequest
from raccoon.modules.collaboration.coordination import (
    run_subagent_operation, spawn_subagent, send_subagent, stop_subagent, list_subagents,
)
from raccoon.runtime.cancellation import current_cancellation_token
from raccoon.runtime.model_types import ToolDefinition
from raccoon.modules.tools.types import Tool, ToolResult, make_error_result, make_text_result
from raccoon.modules.tools.schema import object_schema


def create_delegate_tool(parent) -> Tool:
    """Create the parent-facing delegate tool.

    Background execution itself is handled by tool execution coordinator's generic Phase 1
    `run_in_background` control field.  The delegate executor keeps foreground
    semantics and receives cleaned arguments.
    """

    def delegate(
        role: str | None = None,
        task: str = "",
        parent_summary: str | None = None,
        agent_name: str | None = None,
        isolate_worktree: bool = False,
        action: str = 'spawn',
        subagent_session_id: str | None = None,
        message: str = '',
        mode: str = 'steer',
        event_id: str | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        # ``isolate_worktree`` is an internal control field injected by the parent
        # tool execution coordinator when it backgrounds a mutation-capable role; it is not part of
        # the model-visible schema.
        if kwargs:
            return make_error_result("delegate", f"未知参数：{', '.join(sorted(kwargs))}")
        if action == "profiles":
            profiles = [
                {"name": name, "description": parent.session.subagent_configs[name].description}
                for name in sorted(parent.session.subagent_configs)
            ]
            return make_text_result("delegate", str(profiles), profiles=profiles)
        if action != 'spawn':
            try:
                if action == 'list':
                    subagents = run_subagent_operation(parent, list_subagents(parent))
                    return make_text_result('delegate', str(subagents), subagents=subagents)
                if action == 'send':
                    operation = send_subagent(parent, subagent_session_id, message, mode=mode, event_id=event_id,
                                              cancellation_token=current_cancellation_token())
                    data = run_subagent_operation(parent, operation)
                elif action == 'stop':
                    data = run_subagent_operation(parent, stop_subagent(parent, subagent_session_id))
                else:
                    return make_error_result('delegate', 'action must be spawn, profiles, send, list or stop')
                if data.get('ok') is False:
                    return make_error_result('delegate', data['summary'], **data)
                return make_text_result('delegate', str(data), **data)
            except Exception as exc:
                return make_error_result('delegate', str(exc))
        selected_name = str(agent_name or role or "").strip()
        if selected_name not in parent.session.subagent_configs:
            return make_error_result("delegate", f"未知子代理角色：{selected_name}", agent_name=selected_name, error="agent_unavailable")
        normalized_task = str(task or "").strip()
        if not normalized_task:
            return make_error_result("delegate", "task 不能为空")
        request = SubagentRequest(
            agent_name=selected_name,
            task=normalized_task,
            parent_summary=parent_summary,
            isolate_worktree=bool(isolate_worktree),
        )
        try:
            operation = spawn_subagent(parent, request, cancellation_token=current_cancellation_token())
            result = run_subagent_operation(parent, operation)
        except Exception as error:
            return make_error_result('delegate', str(error))
        if not result.ok:
            return make_error_result("delegate", result.summary, **result.to_data())
        return make_text_result(
            "delegate",
            f"Subagent {result.subagent_session_id} completed.\n\n{result.summary}",
            **result.to_data(),
        )

    return Tool(
        definition=ToolDefinition(
            name="delegate",
            description=(
                "Spawn a restricted Raccoon subagent, or send/list/stop persistent subagents. "
                "Use action=profiles to discover current agent names and descriptions before spawning. "
                "send targets subagent_session_id; steer reaches the next model boundary and follow_up starts after the current turn. Parent messages cannot approve human permissions. Use for independent "
                "research, review, validation, or isolated implementation work. Do not use for nested "
                "delegation. Background execution and worktree isolation follow the selected subagent configuration. "
                "Subagents inherit the parent model."
            ),
            parameters=object_schema(
                {
                    'action': {'type': 'string', 'enum': ['spawn', 'profiles', 'send', 'list', 'stop'], 'description': 'Defaults to spawn.'},
                    'subagent_session_id': {'type': 'string'},
                    'message': {'type': 'string'},
                    'mode': {'type': 'string', 'enum': ['steer', 'follow_up']},
                    'event_id': {'type': 'string', 'description': 'Stable message identity for retry deduplication.'},
                    "agent_name": {
                        "type": "string",
                        "description": "Agent configuration name returned by action=profiles. Validated again when spawning.",
                    },
                    "task": {"type": "string", "description": "Concrete task for the subagent. Include specific file paths and necessary context; runtime directories are inherited automatically."},
                    "parent_summary": {
                        "type": "string",
                        "description": "Optional compact context from the parent.",
                    },
                },
                required=[],
            ),
        ),
        executor=delegate,
    )
