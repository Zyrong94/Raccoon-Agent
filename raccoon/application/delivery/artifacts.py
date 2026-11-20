"""Session-linked deliverables; never expose an arbitrary filesystem browser."""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from collections.abc import Iterable

from raccoon.modules.sessions.events import SessionEvent

MEDIA_MIME_TYPES = {
    ".apng": "image/apng", ".avif": "image/avif", ".bmp": "image/bmp", ".ico": "image/vnd.microsoft.icon",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".aac": "audio/aac",
    ".m4a": "audio/mp4", ".flac": "audio/flac", ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".m4v": "video/mp4", ".webm": "video/webm",
}
OFFICE_MIME_TYPES = {
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
PREVIEW_SUFFIXES = {
    ".md", ".txt", ".html", ".htm", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".csv", ".json", ".py", ".js", ".ts", ".tsx", ".css", ".yaml", ".toml",
    *MEDIA_MIME_TYPES, *OFFICE_MIME_TYPES,
}



def artifact_id(session_id: str, path: Path) -> str:
    return hashlib.sha256(f"{session_id}:{path}".encode()).hexdigest()[:24]


def validate_artifact_path(path: Path, *, project: Path, reports: Path, suffixes=PREVIEW_SUFFIXES) -> Path:
    resolved = path.resolve()
    if not (resolved.is_relative_to(project.resolve()) or resolved.is_relative_to(reports.resolve())):
        raise ValueError("产物不在项目或报告目录内")
    store_root = reports.resolve().parent
    if resolved.is_relative_to(store_root) and not resolved.is_relative_to(reports.resolve()):
        store_relative = resolved.relative_to(store_root)
        if store_root != project.resolve() or (store_relative.parts and store_relative.parts[0] in {
            "sessions", "archives", "agents", "processes", "permissions.json", "model_state.json", "session_index.json",
        }):
            raise ValueError("不允许预览运行时内部数据")
    # 报告目录可能位于 .raccoon 内，只允许 reports 子树，不暴露会话和凭据。
    relative = resolved.relative_to(reports.resolve()) if resolved.is_relative_to(reports.resolve()) else resolved.relative_to(project.resolve())
    if any(part.startswith(".") or part.lower() in {"node_modules", "__pycache__"} for part in relative.parts):
        raise ValueError("不允许预览隐藏文件或内部数据")
    if resolved.suffix.lower() not in suffixes or not resolved.is_file():
        raise ValueError("文件不可预览或不存在")
    return resolved


def session_artifacts(events: Iterable[SessionEvent], *, session_id: str, project: Path, reports: Path) -> dict[str, dict]:
    found = {}
    # Use original successful results: compaction can replace the context projection.
    for event in events:
        if event.type != "tool_result":
            continue
        for part in event.payload.get("parts", []):
            metadata = part.get("metadata") or {}
            if part.get("kind") != "tool_result" or not metadata.get("ok"):
                continue
            data = metadata.get("data") or {}
            candidates = [data.get("report_path")]
            if metadata.get("tool_name") in {"write", "edit", "open_canvas"}:
                candidates.append(data.get("path"))
            if metadata.get("tool_name") == "apply_patch" and not data.get("dry_run"):
                candidates.extend(data.get("changed_files") or [])
            for candidate in candidates:
                if not isinstance(candidate, str) or not candidate:
                    continue
                path = Path(candidate)
                if not path.is_absolute():
                    path = project / path
                try:
                    path = validate_artifact_path(path, project=project, reports=reports)
                    stat = path.stat()
                    size = stat.st_size
                except (ValueError, OSError):
                    continue
                identity = artifact_id(session_id, path)
                found[identity] = {
                    "id": identity, "name": path.name, "size": size, "suffix": path.suffix.lower(),
                    "mime": (MEDIA_MIME_TYPES.get(path.suffix.lower()) or OFFICE_MIME_TYPES.get(path.suffix.lower())
                             or mimetypes.guess_type(path.name)[0] or "application/octet-stream"),
                    "path": path, "modified_ns": stat.st_mtime_ns,
                }
    return found
