from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BrokenBarrierError

import pytest

import raccoon.modules.planning.service as service_module
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.planning.reducer import TaskPlanCommandError, TaskPlanRevisionConflict
from raccoon.modules.planning.service import TaskPlanMutation, TaskPlanService


def _service(tmp_path) -> tuple[SessionStore, TaskPlanService]:
    store = SessionStore(tmp_path)
    data = store.create(session_id="sess_plan")
    return store, TaskPlanService(store=store, data=data)


def _plan_events(store: SessionStore, session_id: str = "sess_plan"):
    return [event for event in store.list_events(session_id) if event.type == "task_plan_updated"]


def test_update_and_revise_each_write_one_event_with_reducer_changes(tmp_path) -> None:
    store, service = _service(tmp_path)
    service.create(
        mode="dag",
        expected_revision=0,
        tasks=[
            {"id": "inspect", "content": "Inspect"},
            {"id": "implement", "content": "Implement", "depends_on": ["inspect"]},
        ],
    )

    updated = service.update(
        expected_revision=1,
        updates=[{"id": "inspect", "status": "completed", "owner": "main"}],
    )
    revised = service.revise(
        expected_revision=2,
        revisions=[{"id": "implement", "content": "Implement and verify"}],
    )

    assert updated.changed is True
    assert updated.projection["ready_task_ids"] == ["implement"]
    assert revised.changed is True
    assert revised.plan.revision == 3
    events = _plan_events(store)
    assert [event.payload["operation"] for event in events] == ["create", "update", "revise"]
    assert events[1].payload["changes"] == [{"id": "inspect", "status": "completed", "owner": "main"}]
    assert events[2].payload["changes"] == [{"id": "implement", "content": "Implement and verify"}]
    assert store.load("sess_plan").task_plan == revised.plan


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda service: service.create(mode="linear", expected_revision=1, tasks=[]),
            id="create",
        ),
        pytest.param(
            lambda service: service.update(
                expected_revision=1,
                updates=[{"id": "work", "owner": "main"}],
            ),
            id="update",
        ),
        pytest.param(
            lambda service: service.revise(
                expected_revision=1,
                revisions=[{"id": "work", "content": "Work"}],
            ),
            id="revise",
        ),
    ],
)
def test_no_op_returns_projection_without_writing_event(
    tmp_path,
    mutate: Callable[[TaskPlanService], TaskPlanMutation],
) -> None:
    store, service = _service(tmp_path)
    original = service.create(
        mode="linear",
        expected_revision=0,
        tasks=[{"id": "work", "content": "Work", "owner": "main"}],
    ).plan
    before = len(_plan_events(store))

    mutation = mutate(service)

    assert mutation.changed is False
    assert mutation.plan == original
    assert mutation.projection["revision"] == 1
    assert len(_plan_events(store)) == before


def test_concurrent_services_atomically_compare_revision_and_append(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SessionStore(tmp_path)
    data_a = store.create(session_id="sess_plan")
    data_b = store.load(data_a.session_id)
    service_a = TaskPlanService(store=store, data=data_a)
    service_b = TaskPlanService(store=store, data=data_b)

    original_create_tasks = service_module.create_tasks
    rendezvous = Barrier(2)

    def synchronized_create_tasks(**kwargs):
        try:
            rendezvous.wait(timeout=0.25)
        except BrokenBarrierError:
            pass
        return original_create_tasks(**kwargs)

    monkeypatch.setattr(service_module, "create_tasks", synchronized_create_tasks)

    def create(service: TaskPlanService, task_id: str):
        try:
            return service.create(
                mode="linear",
                expected_revision=0,
                tasks=[{"id": task_id, "content": task_id}],
            )
        except TaskPlanRevisionConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: create(*item),
                ((service_a, "a"), (service_b, "b")),
            )
        )

    assert sum(isinstance(result, TaskPlanMutation) for result in results) == 1
    assert sum(isinstance(result, TaskPlanRevisionConflict) for result in results) == 1
    assert len(_plan_events(store)) == 1
    assert store.load("sess_plan").task_plan is not None


def test_invalid_batch_is_atomic_and_writes_no_event(tmp_path) -> None:
    store, service = _service(tmp_path)
    original = service.create(
        mode="dag",
        expected_revision=0,
        tasks=[
            {"id": "a", "content": "A"},
            {"id": "b", "content": "B"},
        ],
    ).plan
    before = len(_plan_events(store))

    with pytest.raises(TaskPlanCommandError, match="missing"):
        service.update(
            expected_revision=1,
            updates=[
                {"id": "a", "owner": "agent-1"},
                {"id": "b", "add_depends_on": ["missing"]},
            ],
        )

    assert len(_plan_events(store)) == before
    assert service.current() == original
