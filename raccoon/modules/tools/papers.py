"""Unified paper-search tool; source-specific retrieval stays in its adapter."""

from datetime import date
import json

from raccoon.modules.permissions.types import PermissionAction
from raccoon.modules.tools.arxiv import ENDPOINT, search_arxiv
from raccoon.modules.tools.types import Tool, ToolPermissionSpec, ToolResult, make_error_result
from raccoon.modules.tools.introspection import tool_from_function


def create_search_papers_tool() -> Tool:
    def search_papers(query: str, date_from: str, date_to: str, source: str = "arxiv",
                      date_type: str = "latest_submission", limit: int = 10, offset: int = 0) -> ToolResult:
        """Search official paper metadata by topic and date; currently supports arXiv latest submissions."""
        try:
            if source != "arxiv":
                raise ValueError("当前仅支持 source=arxiv")
            if date_type != "latest_submission":
                raise ValueError("arxiv 当前仅支持 date_type=latest_submission，不会替换日期语义")
            if not isinstance(query, str) or not query.strip() or len(query) > 2000:
                raise ValueError("query 必须为 1–2000 字符的 arXiv 查询表达式")
            if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or offset < 0:
                raise ValueError("limit 必须是 1–20 的整数；offset 必须为非负整数")
            start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
            if start > end:
                raise ValueError("date_from 不能晚于 date_to")
        except (TypeError, ValueError) as exc:
            return make_error_result("search_papers", str(exc))
        data = search_arxiv(query.strip(), start, end, limit, offset)
        return ToolResult(name="search_papers", ok=data["error"] is None,
                          content=json.dumps(data, ensure_ascii=False), data=data, error=data["error"])

    tool = tool_from_function(search_papers, exposure="deferred", supports_background=True)
    tool.permission = ToolPermissionSpec(action=PermissionAction.NETWORK_REQUEST,
                                         target_value=ENDPOINT, reason="查询 arXiv 官方论文元数据")
    return tool
