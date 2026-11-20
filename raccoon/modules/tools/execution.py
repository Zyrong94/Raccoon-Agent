"""同步工具的调用边界，供异步协调层放入工作线程执行。"""

from __future__ import annotations

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from referencing import Registry
from referencing.exceptions import Unresolvable

from raccoon.runtime.cancellation import AgentCancelledError
from raccoon.modules.tools.registry import ToolRegistry
from raccoon.modules.tools.types import Tool, ToolResult, make_error_result


def validate_arguments(tool: Tool, arguments) -> ToolResult | None:
    if not isinstance(arguments, dict):
        return make_error_result(tool.name, "工具参数必须是 JSON 对象。")
    try:
        # 外部 schema 引用必须已在本地解析；参数校验不能隐式发网络请求。
        Draft202012Validator(tool.definition.parameters or {"type": "object"}, registry=Registry()).validate(arguments)
    except (ValidationError, SchemaError, Unresolvable) as error:
        message = error.message if isinstance(error, (ValidationError, SchemaError)) else str(error)
        return make_error_result(tool.name, f"工具参数或定义无效：{message}")
    return None


def execute_tool(registry: ToolRegistry, name: str, arguments=None) -> ToolResult:
    """内部执行函数。模型调用必须先经过会话授权和执行流程。"""
    tool = registry.get(name)
    if tool is None:
        return make_error_result(name, f"未知工具：{name}")
    arguments = {} if arguments is None else arguments
    invalid = validate_arguments(tool, arguments)
    if invalid is not None:
        return invalid
    try:
        return tool.executor(**arguments)
    except AgentCancelledError:
        raise
    except Exception as error:
        return make_error_result(name, f"工具执行失败：{error}")
