"""Read the current session's clock on demand."""
import json
from datetime import UTC
from zoneinfo import ZoneInfo

from raccoon.modules.context import temporal
from raccoon.modules.tools.types import Tool, ToolResult, make_text_result
from raccoon.modules.tools.introspection import tool_from_function


def create_session_status_tool(session_id: str, timezone: str) -> Tool:
    def session_status() -> ToolResult:
        """需要精确当前时间时调用；日期和时区足够时跳过。"""
        now = temporal.current_time().astimezone(ZoneInfo(timezone))
        data = {'session_id': session_id, 'timezone': timezone,
                'current_time': now.isoformat(timespec='seconds'),
                'utc_time': now.astimezone(UTC).isoformat(timespec='seconds')}
        return make_text_result('session_status', json.dumps(data, ensure_ascii=False), **data)

    return tool_from_function(session_status, exposure="deferred")
