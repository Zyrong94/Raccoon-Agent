"""Runtime file locations shared by a parent session and its subagents.

These paths describe locations only; they do not grant sandbox access.
"""

from pathlib import Path
from typing import NotRequired, TypedDict


class ThreadDataState(TypedDict):
    workspace_path: NotRequired[str | None]
    uploads_path: NotRequired[str | None]
    outputs_path: NotRequired[str | None]


def default_thread_data(
    *, store_root: Path, session_id: str, project_root: Path | None = None,
) -> ThreadDataState:
    """Describe existing storage conventions; directories are created on write."""
    return {
        "workspace_path": str(project_root.resolve()) if project_root is not None else None,
        "uploads_path": str((store_root / "attachments" / session_id).resolve()),
        "outputs_path": str((store_root / "outputs" / session_id).resolve()),
    }


def copy_thread_data(data: ThreadDataState) -> ThreadDataState:
    """Copy directory metadata, including when restored from JSONL."""
    if not isinstance(data, dict) or any(
        key not in ThreadDataState.__annotations__ or (value is not None and not isinstance(value, str))
        for key, value in data.items()
    ):
        raise ValueError("Invalid thread_data directory metadata")
    return dict(data)
