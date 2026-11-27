"""JSON 辅助函数测试。"""

from __future__ import annotations

from raccoon.utils.json_utils import dumps_json


def test_dumps_json_uses_compact_non_ascii_json():
    assert dumps_json({"path": "中文.md"}) == '{"path":"中文.md"}'
