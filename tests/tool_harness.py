"""Probe a tool's policy in unit tests without starting an interactive Agent turn.

ASK is exposed as a snapshot for assertions, never resumed by this helper.
Live approval, cancellation and continuation use test_approval_await instead.
"""

from dataclasses import asdict
from raccoon.runtime.user_input import UserInputRequest
from raccoon.runtime.model_types import ToolCall
from raccoon.modules.permissions.types import PermissionRequest
from raccoon.modules.tools.types import ToolResult, make_text_result
from raccoon.modules.tools.permission_results import _permission_request_data

from raccoon.modules.permissions.types import PermissionDecisionKind
from raccoon.modules.tools.execution import execute_tool, validate_arguments
from raccoon.modules.tools.permissions import preflight_tool_permission
from raccoon.modules.tools.permission_results import make_permission_denied_result
from raccoon.modules.tools.types import make_error_result


def execute_test_tool(registry, name, arguments=None, *, permission_manager=None):
    tool = registry.get(name)
    if tool is None:
        return make_error_result(name, f"未知工具：{name}")
    arguments = {} if arguments is None else arguments
    invalid = validate_arguments(tool, arguments)
    if invalid is not None:
        return invalid
    preflight = preflight_tool_permission(permission_manager, tool, arguments)
    if preflight is not None:
        request, decision = preflight
        if decision.kind == PermissionDecisionKind.DENY:
            return make_permission_denied_result(tool_name=name, request=request, decision=decision)
        if decision.kind == PermissionDecisionKind.ASK:
            return make_permission_confirmation_result(
                tool_name=name, request=request,
                confirmation=permission_manager.build_confirmation(request),
            )
    return execute_tool(registry, name, arguments)


def execute_session_tool(session, call):
    error = session.tool_authorization_error(call)
    if error is not None:
        return error
    return execute_test_tool(session.tool_registry, call.name, call.arguments,
                             permission_manager=session.permission_manager)


def make_permission_confirmation_result(
    *,
    tool_name: str,
    request: PermissionRequest,
    confirmation: UserInputRequest,
    pending_tool_call: ToolCall | None = None,
) -> ToolResult:
    """创建供单元测试断言的权限请求快照，不用于 Agent 执行。"""

    data = {
        "requires_user_input": True,
        "request_type": "permission_confirmation",
        "permission_request_id": request.id,
        "question": confirmation.question,
        "options": [asdict(option) for option in confirmation.options],
        "permission_request": _permission_request_data(request),
    }
    if pending_tool_call is not None:
        data["pending_tool_call"] = {
            "id": pending_tool_call.id,
            "name": pending_tool_call.name,
            "arguments": pending_tool_call.arguments,
        }
    return make_text_result(tool_name, confirmation.question, **data)


