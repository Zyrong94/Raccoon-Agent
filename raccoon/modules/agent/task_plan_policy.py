"""Unified creation and final-reconciliation prompts for task plans."""

from __future__ import annotations

from raccoon.modules.planning.projection import ordered_tasks, project_plan

_TERMINAL_STATUSES = frozenset({"completed", "cancelled"})


def final_reconciliation_instruction(plan) -> str | None:
    if plan is None:
        return None

    projection = project_plan(plan)
    unfinished = [task for task in ordered_tasks(plan) if task.status not in _TERMINAL_STATUSES]
    if not unfinished:
        return None

    lines = [
        f"Before finalizing, reconcile the unfinished {projection['mode']} task plan.",
        "Use task_update by task ID to update statuses locally; " "do not recreate or rebuild the plan just to report progress.",
        "Continue required work, or explain the real blocker. " "Do not claim completion while required tasks remain unfinished.",
    ]
    lines.extend(f"- [{task.status}] {task.id}: {task.content}" for task in unfinished)
    return "\n".join(lines)
