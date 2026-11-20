"""将工具参数转换成权限请求；不包装工具目录。"""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from raccoon.modules.permissions.manager import PermissionManager
from raccoon.modules.permissions.types import PermissionDecision, PermissionDecisionKind, PermissionRequest
from raccoon.modules.tools.types import Tool, ToolPermissionSpec


def preflight_tool_permission(
    manager: PermissionManager | None, tool: Tool, arguments: dict[str, Any],
) -> tuple[PermissionRequest, PermissionDecision] | None:
    if manager is None or tool.permission is None:
        return None
    try:
        request = permission_request_for_tool(tool, arguments)
    except ValueError as error:
        request = PermissionRequest(
            id=f"perm_{tool.name}_invalid", action=tool.permission.action,
            target="", reason=str(error), metadata={"tool_name": tool.name, "arguments": dict(arguments)},
        )
        return request, PermissionDecision(kind=PermissionDecisionKind.DENY, reason=str(error))
    request = manager.normalize_request(request)
    return request, manager.preflight(request)


def permission_request_for_tool(tool: Tool, arguments: dict[str, Any]) -> PermissionRequest:
    """根据工具声明和调用参数构造权限请求。"""

    spec = tool.permission
    if spec is None:
        raise ValueError(f"工具没有权限声明：{tool.name}")

    target = _target_from_arguments(spec, arguments)
    cwd = _cwd_from_arguments(spec, arguments)
    request_id = _permission_request_id(tool.name, arguments)
    return PermissionRequest(
        id=request_id,
        action=spec.action,
        target=target,
        reason=spec.reason or f"工具 {tool.name} 请求 {spec.action.value} 权限。",
        cwd=cwd,
        metadata={
            "tool_name": tool.name,
            "arguments": dict(arguments),
            "allow_always": spec.allow_always,
        },
    )


def _target_from_arguments(spec: ToolPermissionSpec, arguments: dict[str, Any]) -> str:
    if spec.target_builder is not None:
        return spec.target_builder(arguments)
    if spec.target_value is not None:
        return spec.target_value
    if spec.target_arg is None:
        return ""
    if spec.target_arg not in arguments:
        raise ValueError(f"权限声明缺少目标参数：{spec.target_arg}")
    return str(arguments[spec.target_arg])


def _cwd_from_arguments(spec: ToolPermissionSpec, arguments: dict[str, Any]) -> Path | None:
    if spec.cwd_arg is None:
        return None
    raw = arguments.get(spec.cwd_arg)
    if raw in (None, ""):
        return None
    return Path(str(raw))


def _permission_request_id(tool_name: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps(
        {"tool": tool_name, "arguments": arguments},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return f"perm_{tool_name}_{sha256(payload.encode('utf-8')).hexdigest()[:12]}"
