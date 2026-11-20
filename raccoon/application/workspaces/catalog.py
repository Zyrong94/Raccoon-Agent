"""Cache read-only catalog projections until their source file changes."""
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path

from raccoon.modules.sessions.catalog import is_safe_session_id, record_from_path


def file_version(path: Path):
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino


@lru_cache(maxsize=512)
def _record(path, version):
    return record_from_path(Path(path))


def catalog_record(path: Path):
    return _record(str(path), file_version(path))


def main_session_id(data_root: Path) -> str | None:
    pointer = data_root / "channels" / "main_session.json"
    if not pointer.exists():
        return None
    data = json.loads(pointer.read_text(encoding="utf-8"))
    identity = data.get("session_id") if isinstance(data, dict) else None
    if not isinstance(identity, str) or not is_safe_session_id(identity):
        raise ValueError("invalid main session pointer")
    return identity


def main_id(root):
    try:
        return main_session_id(root)
    except (OSError, ValueError):
        return None


def workspace_id(path):
    return hashlib.sha256(os.path.normcase(str(Path(path).resolve())).encode()).hexdigest()[:16]
