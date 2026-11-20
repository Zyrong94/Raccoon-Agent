"""Session-backed mutation boundary for canonical task plans."""

from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import portalocker

from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.sessions.models import SessionData
from raccoon.modules.planning.models import TaskPlan
from raccoon.modules.planning.projection import project_plan
from raccoon.modules.planning.reducer import (
    ReductionResult,
    TaskPlanCommandError,
    create_tasks,
    revise_tasks,
    update_tasks,
)

_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}


@dataclass(frozen=True, slots=True)
class TaskPlanMutation:
    """Canonical state and derived view produced by one plan command."""

    plan: TaskPlan
    projection: dict[str, object]
    changed: bool
    changes: tuple[dict[str, object], ...] = ()


class TaskPlanService:
    """Apply task-plan reducers against the latest replayed session state."""

    def __init__(
        self,
        *,
        store: SessionStore,
        data: SessionData,
    ) -> None:
        self._store = store
        self._data = data

    def current(self) -> TaskPlan | None:
        """Return the authoritative plan rebuilt from the event log."""

        self._store.refresh(self._data)
        return self._data.task_plan

    def create(
        self,
        *,
        mode: str,
        expected_revision: int,
        tasks: object,
        start_new_plan: bool = False,
    ) -> TaskPlanMutation:
        with self._mutation_lock():
            current_plan = self.current()
            result = create_tasks(
                current_plan=current_plan,
                expected_revision=expected_revision,
                mode=mode,
                tasks=tasks,
                start_new_plan=start_new_plan,
            )
            return self._finish(
                operation="create",
                previous_revision=current_plan.revision if current_plan is not None else 0,
                result=result,
            )

    def update(
        self,
        *,
        expected_revision: int,
        updates: object,
    ) -> TaskPlanMutation:
        with self._mutation_lock():
            current_plan = self._require_current_plan("update")
            result = update_tasks(
                plan=current_plan,
                expected_revision=expected_revision,
                updates=updates,
            )
            return self._finish(
                operation="update",
                previous_revision=current_plan.revision,
                result=result,
            )

    def revise(
        self,
        *,
        expected_revision: int,
        revisions: object,
    ) -> TaskPlanMutation:
        with self._mutation_lock():
            current_plan = self._require_current_plan("revise")
            result = revise_tasks(
                plan=current_plan,
                expected_revision=expected_revision,
                revisions=revisions,
            )
            return self._finish(
                operation="revise",
                previous_revision=current_plan.revision,
                result=result,
            )

    @contextmanager
    def _mutation_lock(self) -> Iterator[None]:
        session_key = str((self._store.sessions_dir / f"{self._data.session_id}.jsonl").resolve())
        with _THREAD_LOCKS_GUARD:
            thread_lock = _THREAD_LOCKS.setdefault(session_key, threading.RLock())

        digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()
        lock_dir = Path(self._store.root) / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"task-plan-{digest}.lock"

        with thread_lock, portalocker.Lock(lock_path, mode="a+b", flags=portalocker.LOCK_EX):
            yield

    def _require_current_plan(self, operation: str) -> TaskPlan:
        plan = self.current()
        if plan is None:
            raise TaskPlanCommandError(f"cannot {operation}: no current task plan; create one first")
        return plan

    def _finish(
        self,
        *,
        operation: str,
        previous_revision: int,
        result: ReductionResult,
    ) -> TaskPlanMutation:
        if result.changed:
            self._store.append_task_plan_updated(self._data,
                previous_revision=previous_revision,
                operation=operation,
                changes=result.changes,
                snapshot=result.plan,
            )
        return TaskPlanMutation(
            plan=result.plan,
            projection=project_plan(result.plan),
            changed=result.changed,
            changes=result.changes,
        )
