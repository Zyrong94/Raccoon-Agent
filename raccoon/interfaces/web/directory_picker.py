"""Local native directory selection, isolated from the ASGI event loop."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field


class DirectoryPickerError(RuntimeError):
    pass


class DirectoryPickerBusy(DirectoryPickerError):
    pass


class DirectoryPickerTimeout(DirectoryPickerError):
    pass


def initial_directory(value: str | None, project: Path) -> Path:
    candidates = []
    if value:
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            candidates.extend([candidate, *candidate.parents])
    candidates.extend([project, *project.parents, Path.home()])
    for candidate in candidates:
        try:
            if candidate.is_dir():
                return candidate.resolve()
        except (OSError, ValueError):
            continue
    raise DirectoryPickerError('没有可用的初始目录')


class NativeDirectoryPicker:
    def __init__(self, *, timeout: float = 300):
        self.timeout = timeout
        self._task = None
        self._closed = False

    async def pick(self, initial: Path) -> str | None:
        if self._closed:
            raise DirectoryPickerError('系统目录选择器已关闭')
        if self._task is not None:
            raise DirectoryPickerBusy('已有系统目录窗口打开，请先完成或取消选择')
        self._task = asyncio.current_task()
        process = None
        spawn = None
        communication = None
        try:
            # Popen works on Windows SelectorEventLoop too; all process waits run in workers.
            spawn = asyncio.create_task(asyncio.to_thread(
                subprocess.Popen,
                [sys.executable, '-X', 'utf8', str(Path(__file__).with_name('_directory_picker_gui.py')), str(initial)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                encoding='utf-8', shell=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            ))
            process = await asyncio.shield(spawn)
            communication = asyncio.create_task(asyncio.to_thread(process.communicate, timeout=self.timeout))
            output, _ = await asyncio.shield(communication)
            if process.returncode != 0:
                raise DirectoryPickerError('系统目录窗口启动失败，请检查本机 Python 的 Tk 支持与桌面会话')
            try:
                result = json.loads(output)
                if not isinstance(result, dict):
                    raise ValueError
                if result.get('error'):
                    raise DirectoryPickerError(str(result['error'])[:500])
                value = result['path']
                if value is None or value == '':
                    return None
                if not isinstance(value, str):
                    raise ValueError
                selected = Path(value)
                if not selected.is_absolute() or not selected.is_dir():
                    raise ValueError
                return str(selected.resolve())
            except (KeyError, TypeError, ValueError, OSError) as exc:
                raise DirectoryPickerError('系统目录选择结果无效，所选文件夹可能已被移除') from exc
        except subprocess.TimeoutExpired as exc:
            raise DirectoryPickerTimeout('系统目录选择已超时，请重试') from exc
        except OSError as exc:
            raise DirectoryPickerError('无法启动系统目录窗口，请检查本机 Python 与桌面会话') from exc
        finally:
            cleanup = asyncio.create_task(self._cleanup(spawn, process, communication))
            cancelled = False
            try:
                # Disconnect and shutdown may both cancel this request before Popen returns.
                while True:
                    try:
                        await asyncio.shield(cleanup)
                        break
                    except asyncio.CancelledError:
                        if cleanup.cancelled():
                            raise
                        cancelled = True
            finally:
                self._task = None
            if cancelled:
                raise asyncio.CancelledError

    async def _cleanup(self, spawn, process, communication):
        if process is None and spawn is not None:
            try:
                process = await spawn
            except OSError:
                return
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            if communication is not None:
                await asyncio.gather(communication, return_exceptions=True)
            await asyncio.to_thread(process.communicate)

    async def close(self):
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


class DirectoryPickBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str | None = Field(default=None, max_length=4096)


def install_directory_picker_routes(app, workbench):
    picker = NativeDirectoryPicker()
    app.state.directory_picker = picker

    @app.post('/api/directories/pick')
    async def pick_directory(body: DirectoryPickBody, request: Request):
        initial = initial_directory(body.path, Path(workbench.workspace.projects[workbench.selected_session[0]]['path']))
        task = asyncio.create_task(picker.pick(initial))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.25)
                if not task.done() and await request.is_disconnected():
                    raise HTTPException(499, '目录选择请求已断开')
            return {'path': await task}
        except DirectoryPickerBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        except DirectoryPickerTimeout as exc:
            raise HTTPException(504, str(exc)) from exc
        except DirectoryPickerError as exc:
            raise HTTPException(503, str(exc)) from exc
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
