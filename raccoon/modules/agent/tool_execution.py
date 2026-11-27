"""Tool execution helpers for AgentRun.

Owns parallel-batch policy, interactive tool sequencing, inline permission
waiting, and tool-event emission shape so AgentRun can stay orchestration-only.
"""

from __future__ import annotations

import asyncio
from functools import partial
from copy import deepcopy
from dataclasses import dataclass, replace
from uuid import uuid4
from typing import Literal, TYPE_CHECKING

import anyio

from raccoon.runtime.cancellation import AgentCancelledError, CancellationToken, cancellation_context
from raccoon.modules.tools.registry import ToolRegistry
from raccoon.modules.agent.session import AgentSession
from raccoon.modules.tools.background_controls import has_background_control_fields, strip_background_controls
from raccoon.runtime.background import BackgroundCapacityError, BackgroundHandleConflict, BackgroundJob, validate_task_alias
from raccoon.modules.agent.background_results import make_background_placeholder_result
from raccoon.modules.planning.reducer import TaskPlanCommandError, TaskPlanRevisionConflict
from raccoon.modules.planning.projection import ready_task_ids
from raccoon.modules.planning.service import TaskPlanService
from raccoon.runtime.user_input import UserInputRequest, user_input_request_from_tool_result
from raccoon.modules.permissions.types import PermissionDecisionKind, PermissionMode, PermissionRequest
from raccoon.runtime.model_types import ToolCall, ToolDefinition
from raccoon.modules.tools.permission_results import make_permission_denied_result, make_prewrite_review_failed_result, make_prewrite_review_stale_result
from raccoon.modules.tools.review import PrewriteReview, build_prewrite_review, supports_prewrite_review
from raccoon.modules.agent.permission_review import review_tool_operation
from raccoon.modules.tools.permissions import preflight_tool_permission
from raccoon.modules.tools.execution import execute_tool
from raccoon.modules.tools.types import ToolResult, make_error_result
from raccoon.modules.collaboration.worktree import WorktreeManager

PARALLEL_READONLY_TOOL_NAMES = frozenset(
    {
        "ls",
        "view",
        "grep",
        "glob",
        "tree",
        "read_multi",
        "git_status",
        "git_diff",
        "git_log",
    }
)
BYPASS_PARALLEL_TOOL_NAMES = PARALLEL_READONLY_TOOL_NAMES | frozenset(
    {
        "write",
        "edit",
        "delete",
        "apply_patch",
        "shell",
        "python_exec",
        "web_fetch",
        "web_search",
        "search_papers",
    }
)


@dataclass(frozen=True, slots=True)
class ToolExecutionEvent:
    """Runtime-visible tool activity event.

    These events are intentionally separate from provider stream events: provider
    streams describe model output, while this describes local tool execution.
    """

    kind: Literal[
        "prewrite_review",
        "started",
        "finished",
        "permission_requested",
        "denied",
        "skipped",
        "interrupted",
        "background_started",
    ]
    tool_call: ToolCall
    result: ToolResult | None = None
    permission_request: PermissionRequest | None = None
    prewrite_review: dict[str, object] | None = None


if TYPE_CHECKING:
    from raccoon.modules.agent.run import AgentRun


async def execute_tool_batch(loop: AgentRun, tool_calls: list[ToolCall]) -> None:
    tool_calls = deepcopy(tool_calls)
    # wait_for_tasks reads this scope through its registered executor callback.
    loop._background_alias_scope = object()
    try:
        loaded_at_start = loop.session.loaded_tool_definitions()
        # 先一次性把控制面字段（run_in_background/background_label）从每个 tool_call 里剥掉，
        # executor 永远看不到它们。没有控制字段时归一化结果与原 tool_call 完全等价，普通
        # 路径行为不变；后台请求信息按 tool_call_id 单独记录，供本轮调度使用。
        tool_calls, background_requests = _normalize_background_controls(loop.session, tool_calls)
        index = 0
        while index < len(tool_calls):
            loop._check_cancelled()
            tool_call = tool_calls[index]
            validation_error = loop._validate_tool_call(tool_call, loaded_definitions=loaded_at_start)
            request = background_requests.get(tool_call.id)
            handle = request[3] if request is not None else None
            if validation_error is None and request is not None and request[0] and not loop.session.allow_background_execution:
                validation_error = make_error_result(tool_call.name, "当前 Agent 不允许后台执行。", background_rejected="not_allowed")
            if validation_error is None:
                validation_error = _invalid_task_handle(tool_call.name, handle)
            if validation_error is not None:
                loop._emit_tool_event("denied", tool_call, result=validation_error)
                await _record_result(loop, tool_call, validation_error)
                index += 1
                continue
            permission_error = await check_tool_permission(loop, tool_call)
            if permission_error is not None:
                loop._emit_tool_event("denied", tool_call, result=permission_error)
                await _record_result(loop, tool_call, permission_error)
                index += 1
                continue

            native_background = loop.native_async_tools and tool_call.async_execution
            requested_background = request is not None and request[0]
            if (native_background or requested_background) and not _delegate_control(tool_call):
                result = await anyio.to_thread.run_sync(partial(
                    _dispatch_background, loop, tool_call,
                    label=request[1] if requested_background and not native_background else None,
                    task_id=request[2] if requested_background and not native_background else None,
                    native=native_background, handle=handle,
                ))
                if result is not None:
                    await _record_result(loop, tool_call, result)
                index += 1
                continue

            if can_execute_in_parallel(loop, tool_call, background_request=request, permission_checked=True):
                batch_end = parallel_readonly_batch_end(loop, tool_calls, index, background_requests)
                batch = tool_calls[index:batch_end]
                if len(batch) == 1:
                    result = await execute_tool_call(loop, batch[0])
                    await _record_result(loop, batch[0], result)
                else:
                    results = await execute_parallel_batch(loop, batch)
                    for batch_tool_call, result in zip(batch, results, strict=True):
                        await _record_result(loop, batch_tool_call, result)
                index = batch_end
                continue

            result = await execute_tool_call(loop, tool_call)
            await _record_result(loop, tool_call, result)
            index += 1
    finally:
        loop._background_alias_scope = None


def _normalize_background_controls(
    session: AgentSession,
    tool_calls: list[ToolCall],
) -> tuple[list[ToolCall], dict[str, tuple[bool, str | None, str | None, str | None]]]:
    """Strip control-plane fields once and record which calls asked for background.

    Returns cleaned tool calls (executor-visible args only) plus a map from
    tool_call_id to ``(run_in_background, label, task_id, handle)``.  Calls
    without control fields are returned unchanged, so the ordinary path is
    unaffected.
    """

    cleaned: list[ToolCall] = []
    requested: dict[str, tuple[bool, str | None, str | None, str | None]] = {}
    for tool_call in tool_calls:
        if not has_background_control_fields(tool_call.arguments):
            cleaned.append(tool_call)
            continue
        clean_args, run_in_background, label, task_id, handle = strip_background_controls(
            tool_call.arguments, preserve_task_handle=not session.has_task_alias(tool_call),
        )
        cleaned.append(replace(tool_call, arguments=clean_args))
        if run_in_background or handle is not None:
            requested[tool_call.id] = (run_in_background, label, task_id, handle)
    return cleaned, requested


def _invalid_task_handle(tool_name: str, handle: str | None) -> ToolResult | None:
    if handle is not None:
        try:
            validate_task_alias(handle)
        except BackgroundHandleConflict as exc:
            return make_error_result(tool_name, str(exc), background_rejected="invalid_handle")
    return None


def _dispatch_background(
    loop: AgentRun,
    tool_call: ToolCall,
    *,
    label: str | None,
    task_id: str | None,
    native: bool = False,
    handle: str | None = None,
) -> ToolResult | None:
    """Enqueue a permission-cleared tool call as a background job.

    Legacy dispatch returns a placeholder; native dispatch returns None and
    leaves the original call pending. On rejection (disabled runtime, ineligible tool,
    capacity, or a conflicting handle) it returns a normal error result so the model can react and
    the provider history stays valid.
    """

    loop._check_cancelled()
    if not loop.session.allow_background_execution:
        return make_error_result(tool_call.name, "当前 Agent 不允许后台执行。", background_rejected="not_allowed")
    if loop.background_manager is None:
        return make_error_result(
            tool_call.name,
            "后台执行未启用；请去掉 run_in_background 后重试。",
            background_rejected="disabled",
        )
    observed_revision = _validate_background_task_id(loop, tool_call.name, task_id)
    if isinstance(observed_revision, ToolResult):
        return observed_revision
    tool = loop.session.tool_registry.get(tool_call.name)
    if tool is None or not tool.supports_background:
        return make_error_result(
            tool_call.name,
            f"工具 {tool_call.name} 不支持后台执行；请去掉 run_in_background 后重试。",
            background_rejected="not_allowed",
        )
    if tool_call.name == "delegate" and not _delegate_call_allows_background(loop, tool_call):
        return make_error_result(
            tool_call.name,
            "该子 Agent 的配置不允许后台执行，或它不在当前可委派列表中。",
            background_rejected="role_not_allowed",
        )
    if tool_call.name == "delegate" and _delegate_call_requires_worktree(loop, tool_call) and not _worktree_isolation_available(loop):
        return make_error_result(
            tool_call.name,
            "该子 Agent 的后台执行需要 git worktree 隔离，但当前项目不可用；请在 git 仓库内使用，或改用前台执行。",
            background_rejected="worktree_unavailable",
        )
    # 冻结一份可信 tool_call，后台线程执行时不再受外部影响。已通过权限预检，因此
    # 后台执行直接调用执行器，不会二次触发 ASK。
    trusted_arguments = deepcopy(tool_call.arguments)
    # 可变更文件的后台角色（coder）必须在隔离 git worktree 内执行，绝不触碰父工作区。
    # 这里注入内部控制字段 isolate_worktree，delegate executor 会据此走隔离路径。
    if tool_call.name == "delegate" and _delegate_call_requires_worktree(loop, tool_call):
        if isinstance(trusted_arguments, dict):
            trusted_arguments = {**trusted_arguments, "isolate_worktree": True}
    trusted = ToolCall(id=tool_call.id, name=tool_call.name, arguments=trusted_arguments, async_execution=tool_call.async_execution)

    def run() -> ToolResult:
        invalid = loop._validate_tool_call(tool_call)
        if invalid is not None:
            return invalid
        return execute_tool(loop.session.tool_registry, trusted.name, trusted.arguments)

    def complete_task_plan(job: BackgroundJob) -> str | None:
        if job.task_id is not None:
            return _mark_background_task_completed(
                loop,
                job.task_id,
                observed_revision=job.observed_revision,
            )
        return None

    if native:
        # Persist ownership before any background side effect can start.
        loop.session.mark_background_started(tool_call.id, loop.background_manager.run_id, native=True)
    try:
        job = loop.background_manager.start(
            run,
            session_id=loop.session.session_id,
            tool_name=tool_call.name,
            label=label,
            task_id=task_id,
            observed_revision=observed_revision,
            on_completed=complete_task_plan if task_id is not None else None,
            tool_call_id=tool_call.id if native else None,
            handle=handle,
            alias_scope=loop._background_alias_scope,
        )
    except BackgroundCapacityError as exc:
        return make_error_result(
            tool_call.name,
            str(exc),
            background_rejected="capacity",
        )
    except BackgroundHandleConflict as exc:
        return make_error_result(
            tool_call.name,
            str(exc),
            background_rejected="handle_conflict",
        )
    loop.session.mark_background_started(tool_call.id, loop.background_manager.run_id, job_id=job.id)
    loop._emit_tool_event("background_started", tool_call)
    if native:
        return None
    return make_background_placeholder_result(job)


def _task_plan_service(loop: AgentRun) -> TaskPlanService:
    return TaskPlanService(store=loop.session.store, data=loop.session.data)


def _validate_background_task_id(loop: AgentRun, tool_name: str, task_id: str | None) -> int | ToolResult | None:
    if task_id is None:
        return None
    plan = _task_plan_service(loop).current()
    if plan is None:
        return make_error_result(
            tool_name,
            f"Cannot start background work for task_id {task_id!r}: no current task plan. " "Call task_create first, or remove task_id.",
            background_rejected="task_plan_missing",
            task_id=task_id,
        )
    if not any(task.id == task_id for task in plan.tasks):
        return make_error_result(
            tool_name,
            f"Cannot start background work: task_id {task_id!r} is not in the current task plan. " "Call task_list, then retry with an existing task ID.",
            background_rejected="task_not_found",
            task_id=task_id,
            actual_revision=plan.revision,
        )
    return plan.revision


def _mark_background_task_completed(loop: AgentRun, task_id: str, *, observed_revision: int | None) -> str:
    """Advance the still-active task without overwriting newer agent decisions."""

    service = _task_plan_service(loop)
    for _ in range(3):
        plan = service.current()
        if plan is None:
            return f"TaskPlan task {task_id!r} was not updated because no plan is current."
        task = next((candidate for candidate in plan.tasks if candidate.id == task_id), None)
        if task is None:
            return f"TaskPlan task {task_id!r} was not updated because it no longer exists."
        if task.status in {"completed", "cancelled"}:
            return f"TaskPlan task {task_id!r} was not updated because it is already {task.status}."
        try:
            if task.status == "pending":
                if plan.revision != observed_revision:
                    return f"TaskPlan task {task_id!r} was not updated because it returned to pending " "after this background job started."
                if task_id not in ready_task_ids(plan):
                    return f"TaskPlan task {task_id!r} was not updated because it is pending and blocked."
                plan = service.update(
                    expected_revision=plan.revision,
                    updates=[{"id": task_id, "status": "in_progress"}],
                ).plan
            service.update(
                expected_revision=plan.revision,
                updates=[{"id": task_id, "status": "completed"}],
            )
        except TaskPlanRevisionConflict:
            continue
        except TaskPlanCommandError:
            return f"TaskPlan task {task_id!r} was not updated because its latest state rejects completion."
        return f"TaskPlan task {task_id!r} completed."
    return f"TaskPlan task {task_id!r} was not updated because the plan changed concurrently."


def _delegate_call_allows_background(loop: AgentRun, tool_call: ToolCall) -> bool:
    arguments = tool_call.arguments
    if not isinstance(arguments, dict):
        return False
    config = loop.session.subagent_configs.get(str(arguments.get("agent_name") or arguments.get("role") or ""))
    return bool(config and config.allow_background)


def _delegate_control(tool_call: ToolCall) -> bool:
    return tool_call.name == 'delegate' and isinstance(tool_call.arguments, dict) and tool_call.arguments.get('action', 'spawn') != 'spawn'


def _delegate_call_requires_worktree(loop: AgentRun, tool_call: ToolCall) -> bool:
    arguments = tool_call.arguments
    if not isinstance(arguments, dict):
        return False
    config = loop.session.subagent_configs.get(str(arguments.get("agent_name") or arguments.get("role") or ""))
    return bool(config and config.requires_worktree)


def _worktree_isolation_available(loop: AgentRun) -> bool:
    manager = loop.session.permission_manager
    if manager is None:
        return False
    return WorktreeManager(manager.policy.project_root).available()


async def check_tool_permission(loop: AgentRun, tool_call: ToolCall) -> ToolResult | None:
    """权限检查、必要时等待用户、复核原调用，然后回到同一批执行流程。"""
    manager = loop.session.permission_manager
    tool = loop.session.tool_registry.get(tool_call.name)
    preflight = preflight_tool_permission(manager, tool, tool_call.arguments)
    if preflight is None:
        return None
    request, decision = preflight
    if decision.kind == PermissionDecisionKind.DENY:
        return make_permission_denied_result(tool_name=tool_call.name, request=request, decision=decision)
    if decision.kind == PermissionDecisionKind.ALLOW:
        if manager.mode == PermissionMode.BYPASS:
            return await anyio.to_thread.run_sync(partial(
                _publish_bypass_preview, loop, tool_call, request=request,
            ))
        return None

    trusted = deepcopy(tool_call)
    definition = deepcopy(tool.definition)
    review = None
    if supports_prewrite_review(trusted.name):
        review = await anyio.to_thread.run_sync(partial(
            build_prewrite_review, manager.policy.project_root, trusted, access=loop.session.sandbox_access,
        ))
        if not review.ok:
            return make_prewrite_review_failed_result(tool_name=trusted.name, request=request, error=review.error or "未知错误")

    if manager.mode == PermissionMode.AUTO:
        decision = await review_tool_operation(loop, tool, trusted, request, review)
        loop._check_cancelled()
        current = manager.preflight(request)
        if current.kind == PermissionDecisionKind.DENY:
            return make_permission_denied_result(tool_name=trusted.name, request=request, decision=current)
        if decision.kind == PermissionDecisionKind.DENY:
            return make_permission_denied_result(tool_name=trusted.name, request=request, decision=decision)
        if decision.kind == PermissionDecisionKind.ALLOW:
            return await _check_reviewed_operation(loop, trusted, definition, request, review)
        request = replace(request, reason=decision.reason)

    if not manager.allow_user_approval or loop.request_input is None:
        decision = manager.reject_user_approval("当前执行入口不支持人工审批。")
        return make_permission_denied_result(tool_name=trusted.name, request=request, decision=decision)

    # Each interaction has a unique ID; an old button cannot approve a new operation.
    request = replace(request, id="approval_" + uuid4().hex)
    confirmation = manager.build_confirmation(request)
    if review is not None:
        confirmation.payload["prewrite_review"] = review.to_payload()
    confirmation.payload["pending_tool_call"] = {
        "id": trusted.id, "name": trusted.name, "arguments": deepcopy(trusted.arguments),
    }
    loop._emit_tool_event("permission_requested", trusted, permission_request=request)
    answer = await _wait_for_permission(loop, trusted, confirmation)
    invalid = await _check_reviewed_operation(loop, trusted, definition, request, review)
    if invalid is not None:
        return invalid
    decision = manager.resolve_confirmation(request, answer)
    if decision.kind != PermissionDecisionKind.ALLOW:
        return make_permission_denied_result(tool_name=trusted.name, request=request, decision=decision)
    return None


async def _check_reviewed_operation(
    loop: AgentRun, trusted: ToolCall, definition: ToolDefinition,
    request: PermissionRequest, review: PrewriteReview | None,
) -> ToolResult | None:
    """Both model and human approvals apply only to the operation and file snapshot reviewed."""
    current_tool = loop.session.tool_registry.get(trusted.name)
    if current_tool is None or current_tool.definition != definition:
        return make_error_result(trusted.name, "审核期间工具定义发生变化，请重新发起调用。")
    if review is not None and not await anyio.to_thread.run_sync(partial(
        review.is_current, loop.session.permission_manager.policy.project_root, access=loop.session.sandbox_access,
    )):
        return make_prewrite_review_stale_result(tool_name=trusted.name, request=request)
    return None


async def _wait_for_permission(loop: AgentRun, trusted: ToolCall, confirmation: UserInputRequest) -> str:
    """等待人工输入，取消时结算未执行的调用。"""
    try:
        answer = await loop.request_input(confirmation)
        loop._check_cancelled()
    except (AgentCancelledError, asyncio.CancelledError):
        result = make_error_result(trusted.name, "等待审批时已取消；工具未执行。", interrupted=True, execution_outcome="not_executed")
        loop.session.settle_tool_result(tool_call=trusted, result=result)
        loop._emit_tool_event("interrupted", trusted, result=result)
        raise
    return answer


async def _record_result(
    loop: AgentRun,
    tool_call: ToolCall,
    result: ToolResult,
) -> None:
    pending = user_input_request_from_tool_result(
        result, tool_call_id=tool_call.id, tool_name=tool_call.name,
    )
    if pending is not None:
        if loop.request_input is None:
            result = make_error_result(tool_call.name, "当前运行没有人工回答入口。")
        else:
            answer = await loop.request_input(pending)
            loop._check_cancelled()
            data = dict(result.data)
            data.pop("requires_user_input", None)
            data["answer"] = answer
            result = replace(result, content=f"{pending.question}\n用户回答：{answer}", data=data)
    loop.session.settle_tool_result(tool_call=tool_call, result=result)


def parallel_readonly_batch_end(loop: AgentRun, tool_calls: list[ToolCall], start: int, background_requests) -> int:
    end = start + 1
    while end < len(tool_calls):
        call = tool_calls[end]
        if not can_execute_in_parallel(loop, call, background_request=background_requests.get(call.id)):
            break
        end += 1
    return end


def can_execute_in_parallel(loop: AgentRun, tool_call: ToolCall, *, background_request=None, permission_checked: bool = False) -> bool:
    # Every member needs routing validation before even permission preflight.
    if not permission_checked and loop._validate_tool_call(tool_call) is not None:
        return False
    if loop.native_async_tools and tool_call.async_execution and not _delegate_control(tool_call):
        return False
    if requires_bypass_prewrite_review(loop, tool_call):
        return False
    if background_request is not None and (background_request[3] is not None or background_request[0] and not _delegate_control(tool_call)):
        # Calls with a task alias must go through the per-call validation above.
        return False
    if tool_call.name not in parallel_tool_names_for_current_mode(loop):
        return False
    if permission_checked:
        return True
    tool = loop.session.tool_registry.get(tool_call.name)
    preflight = preflight_tool_permission(loop.session.permission_manager, tool, tool_call.arguments)
    return preflight is None or preflight[1].kind == PermissionDecisionKind.ALLOW


def parallel_tool_names_for_current_mode(loop: AgentRun) -> frozenset[str]:
    if loop.session.permission_manager is not None and loop.session.permission_manager.mode == PermissionMode.BYPASS:
        return BYPASS_PARALLEL_TOOL_NAMES
    return PARALLEL_READONLY_TOOL_NAMES


def requires_bypass_prewrite_review(loop: AgentRun, tool_call: ToolCall) -> bool:
    manager = loop.session.permission_manager
    return (
        loop.session.require_prewrite_review
        and manager is not None
        and manager.mode == PermissionMode.BYPASS
        and loop.session.tool_registry.get(tool_call.name) is not None
        and supports_prewrite_review(tool_call.name)
    )


def _publish_bypass_preview(
    loop: AgentRun,
    tool_call: ToolCall,
    *,
    request: PermissionRequest,
) -> ToolResult | None:
    if not requires_bypass_prewrite_review(loop, tool_call):
        return None
    review = build_prewrite_review(
        loop.session.permission_manager.policy.project_root,
        tool_call,
        access=loop.session.sandbox_access,
    )
    if not review.ok:
        return make_prewrite_review_failed_result(
            tool_name=tool_call.name,
            request=request,
            error=review.error or "未知错误",
        )
    loop._emit_tool_event("prewrite_review", tool_call, prewrite_review=review.to_payload())
    return None


async def execute_tool_call(loop: AgentRun, tool_call: ToolCall) -> ToolResult:
    loop._check_cancelled()
    loop._emit_tool_event("started", tool_call)
    result = await anyio.to_thread.run_sync(_execute_checked_tool, loop.session.tool_registry, tool_call, loop.cancellation_token)
    loop._emit_tool_event("finished", tool_call, result=result)
    loop._check_cancelled()
    return result


async def execute_parallel_batch(loop: AgentRun, tool_calls: list[ToolCall]) -> list[ToolResult]:
    """并发执行已通过检查的工具；按调用顺序返回结果。"""

    loop._check_cancelled()
    for tool_call in tool_calls:
        loop._emit_tool_event("started", tool_call)
    executions = []
    for call in tool_calls:
        executions.append(anyio.to_thread.run_sync(
            _execute_checked_tool, loop.session.tool_registry, call, loop.cancellation_token))
    results = await asyncio.gather(*executions, return_exceptions=True)
    # 先等同批工作线程结算，避免取消后仍有未归属的前台操作继续执行。
    for result in results:
        if isinstance(result, BaseException):
            raise result
    for tool_call, result in zip(tool_calls, results, strict=True):
        loop._emit_tool_event("finished", tool_call, result=result)
    loop._check_cancelled()
    return results


def _execute_checked_tool(registry: ToolRegistry, tool_call: ToolCall, cancellation_token: CancellationToken | None) -> ToolResult:
    """已通过审批的同步工具在工作线程中执行，保留取消上下文。"""

    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()
    with cancellation_context(cancellation_token):
        return execute_tool(registry, tool_call.name, tool_call.arguments)
