"""Remove a single conversation and its owned data, never project deliverables."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat

from raccoon.modules.sessions.catalog import is_safe_session_id


def _check_path(root: Path, path: Path) -> None:
    if not path.resolve().is_relative_to(root) or path == root:
        raise ValueError('会话数据不在指定的数据目录内')
    cursor = path
    while cursor != root:
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            cursor = cursor.parent
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('会话数据不能使用符号链接或目录联接')
        cursor = cursor.parent


def delete_session_data(root: Path, session_id: str) -> None:
    if not is_safe_session_id(session_id):
        raise ValueError('无效会话ID')
    root = root.resolve()
    log = root/'sessions'/f'{session_id}.jsonl'
    targets = [root/'attachments'/session_id]
    apps = root/'official-mcp-apps'
    _check_path(root, apps)
    if apps.exists():
        for workspace in apps.iterdir():
            _check_path(root, workspace)
            if workspace.is_dir():
                targets.append(workspace/session_id)
    # Validate every deletion target (including descendants) before removing anything.
    _check_path(root, log)
    if not log.is_file():
        raise ValueError('会话记录不存在')
    for path in targets:
        _check_path(root, path)
        if not path.exists():
            continue
        if not path.is_dir():
            raise ValueError('会话数据类型异常，无法删除')
        for current, dirs, files in os.walk(path, followlinks=False):
            for name in [*dirs, *files]:
                _check_path(root, Path(current)/name)
    try:
        for path in targets:
            if path.exists():
                shutil.rmtree(path)
        log.unlink()
    except OSError as error:
        raise ValueError('会话删除未完成，请确认数据文件未被占用后重试') from error
