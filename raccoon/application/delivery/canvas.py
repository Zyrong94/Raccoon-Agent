"""HTML publication use case and saved Canvas projection."""
import json
from pathlib import Path
from raccoon.runtime.model_types import ToolDefinition
from raccoon.modules.tools.types import Tool, make_error_result, make_text_result
from raccoon.application.delivery.artifacts import artifact_id, validate_artifact_path

def create_open_canvas_tool(*, project: Path, reports: Path, session_id: str) -> Tool:
    def open_canvas(path, title=None):
        if not isinstance(path, str) or not path.strip() or len(path) > 4096:
            return make_error_result("open_canvas", "path 必须是已存在 HTML 文件的路径")
        if title is not None and (not isinstance(title, str) or len(title) > 200):
            return make_error_result("open_canvas", "title 必须是不超过 200 字的文字")
        try:
            candidate = Path(path)
            resolved = validate_artifact_path(candidate if candidate.is_absolute() else project / candidate,
                                              project=project, reports=reports)
            if resolved.suffix.lower() not in {".html", ".htm"}:
                raise ValueError("Canvas 只支持 HTML 文件，请先使用文件工具生成页面")
        except (OSError, ValueError) as exc:
            return make_error_result("open_canvas", str(exc))
        canvas = {"artifact_id": artifact_id(session_id, resolved), "title": (title or "").strip() or resolved.name}
        return make_text_result("open_canvas", json.dumps({"opened": True, **canvas}, ensure_ascii=False),
                                path=str(resolved), canvas=canvas)

    return Tool(ToolDefinition(
        "open_canvas",
        "Open an existing HTML file in the user's side-by-side Canvas preview. "
        "Use for a standalone interactive page, visualization, or report. First create the file with write/apply_patch; "
        "then call open_canvas with its path. Later file edits refresh the open preview. "
        "Keep local CSS/JS/images beside the HTML or in its subdirectories. "
        "This tool does not create or edit files.",
        {"type": "object", "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 4096},
            "title": {"type": "string", "maxLength": 200},
        }, "required": ["path"], "additionalProperties": False}), open_canvas)

def session_canvas(events, artifacts):
    revision = None
    for event in reversed(events):
        if event.type != "tool_result":
            continue
        for part in reversed(event.payload.get("parts", [])):
            metadata = part.get("metadata") or {}
            if part.get("kind") != "tool_result" or not metadata.get("ok"):
                continue
            name = metadata.get("tool_name")
            if revision is None and name in {"write", "edit", "apply_patch", "open_canvas"}:
                revision = event.id
            if name == "open_canvas":
                canvas = (metadata.get("data") or {}).get("canvas")
                if not isinstance(canvas, dict) or canvas.get("artifact_id") not in artifacts:
                    return None
                return {**canvas, "request_id": event.id, "revision": revision}
    return None
