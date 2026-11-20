from __future__ import annotations

from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.planning.service import TaskPlanService
from raccoon.modules.tools.task_create import create_task_create_tool
from raccoon.modules.tools.task_list import create_task_list_tool
from raccoon.modules.tools.task_revise import create_task_revise_tool
from raccoon.modules.tools.task_update import create_task_update_tool


def _tools(tmp_path):
    store = SessionStore(tmp_path)
    service = TaskPlanService(store=store, data=store.create())
    return {
        tool.name: tool
        for tool in (
            create_task_create_tool(service),
            create_task_update_tool(service),
            create_task_revise_tool(service),
            create_task_list_tool(service),
        )
    }


def test_task_plan_tools_return_normalized_mutations_and_current_state(tmp_path) -> None:
    tools = _tools(tmp_path)
    assert tools["task_list"].executor().data["revision"] == 0

    created = tools["task_create"].executor(
        mode="linear",
        expected_revision=0,
        tasks=[{"id": "inspect", "content": "Inspect"}],
    )
    updated = tools["task_update"].executor(
        expected_revision=1,
        updates=[{"id": "inspect", "owner": "main", "status": "in_progress"}],
    )
    revised = tools["task_revise"].executor(
        expected_revision=2,
        revisions=[{"id": "inspect", "content": "Inspect carefully"}],
    )
    listed = tools["task_list"].executor()

    assert created.ok and created.data["revision"] == 1
    assert created.data["changes"][0]["order"] == 0
    assert updated.ok and updated.data["changes"] == [{"id": "inspect", "status": "in_progress", "owner": "main"}]
    assert revised.ok and revised.data["changes"] == [{"id": "inspect", "content": "Inspect carefully"}]
    assert revised.data["snapshot"] == listed.data["plan"]
    assert "plan" not in revised.data
    assert listed.data["revision"] == 3
    assert listed.data["projection"]["ready_task_ids"] == []


def test_revision_conflict_is_recoverable_and_actionable(tmp_path) -> None:
    tools = _tools(tmp_path)
    tools["task_create"].executor(
        mode="linear",
        expected_revision=0,
        tasks=[{"id": "work", "content": "Work"}],
    )

    result = tools["task_update"].executor(
        expected_revision=0,
        updates=[{"id": "work", "status": "in_progress"}],
    )

    assert result.ok is False
    assert result.data["actual_revision"] == 1
    assert "task_list" in result.content
