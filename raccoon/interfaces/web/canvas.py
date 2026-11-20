"""Web-only HTML publication; files and saved results remain the source of truth."""

from pathlib import Path

from raccoon.application.delivery.artifacts import artifact_id, validate_artifact_path

CANVAS_ASSET_TYPES = {
    ".html": "text/html", ".htm": "text/html", ".css": "text/css",
    ".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".wasm": "application/wasm",
}


def canvas_asset_path(entry: Path, asset_path: str, *, project: Path, reports: Path) -> Path:
    parts = asset_path.replace("\\", "/").split("/")
    if any(not part or part in {".", ".."} or ":" in part for part in parts):
        raise ValueError("无效 Canvas 资源路径")
    root = entry.parent.resolve()
    candidate = root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Canvas 资源不在页面目录内")
    return validate_artifact_path(candidate, project=project, reports=reports, suffixes=CANVAS_ASSET_TYPES)


