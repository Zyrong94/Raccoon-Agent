"""Model-visible controls for background tool execution."""
from __future__ import annotations
from copy import deepcopy
from typing import Any
from raccoon.runtime.model_types import ToolDefinition

RUN_IN_BACKGROUND_ARG = "run_in_background"
BACKGROUND_LABEL_ARG = "background_label"
BACKGROUND_TASK_ID_ARG = "task_id"
BACKGROUND_HANDLE_ARG = "task_handle"
BACKGROUND_CONTROL_ARGS = frozenset(
    {RUN_IN_BACKGROUND_ARG, BACKGROUND_LABEL_ARG, BACKGROUND_TASK_ID_ARG, BACKGROUND_HANDLE_ARG}
)

_HANDLE_DESCRIPTION = (
    "Optional alias for wait_for_tasks in this same response. Use the runtime handle "
    "shown by background_status in later responses."
)

def with_background_controls(definition: ToolDefinition) -> ToolDefinition:
    """返回一个附加后台控制字段（含可选 TaskPlan ``task_id``）的 schema 副本。

    只增字段、不改原定义，避免污染 registry 里存的工具。executor 会在真正调用
    executor 前把这些控制面字段剥掉。
    """

    parameters: dict[str, Any] = deepcopy(definition.parameters) if definition.parameters else {}
    parameters.setdefault("type", "object")
    properties = parameters.setdefault("properties", {})
    if not isinstance(properties, dict):  # 防御：异常 schema 不做增强
        return definition
    properties[RUN_IN_BACKGROUND_ARG] = {
        "type": "boolean",
        "description": (
            "Set true to run this tool asynchronously in the background. You immediately "
            "get a job id placeholder; the real result arrives later as a "
            "<task_notification>. Use for slow, independent work that should not block "
            "the next step."
        ),
    }
    properties[BACKGROUND_LABEL_ARG] = {
        "type": "string",
        "description": "Optional short label to recognise this background job in status/notifications.",
    }
    properties[BACKGROUND_TASK_ID_ARG] = {
        "type": "string",
        "description": (
            "Optional TaskPlan task ID to associate with this background job. The task must already " "exist in the current plan; successful completion advances that same task when it remains active."
        ),
    }
    properties.setdefault(BACKGROUND_HANDLE_ARG, {
        "type": "string",
        "description": _HANDLE_DESCRIPTION,
    })
    return ToolDefinition(
        name=definition.name,
        description=definition.description,
        parameters=parameters,
    )


def with_task_handle_control(definition: ToolDefinition) -> ToolDefinition:
    """Add only the ``task_handle`` control field (native async tools keep no run flag)."""

    parameters: dict[str, Any] = deepcopy(definition.parameters) if definition.parameters else {}
    parameters.setdefault("type", "object")
    properties = parameters.setdefault("properties", {})
    if not isinstance(properties, dict):  # 防御：异常 schema 不做增强
        return definition
    properties.setdefault(BACKGROUND_HANDLE_ARG, {
        "type": "string",
        "description": _HANDLE_DESCRIPTION,
    })
    return ToolDefinition(
        name=definition.name,
        description=definition.description,
        parameters=parameters,
    )


def strip_background_controls(
    arguments: Any, *, preserve_task_handle: bool = False,
) -> tuple[dict[str, Any], bool, str | None, str | None, str | None]:
    """从工具参数里分离控制面字段。

    返回 `(clean_arguments, run_in_background, label, task_id, task_handle)`。非 dict 参数
    原样透传，`run_in_background=False`，让调用方走普通同步路径。
    """

    if not isinstance(arguments, dict):
        return {} if arguments is None else arguments, False, None, None, None
    controls = BACKGROUND_CONTROL_ARGS - {BACKGROUND_HANDLE_ARG} if preserve_task_handle else BACKGROUND_CONTROL_ARGS
    clean = {key: value for key, value in arguments.items() if key not in controls}
    run_in_background = bool(arguments.get(RUN_IN_BACKGROUND_ARG))
    raw_label = arguments.get(BACKGROUND_LABEL_ARG)
    raw_task_id = arguments.get(BACKGROUND_TASK_ID_ARG)
    raw_handle = arguments.get(BACKGROUND_HANDLE_ARG)
    label = str(raw_label).strip() if isinstance(raw_label, str) and raw_label.strip() else None
    task_id = str(raw_task_id).strip() if isinstance(raw_task_id, str) and raw_task_id.strip() else None
    # An explicit invalid value is distinct from omission.
    handle = None
    if BACKGROUND_HANDLE_ARG in controls and BACKGROUND_HANDLE_ARG in arguments:
        handle = raw_handle if isinstance(raw_handle, str) else ""
    return clean, run_in_background, label, task_id, handle


def has_background_control_fields(arguments: Any) -> bool:
    """判断参数里是否带有任一控制面字段（不管真假值）。"""

    return isinstance(arguments, dict) and any(key in arguments for key in BACKGROUND_CONTROL_ARGS)
