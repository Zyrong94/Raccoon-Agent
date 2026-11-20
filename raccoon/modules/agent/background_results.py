"""Adapt finished jobs to Agent tool results and model notifications."""
from __future__ import annotations
from html import escape
from raccoon.runtime.background import (
    BackgroundJob,
    BackgroundNotification,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
    _summarize,
)
from raccoon.modules.tools.types import ToolResult, make_text_result

def build_background_notification(job: BackgroundJob, run_id: str) -> BackgroundNotification:
    summary = _summarize(job)
    ok = job.status == STATUS_COMPLETED
    payload = None
    if job.tool_call_id is not None:
        from raccoon.modules.tools.result_payload import result_to_payload
        from raccoon.modules.tools.types import make_error_result

        result = job.result
        if job.status == STATUS_CANCELLED or job.error is not None or result is None:
            result = make_error_result(job.tool_name, job.error or f"Background job {job.status}", status=job.status)
        try:
            payload = result_to_payload(result)
        except (TypeError, ValueError, RecursionError) as exc:
            payload = result_to_payload(make_error_result(job.tool_name, f"Tool result could not be persisted: {exc}"))
            job.status = STATUS_FAILED
            ok = False
    notification = BackgroundNotification(
        job_id=job.id,
        session_id=job.session_id,
        tool_name=job.tool_name,
        status=job.status,
        summary=summary,
        ok=ok,
        label=job.label,
        task_id=job.task_id,
        observed_revision=job.observed_revision,
        task_plan_completion=job.task_plan_completion,
        run_id=job.completion_run_id or run_id,
        kind='subagent' if job.completion_run_id else 'tool',
        tool_call_id=job.tool_call_id,
        result=payload,
        handle=job.handle,
    )
    return notification


def finalize_background_job(job: BackgroundJob) -> None:
    if job.status != STATUS_COMPLETED or job.on_completed is None:
        return
    try:
        job.task_plan_completion = job.on_completed(job)
    except Exception as exc:  # noqa: BLE001 - report persistence failures truthfully
        job.status = STATUS_FAILED
        job.error = f"Failed to record TaskPlan completion: {exc}"
        job.task_plan_completion = "TaskPlan completion failed; task state was not confirmed."


def make_background_placeholder_result(job: BackgroundJob) -> ToolResult:
    """为原始 tool_call 生成立即返回的占位结果。

    这条结果闭合了 provider 历史里的 tool_call，且刻意不带 requires_user_input，
    不会让 agent loop 暂停。真正结果稍后由 notification 送达。
    """

    label_hint = f"（{job.label}）" if job.label else ""
    handle_line = f"Task handle: {job.handle}\n" if job.handle else ""
    content = (
        f"Background job {job.id} started for {job.tool_name}{label_hint}.\n"
        f"{handle_line}"
        f"Result will be delivered as <task_notification> when complete.\n"
        f'Use background_status or background_cancel with job_id="{job.id}" if needed.'
    )
    return make_text_result(
        job.tool_name,
        content,
        background_job_id=job.id,
        tool_name=job.tool_name,
        status=STATUS_RUNNING,
        notification_pending=True,
        task_id=job.task_id,
        observed_revision=job.observed_revision,
        task_handle=job.handle,
    )


def render_task_notification(notification: BackgroundNotification) -> str:
    """把完成事件渲染成注入模型的 `<task_notification>` 文本。"""

    label = escape(notification.label, quote=False) if notification.label else None
    lines = [
        "<task_notification>",
        f"  <job_id>{escape(notification.job_id, quote=False)}</job_id>",
        f"  <kind>{escape(notification.kind, quote=False)}</kind>",
        f"  <tool_name>{escape(notification.tool_name, quote=False)}</tool_name>",
    ]
    if label:
        lines.append(f"  <label>{label}</label>")
    if notification.handle:
        lines.append(f"  <handle>{escape(notification.handle, quote=False)}</handle>")
    if notification.task_id:
        lines.append(f"  <task_id>{escape(notification.task_id, quote=False)}</task_id>")
    if notification.observed_revision is not None:
        lines.append(f"  <observed_revision>{notification.observed_revision}</observed_revision>")
    if notification.task_plan_completion:
        lines.append("  <task_plan_completion>" f"{escape(notification.task_plan_completion, quote=False)}" "</task_plan_completion>")
    lines.append(f"  <status>{escape(notification.status, quote=False)}</status>")
    lines.append(f"  <summary>{escape(notification.summary, quote=False)}</summary>")
    lines.append("</task_notification>")
    return "\n".join(lines)
