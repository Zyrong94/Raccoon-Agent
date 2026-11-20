"""`view` 工具。"""

from __future__ import annotations

from pathlib import Path

from raccoon.modules.tools.path_permissions import with_read_permission
from raccoon.modules.tools.types import Tool, ToolResult, make_error_result, make_text_result
from raccoon.modules.tools.introspection import tool_from_function
from raccoon.runtime.sandbox import PathSandbox
from raccoon.modules.permissions.sandbox_access import SandboxAccess
from raccoon.utils.text import safe_read_text


def create_view_tool(root: str | Path, *, access: SandboxAccess | None = None) -> Tool:
    """创建读取文本文件的工具。"""

    sandbox = PathSandbox(root, access=access)

    def view(path: str, start_line: int | None = None, end_line: int | None = None) -> ToolResult:
        """读取 UTF-8 文本；行号从 1 开始且含结束行，默认最多返回 50,000 字符。"""

        try:
            target = sandbox.resolve_validated(path, expect="file")
        except ValueError as exc:
            return make_error_result("view", str(exc))
        first = 1 if start_line is None else start_line
        if first < 1 or (end_line is not None and end_line < 1):
            return make_error_result("view", "start_line 和 end_line 必须大于 0")
        if end_line is not None and first > end_line:
            return make_error_result("view", "start_line 不能大于 end_line")

        try:
            lines = safe_read_text(target).splitlines()
        except UnicodeDecodeError:
            return make_error_result("view", f"文件不是 UTF-8 文本或无法作为文本读取：{path}")

        last = min(end_line if end_line is not None else len(lines), len(lines))
        selected: list[str] = []
        used = 0
        # Reserve space for the continuation marker, including large line numbers.
        marker_budget = 200 + 4 * len(str(len(lines)))
        for index in range(first - 1, last):
            rendered = f"{index + 1}: {lines[index]}"
            if used + len(rendered) + bool(selected) > 50000 - marker_budget:
                if not selected:
                    return make_error_result(
                        "view", f"第 {index + 1} 行超过单次读取上限；请用 shell 执行 Python 等程序按字符分段读取该行。",
                        path=sandbox.relative(target), start_line=index + 1,
                    )
                break
            selected.append(rendered)
            used += len(rendered) + (1 if len(selected) > 1 else 0)
        actual_start = first if selected else None
        actual_end = first + len(selected) - 1 if selected else None
        truncated = actual_end is not None and actual_end < len(lines)
        next_start = actual_end + 1 if truncated else None
        content = "\n".join(selected)
        if truncated:
            content += f"\n[本次仅返回第 {first}–{actual_end} 行，共 {len(lines)} 行；继续读取请使用 start_line={next_start}。]"

        return make_text_result(
            "view",
            content or "没有可显示内容。",
            path=sandbox.relative(target),
            start_line=actual_start,
            end_line=actual_end,
            truncated=truncated,
            total_lines=len(lines),
            next_start_line=next_start,
        )

    tool = tool_from_function(view)
    for name in ("start_line", "end_line"):
        tool.definition.parameters["properties"][name] = {"type": "integer"}
    return with_read_permission(tool, reason="读取文件需要权限检查。")
