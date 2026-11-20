"""Agent-specific defaults and compatible exports for background tools."""
from __future__ import annotations
from typing import Callable
from raccoon.runtime.background import (
    BackgroundJobManager as _BackgroundJobManager,
    BackgroundJob,
    BackgroundNotification,
    BackgroundCapacityError,
    BackgroundHandleConflict,
    validate_task_alias,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
    _summarize,
    _truncate,
)
from raccoon.modules.agent.background_results import (
    build_background_notification,
    finalize_background_job,
    make_background_placeholder_result,
    render_task_notification,
)
from raccoon.modules.tools.background_controls import (
    RUN_IN_BACKGROUND_ARG,
    BACKGROUND_LABEL_ARG,
    BACKGROUND_TASK_ID_ARG,
    BACKGROUND_HANDLE_ARG,
    BACKGROUND_CONTROL_ARGS,
    DEFAULT_BACKGROUND_TOOL_NAMES,
    with_background_controls,
    with_task_handle_control,
    strip_background_controls,
    has_background_control_fields,
)


class BackgroundJobManager(_BackgroundJobManager):
    """Run jobs with the Agent's tool-result and TaskPlan completion protocol."""

    def __init__(
        self,
        *,
        max_jobs: int = 8,
        max_workers: int = 4,
        clock: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(
            max_jobs=max_jobs,
            max_workers=max_workers,
            clock=clock,
            notification_factory=build_background_notification,
            finalize_job=finalize_background_job,
        )
