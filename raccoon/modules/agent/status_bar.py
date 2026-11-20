"""Render current state; persistence and request timing stay in AgentRun."""

from raccoon.modules.planning.projection import ordered_tasks, project_plan


def render_agent_status(view, background_manager, instruction=None):
    lines = [
        "<agent_status>",
        "框架生成的当前状态，不是用户输入。同类状态以最新快照为准，旧快照仅记录当时状态。",
        "",
        "计划：",
    ]
    plan = view.task_plan
    if plan is None:
        lines.append("无当前计划。")
    else:
        lines.append(f"模式：{plan.mode}；revision={plan.revision}")
        completed = sum(task.status == "completed" for task in plan.tasks)
        cancelled = sum(task.status == "cancelled" for task in plan.tasks)
        lines.append(f"进度：{completed}/{len(plan.tasks)} 已完成，{cancelled} 已取消。")
        names = {"pending": "待开始", "in_progress": "进行中", "completed": "已完成", "cancelled": "已取消"}
        for task in ordered_tasks(plan):
            details = []
            if task.owner:
                details.append(f"负责人：{task.owner}")
            if task.depends_on:
                details.append("依赖：" + ", ".join(task.depends_on))
            suffix = "；" + "；".join(details) if details else ""
            lines.append(f"- {task.id} [{names[task.status]}] {task.content}{suffix}")
        projection = project_plan(plan)
        lines.append("可开始：" + (", ".join(projection["ready_task_ids"]) or "无"))
        lines.append("被阻塞：" + (", ".join(projection["blocked_task_ids"]) or "无"))

    calls = {}
    results = {}
    pending_results = []
    for message in view.messages:
        for part in message.parts:
            if part.kind == "tool_call":
                calls[part.metadata["tool_call_id"]] = part.metadata
            if "background_delivery" not in part.metadata:
                continue
            call = calls.get(part.metadata.get("tool_call_id"), {})
            job_id = part.metadata.get("background_job_id") or call.get("background_job_id")
            run_id = part.metadata.get("background_run_id") or call.get("background_run_id")
            if job_id:
                results[(run_id, job_id)] = part.metadata["background_delivery"]
            if part.metadata["background_delivery"] == "pending":
                name = part.metadata.get("background_tool_name") or part.metadata.get("tool_name", "")
                status = part.metadata.get("background_status")
                if status is None:
                    status = "completed" if part.metadata.get("ok") else "failed"
                pending_results.append((job_id or part.metadata.get("tool_call_id"), name, status))

    jobs = background_manager.list(session_id=view.session_id) if background_manager is not None else []
    lines.extend(["", "后台任务："])
    background_lines = []
    status_names = {"completed": "已完成", "failed": "失败", "cancelled": "已取消"}
    for job in sorted(jobs, key=lambda item: item.id):
        identity = (job.completion_run_id or job.run_id or background_manager.run_id, job.id)
        if identity in results:
            continue
        label = job.label or job.tool_name
        if job.status == "running":
            state = "取消中" if job.cancel_requested else "运行中"
        else:
            state = status_names.get(job.status, job.status) + "，结果待入历史"
        background_lines.append(f"- {job.handle or job.id} [{state}] {label}")
    for job_id, name, status in pending_results:
        background_lines.append(f"- {job_id} [{status_names.get(status, status)}，结果待接收] {name}")
    lines.extend(background_lines or ["无运行中或待接收结果的任务。"])
    if background_lines:
        lines.append("本轮普通原生工具须结束后才能最终回答；可用 wait_for_tasks 等待，或 background_status/background_cancel 查询、取消。独立委派任务可在后续轮次报告。")
    lines.extend(["", "临时提醒："])
    if instruction:
        lines.extend(["仅适用于本次模型调用；后续以新快照为准。", instruction])
    else:
        lines.append("无。")
    lines.append("</agent_status>")
    return "\n".join(lines)
