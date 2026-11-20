"""In-process jobs, cancellation, ownership and completion subscriptions."""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from contextvars import copy_context
from copy import deepcopy
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
)
from uuid import uuid4

from raccoon.runtime.cancellation import CancellationToken, cancellation_context

if TYPE_CHECKING:
    from raccoon.modules.tools.types import ToolResult

_HANDLE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_RESERVED_HANDLE_PATTERN = re.compile(r"(?:task-(?:[0-9a-f]{8}|[0-9a-f]{32})-|bg_)[0-9]{4,}")
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
_SUMMARY_PREVIEW_LIMIT = 2000

@dataclass
class BackgroundNotification:
    """一次后台任务完成后要回喂给模型的结构化摘要。"""

    job_id: str
    tool_name: str
    status: str
    summary: str
    ok: bool
    session_id: str | None = None
    label: str | None = None
    task_id: str | None = None
    observed_revision: int | None = None
    task_plan_completion: str | None = None
    kind: str = "tool"
    run_id: str | None = None
    tool_call_id: str | None = None
    result: dict[str, Any] | None = None
    handle: str | None = None


@dataclass(slots=True)
class BackgroundJob:
    """后台任务的运行期记录。"""

    id: str
    tool_name: str
    session_id: str | None = None
    label: str | None = None
    task_id: str | None = None
    observed_revision: int | None = None
    status: str = STATUS_RUNNING
    result: ToolResult | None = None
    error: str | None = None
    cancel_requested: bool = False
    created_at: float = 0.0
    token: CancellationToken = field(default_factory=CancellationToken)
    on_completed: Callable[["BackgroundJob"], str | None] | None = field(default=None, repr=False)
    task_plan_completion: str | None = None
    delivery_error: str | None = None
    completion_snapshot: BackgroundNotification | None = field(default=None, repr=False)
    final_notification: BackgroundNotification | None = field(default=None, repr=False)
    finalizing: bool = field(default=False, repr=False)
    tool_call_id: str | None = None
    completion_run_id: str | None = None
    handle: str | None = None
    run_id: str | None = None

    def snapshot(self) -> dict[str, Any]:
        """给 background_status 工具用的可读快照。"""

        summary = _summarize(self) if self.status != STATUS_RUNNING else None
        return {
            "job_id": self.id,
            "handle": self.handle,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "label": self.label,
            "task_id": self.task_id,
            "observed_revision": self.observed_revision,
            "status": self.status,
            "ok": None if self.result is None else self.status == STATUS_COMPLETED,
            "error": self.error,
            "summary": summary,
            "cancel_requested": self.cancel_requested,
            **({"delivery_error": self.delivery_error} if self.delivery_error is not None else {}),
        }


class BackgroundJobManager:
    """内存态后台任务管理器。

    - 用 `ThreadPoolExecutor` 跑同步工具执行函数（Phase 1 只接非写入工具）。
    - id 递增且确定（`bg_0001`...），方便测试与人读。
    - 完成的任务进入 `_completed` 队列，等 AgentRun 在下次 provider 请求前收集，
      转成 `<task_notification>` 用户消息。
    """

    def __init__(
        self,
        *,
        max_jobs: int = 8,
        max_workers: int = 4,
        clock: Callable[[], float] | None = None,
        notification_factory: Callable[[BackgroundJob, str], BackgroundNotification],
        finalize_job: Callable[[BackgroundJob], None],
    ) -> None:
        self._notification_factory = notification_factory
        self._finalize_job = finalize_job
        self.max_jobs = max_jobs
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="fc-bg")
        self._lock = threading.Lock()
        self._jobs: dict[str, BackgroundJob] = {}
        self._futures: dict[str, Future] = {}
        self._completed: deque[BackgroundJob] = deque()
        self._counter = 0
        self._handles: dict[str, str] = {}
        self._aliases: dict[tuple[str | None, str], tuple[str, object | None]] = {}
        self._clock = clock or time.monotonic
        run_uuid = uuid4()
        self._run_id = str(run_uuid)
        self._handle_prefix = f"task-{run_uuid.hex}-"
        self._completion_sinks: dict[str, Callable[[BackgroundNotification], None]] = {}

    @property
    def run_id(self) -> str:
        return self._run_id

    def bind_completion_sink(self, session_id: str, sink: Callable[[BackgroundNotification], None]) -> Callable[[], None]:
        """Bind one delivery owner; publication never runs TaskPlan callbacks."""
        if not isinstance(session_id, str) or not session_id.strip() or not callable(sink):
            raise ValueError("completion sink requires a session_id and callable")
        with self._lock:
            if session_id in self._completion_sinks:
                raise ValueError("session already has a completion sink")
            self._completion_sinks[session_id] = sink
        self.replay_completed(session_id)

        def detach() -> None:
            with self._lock:
                if self._completion_sinks.get(session_id) is sink:
                    self._completion_sinks.pop(session_id)

        return detach

    def replay_completed(self, session_id: str) -> None:
        with self._lock:
            jobs = [job for job in self._completed if job.session_id == session_id]
        for job in jobs:
            self._emit_completion(job)

    def _emit_completion(self, job: BackgroundJob) -> None:
        with self._lock:
            sink = self._completion_sinks.get(job.session_id)
            snapshot = deepcopy(job.completion_snapshot)
        if sink is None or snapshot is None:
            return
        try:
            sink(snapshot)
        except Exception as exc:
            with self._lock:
                job.delivery_error = str(exc) or type(exc).__name__
        else:
            with self._lock:
                job.delivery_error = None

    def start(
        self,
        func: Callable[[], ToolResult],
        *,
        session_id: str | None = None,
        tool_name: str,
        label: str | None = None,
        task_id: str | None = None,
        observed_revision: int | None = None,
        on_completed: Callable[[BackgroundJob], str | None] | None = None,
        tool_call_id: str | None = None,
        handle: str | None = None,
        alias_scope: object | None = None,
    ) -> BackgroundJob:
        """登记并调度一个后台任务。

        `func` 必须是一个无参、返回 `ToolResult` 的可调用；executor 通常把它包成
        “执行剥离控制字段后的 tool_call”。若已达并发上限会抛 `BackgroundCapacityError`，
        由调用方转成普通错误结果告诉模型。`handle` 是模型在同一响应内使用的可选别名，
        每个任务都有包含完整运行标识的稳定句柄。
        """

        with self._lock:
            active = sum(1 for job in self._jobs.values() if job.status == STATUS_RUNNING)
            if active >= self.max_jobs:
                raise BackgroundCapacityError(self.max_jobs)
            if handle is not None:
                validate_task_alias(handle)
                if (session_id, handle) in self._aliases:
                    raise BackgroundHandleConflict(f"task handle {handle!r} is already bound to another job")
            self._counter += 1
            job_id = f"bg_{self._counter:04d}"
            stable_handle = f"{self._handle_prefix}{self._counter:04d}"
            job = BackgroundJob(
                id=job_id,
                session_id=session_id,
                tool_name=tool_name,
                label=label,
                task_id=task_id,
                observed_revision=observed_revision,
                created_at=self._clock(),
                on_completed=on_completed,
                tool_call_id=tool_call_id,
                handle=stable_handle,
                run_id=self.run_id,
            )
            if handle is not None:
                self._aliases[(session_id, handle)] = (job_id, alias_scope)
            self._handles[stable_handle] = job_id
            self._jobs[job_id] = job
            future = self._executor.submit(self._run, job, func, copy_context())
            self._futures[job_id] = future
        return job

    def resolve(self, handle: str, *, session_id: str, alias_scope: object | None = None) -> BackgroundJob | None:
        """Resolve a model-facing handle to a live job in this run and session.

        A handle from another manager run (for example after a restart that reuses
        ``bg_0001``), another session, or one that no longer maps to the recorded
        job returns ``None``.
        """

        if not isinstance(handle, str):
            return None
        with self._lock:
            job_id = self._handles.get(handle)
            if job_id is not None:
                job = self._jobs.get(job_id)
                if job is None or job.handle != handle:
                    return None
            else:
                alias = self._aliases.get((session_id, handle))
                if alias_scope is None or alias is None or alias[1] is not alias_scope:
                    return None
                job = self._jobs.get(alias[0])
            if job is None or job.session_id != session_id or job.run_id != self.run_id:
                return None
            return job

    def publish_completed(self, *, job_id: str, session_id: str, tool_name: str, result: ToolResult, completion_run_id: str | None = None) -> BackgroundJob:
        """Deliver scheduler-owned work through the ordinary completion sink, without a worker."""
        with self._lock:
            existing = self._jobs.get(job_id)
            if existing is not None:
                return existing
            job = BackgroundJob(id=job_id, session_id=session_id, tool_name=tool_name, created_at=self._clock())
            job.completion_run_id = completion_run_id
            self._jobs[job_id] = job
        self._finish(job, result=result, error=None)
        return job

    def owns_completion(self, *, run_id: str, job_id: str, session_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job is not None and job.session_id == session_id and (job.completion_run_id or self.run_id) == run_id)

    def _run(self, job: BackgroundJob, func: Callable[[], ToolResult], context) -> None:
        try:
            def execute():
                with cancellation_context(job.token):
                    return func()
            # Completion callbacks run outside the original tool's permission context.
            result = context.run(execute)
        except Exception as exc:  # noqa: BLE001 - 后台失败必须转成 notification，而不是吞掉
            self._finish(job, result=None, error=f"后台任务执行失败：{exc}")
            return
        self._finish(job, result=result, error=None)

    def _finish(self, job: BackgroundJob, *, result: ToolResult | None, error: str | None) -> None:
        with self._lock:
            if job.status == STATUS_CANCELLED:
                # 已被显式取消：保留取消状态，但仍记录迟到结果，避免悬空。
                job.result = result
                self._futures.pop(job.id, None)
                return
            job.result = result
            job.error = error
            if job.cancel_requested:
                job.status = STATUS_CANCELLED
            elif error is not None:
                job.status = STATUS_FAILED
            elif result is not None and not result.ok:
                job.status = STATUS_FAILED
            else:
                job.status = STATUS_COMPLETED
            job.completion_snapshot = self._notification_for(job)
            self._completed.append(job)
        self._emit_completion(job)
        with self._lock:
            self._futures.pop(job.id, None)

    def _notification_for(self, job: BackgroundJob) -> BackgroundNotification:
        return self._notification_factory(job, self.run_id)

    # -- 查询/收集 --------------------------------------------------------

    def collect_completed(self, *, session_id: str | None = None) -> list[BackgroundNotification]:
        """Finalize and return completed jobs from a serialized consumer point.

        Worker threads only determine the tool's final status.  A linked TaskPlan
        completion callback runs here, immediately before the caller persists the
        notification, so it never races normal session event writes.
        """

        with self._lock:
            jobs: list[BackgroundJob] = []
            remaining: deque[BackgroundJob] = deque()
            for job in self._completed:
                if (session_id is None or job.session_id == session_id) and job.session_id not in self._completion_sinks:
                    jobs.append(job)
                else:
                    remaining.append(job)
            self._completed = remaining
        notifications: list[BackgroundNotification] = []
        for job in jobs:
            note = self.finalize_completed(job.id, session_id=job.session_id)
            if note is not None:
                notifications.append(note)
        return notifications

    def finalize_completed(self, job_id: str, *, session_id: str | None) -> BackgroundNotification | None:
        """Finalize once, only from the serialized owner-session consumer."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.session_id != session_id or job.status == STATUS_RUNNING:
                return None
            if job.final_notification is not None:
                return deepcopy(job.final_notification)
            if job.finalizing:
                raise RuntimeError("background completion is already being finalized")
            job.finalizing = True
        try:
            self._finalize_job(job)
        except BaseException:
            with self._lock:
                job.finalizing = False
            raise
        with self._lock:
            job.final_notification = self._notification_for(job)
            job.finalizing = False
            self._completed = deque(item for item in self._completed if item.id != job_id)
            return deepcopy(job.final_notification)


    def get(self, job_id: str, *, session_id: str | None = None) -> BackgroundJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or (session_id is not None and job.session_id != session_id):
                return None
            return job

    def list(self, *, session_id: str | None = None) -> list[BackgroundJob]:
        with self._lock:
            return [job for job in self._jobs.values() if session_id is None or job.session_id == session_id]

    def cancel(self, job_id: str, *, session_id: str | None = None) -> BackgroundJob | None:
        """尽力取消一个后台任务。

        - 还没开始执行：直接 `future.cancel()`，标记 cancelled 并产出一条通知。
        - 已在运行：设置协作取消 token；能否真正停下取决于工具是否检查取消。
        """

        publish = False
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or (session_id is not None and job.session_id != session_id):
                return None
            if job.status != STATUS_RUNNING:
                return job
            future = self._futures.get(job_id)
            if future is not None and future.cancel():
                job.status = STATUS_CANCELLED
                self._futures.pop(job_id, None)
                job.completion_snapshot = self._notification_for(job)
                self._completed.append(job)
                publish = True
            else:
                job.cancel_requested = True
                job.token.cancel()
        if publish:
            self._emit_completion(job)
        return job

    def wait(self, timeout: float | None = None) -> bool:
        """阻塞直到当前已知的 future 全部结束。主要给测试用。

        返回是否在超时前全部完成。
        """

        with self._lock:
            futures = list(self._futures.values())
        if not futures:
            return True
        _, not_done = futures_wait(futures, timeout=timeout)
        return not not_done

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


class BackgroundCapacityError(RuntimeError):
    """后台任务数量已达上限。"""

    def __init__(self, max_jobs: int) -> None:
        super().__init__(f"后台任务已达上限（{max_jobs}）。请等待现有任务完成或取消后再试。")
        self.max_jobs = max_jobs


class BackgroundHandleConflict(ValueError):
    """模型的 task handle 非法或已被同一运行占用。"""


def validate_task_alias(handle: str) -> None:
    if not isinstance(handle, str) or _HANDLE_PATTERN.fullmatch(handle) is None:
        raise BackgroundHandleConflict(
            f"invalid task handle {handle!r}; use 1-64 characters from [A-Za-z0-9._-]"
        )
    if _RESERVED_HANDLE_PATTERN.fullmatch(handle) is not None:
        raise BackgroundHandleConflict(f"task handle {handle!r} is reserved for runtime-generated identifiers")


def _summarize(job: BackgroundJob) -> str:
    if job.error is not None:
        return _truncate(job.error)
    if job.result is None:
        return "(no output)"
    content = job.result.content or "(empty result)"
    return _truncate(content)


def _truncate(text: str) -> str:
    if len(text) <= _SUMMARY_PREVIEW_LIMIT:
        return text
    return text[:_SUMMARY_PREVIEW_LIMIT] + "\n…(truncated; use background_status for the full result)"
