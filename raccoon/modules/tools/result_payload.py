"""JSON-safe snapshots of tool results for durable asynchronous delivery."""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import asdict
import json
from typing import Any

from raccoon.modules.tools.types import MAX_TOOL_IMAGE_BYTES, ToolImage, ToolResult


def result_to_payload(result: ToolResult) -> dict[str, Any]:
    payload = asdict(result)
    for image in payload["images"]:
        image["data"] = base64.b64encode(image["data"]).decode("ascii")
    # Fail at publication rather than silently dropping non-JSON evidence.
    return json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))


def result_from_payload(payload: dict[str, Any]) -> ToolResult:
    fields = deepcopy(payload)
    if (set(fields) != {"name", "ok", "content", "data", "error", "images"}
            or not isinstance(fields["name"], str) or not fields["name"].strip()
            or not isinstance(fields["ok"], bool) or not isinstance(fields["content"], str)
            or not isinstance(fields["data"], dict) or not isinstance(fields["images"], list)
            or (fields["error"] is not None and not isinstance(fields["error"], str))):
        raise ValueError("invalid tool result snapshot")
    images = []
    for raw in fields.pop("images"):
        if not isinstance(raw, dict) or not isinstance(raw.get("data"), str):
            raise ValueError("invalid tool image snapshot")
        raw["data"] = base64.b64decode(raw["data"], validate=True)
        if len(raw["data"]) > MAX_TOOL_IMAGE_BYTES:
            raise ValueError("tool image snapshot exceeds size limit")
        images.append(ToolImage(**raw))
    return ToolResult(**fields, images=images)
