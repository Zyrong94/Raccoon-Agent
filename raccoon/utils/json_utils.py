"""项目内部共用的 JSON 辅助函数。"""

from __future__ import annotations

import json
from typing import Any


def dumps_json(value: Any) -> str:
    """把 Python 对象序列化成紧凑 JSON 字符串。"""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads_json(value: str) -> Any:
    """把 JSON 字符串解析成 Python 对象；解析失败时抛出 ValueError。"""

    return json.loads(value)
