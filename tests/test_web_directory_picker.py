"""Offline native folder-picker contracts; no test opens a desktop window."""

import asyncio
import json
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def module():
    import raccoon.interfaces.web.directory_picker as directory_picker
    return directory_picker


class FakeProcess:
    def __init__(self, output, *, blocked=False, returncode=0):
        self.output = output
        self.returncode = None if blocked else returncode
        self.released = threading.Event()
        if not blocked:
            self.released.set()
        self.killed = False
        self.communicating = threading.Lock()

    def communicate(self, timeout=None):
        if not self.communicating.acquire(blocking=False):
            raise RuntimeError('Concurrent pipe readers')
        try:
            if not self.released.wait(timeout):
                raise subprocess.TimeoutExpired('picker', timeout)
            if self.killed:
                time.sleep(0.01)
            return self.output, ''
        finally:
            self.communicating.release()

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -1
        self.released.set()

    def wait(self):
        self.released.wait(2)
        return self.returncode


@pytest.mark.anyio
async def test_native_selection_uses_argv_and_returns_existing_unicode_directory(tmp_path, monkeypatch):
    picker_module = module()
    selected = tmp_path / '项目 ; $(never)'
    selected.mkdir()
    calls = []
    def launch(argv, **kwargs):
        calls.append((argv, kwargs))
        return FakeProcess(json.dumps({'path': str(selected)}))
    monkeypatch.setattr(picker_module.subprocess, 'Popen', launch)
    picker = picker_module.NativeDirectoryPicker()
    assert await picker.pick(tmp_path) == str(selected.resolve())
    argv, kwargs = calls[0]
    assert argv[-1] == str(tmp_path)
    assert argv[-2].endswith('_directory_picker_gui.py')
    assert kwargs['shell'] is False
    assert kwargs['stdin'] == subprocess.DEVNULL


@pytest.mark.anyio
async def test_real_helper_process_round_trips_utf8_without_a_desktop(tmp_path, monkeypatch):
    picker_module = module()
    selected = tmp_path / '项目'
    selected.mkdir()
    helper = tmp_path / '_directory_picker_gui.py'
    helper.write_text("import json,sys; print(json.dumps({'path': sys.argv[1]}, ensure_ascii=False))", encoding='utf-8')
    monkeypatch.setattr(picker_module, '__file__', str(tmp_path / 'directory_picker.py'))
    assert await picker_module.NativeDirectoryPicker().pick(selected) == str(selected.resolve())


@pytest.mark.anyio
async def test_cancellation_during_process_creation_still_reaps_helper(tmp_path, monkeypatch):
    picker_module = module()
    process = FakeProcess('', blocked=True)
    launched = threading.Event()
    def launch(*args, **kwargs):
        launched.set()
        time.sleep(0.03)
        return process
    monkeypatch.setattr(picker_module.subprocess, 'Popen', launch)
    picker = picker_module.NativeDirectoryPicker()
    task = asyncio.create_task(picker.pick(tmp_path))
    await asyncio.to_thread(launched.wait, 2)
    await picker.close()
    assert task.cancelled()
    assert process.killed and process.poll() is not None


@pytest.mark.anyio
async def test_repeated_cancellation_during_spawn_keeps_guard_until_helper_is_reaped(tmp_path, monkeypatch):
    picker_module = module()
    process = FakeProcess('', blocked=True)
    launched, finish_spawn = threading.Event(), threading.Event()
    def launch(*args, **kwargs):
        launched.set()
        finish_spawn.wait(2)
        return process
    monkeypatch.setattr(picker_module.subprocess, 'Popen', launch)
    picker = picker_module.NativeDirectoryPicker()
    task = asyncio.create_task(picker.pick(tmp_path))
    await asyncio.to_thread(launched.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    guarded = picker._task is task and not task.done()
    try:
        if guarded:
            with pytest.raises(picker_module.DirectoryPickerBusy):
                await picker.pick(tmp_path)
    finally:
        finish_spawn.set()
        await asyncio.gather(task, return_exceptions=True)
    assert process.killed and process.poll() is not None
    assert guarded
    assert task.cancelled()
    assert picker._task is None
    monkeypatch.setattr(picker_module.subprocess, 'Popen', lambda *a, **k: FakeProcess('{"path":null}'))
    assert await picker.pick(tmp_path) is None


@pytest.mark.anyio
@pytest.mark.parametrize('output', [{'path': None}, {'path': ''}])
async def test_native_cancel_returns_null(tmp_path, monkeypatch, output):
    picker_module = module()
    monkeypatch.setattr(picker_module.subprocess, 'Popen', lambda *a, **k: FakeProcess(json.dumps(output)))
    assert await picker_module.NativeDirectoryPicker().pick(tmp_path) is None


@pytest.mark.anyio
@pytest.mark.parametrize('output', ['not-json', '{"path":12}', '{"path":"relative"}', '{"error":"Tk unavailable"}'])
async def test_native_invalid_or_failed_output_is_an_explicit_error(tmp_path, monkeypatch, output):
    picker_module = module()
    monkeypatch.setattr(picker_module.subprocess, 'Popen', lambda *a, **k: FakeProcess(output))
    with pytest.raises(picker_module.DirectoryPickerError):
        await picker_module.NativeDirectoryPicker().pick(tmp_path)


@pytest.mark.anyio
async def test_timeout_kills_and_reaps_helper_and_allows_retry(tmp_path, monkeypatch):
    picker_module = module()
    process = FakeProcess('', blocked=True)
    monkeypatch.setattr(picker_module.subprocess, 'Popen', lambda *a, **k: process)
    picker = picker_module.NativeDirectoryPicker(timeout=0.01)
    with pytest.raises(picker_module.DirectoryPickerTimeout):
        await picker.pick(tmp_path)
    assert process.killed and process.poll() is not None
    monkeypatch.setattr(picker_module.subprocess, 'Popen', lambda *a, **k: FakeProcess('{"path":null}'))
    assert await picker.pick(tmp_path) is None


@pytest.mark.anyio
async def test_concurrent_pick_is_rejected_and_shutdown_cleans_helper(tmp_path, monkeypatch):
    picker_module = module()
    process = FakeProcess('', blocked=True)
    launched = threading.Event()
    def launch(*args, **kwargs):
        launched.set()
        return process
    monkeypatch.setattr(picker_module.subprocess, 'Popen', launch)
    picker = picker_module.NativeDirectoryPicker()
    task = asyncio.create_task(picker.pick(tmp_path))
    await asyncio.to_thread(launched.wait, 2)
    while not process.communicating.locked():
        await asyncio.sleep(0)
    with pytest.raises(picker_module.DirectoryPickerBusy):
        await picker.pick(tmp_path)
    await picker.close()
    assert task.cancelled()
    assert process.killed and process.poll() is not None


def test_initial_directory_uses_valid_ancestor_then_project(tmp_path):
    picker_module = module()
    current = tmp_path / 'current'
    current.mkdir()
    assert picker_module.initial_directory(str(tmp_path / 'missing' / 'child'), current) == tmp_path
    assert picker_module.initial_directory('relative/path', current) == current
    assert picker_module.initial_directory(None, current) == current


@pytest.fixture
def picker_workbench(tmp_path):
    return SimpleNamespace(
        workspace=SimpleNamespace(projects={'project': {'path': str(tmp_path)}}),
        selected_session=('project', 'current'),
    )


def test_route_validates_request_and_does_not_register_or_open_the_project(tmp_path, monkeypatch, picker_workbench):
    picker_module = module()
    calls = []
    class Picker:
        async def pick(self, initial):
            calls.append(initial)
            return None
    monkeypatch.setattr(picker_module, 'NativeDirectoryPicker', Picker)
    app = FastAPI()
    picker_module.install_directory_picker_routes(app, picker_workbench)
    with TestClient(app) as client:
        result = client.post('/api/directories/pick', json={})
        assert result.status_code == 200 and result.json() == {'path': None}
        assert client.post('/api/directories/pick', json={'path': 12}).status_code == 422
        assert client.post('/api/directories/pick', json={'path': 'x' * 4097}).status_code == 422
    assert calls == [tmp_path]


@pytest.mark.parametrize('exception,status', [
    ('DirectoryPickerBusy', 409), ('DirectoryPickerTimeout', 504), ('DirectoryPickerError', 503),
])
def test_route_reports_native_failures(monkeypatch, picker_workbench, exception, status):
    picker_module = module()
    class Picker:
        async def pick(self, initial):
            raise getattr(picker_module, exception)('native failure')
    monkeypatch.setattr(picker_module, 'NativeDirectoryPicker', Picker)
    app = FastAPI()
    picker_module.install_directory_picker_routes(app, picker_workbench)
    with TestClient(app) as client:
        result = client.post('/api/directories/pick', json={})
        assert result.status_code == status
        assert result.json()['detail'] == 'native failure'


@pytest.mark.anyio
async def test_disconnected_request_cancels_native_selection(monkeypatch, picker_workbench):
    picker_module = module()
    cancelled = asyncio.Event()
    class Picker:
        async def pick(self, initial):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
    class Request:
        async def is_disconnected(self):
            return True
    monkeypatch.setattr(picker_module, 'NativeDirectoryPicker', Picker)
    app = FastAPI()
    picker_module.install_directory_picker_routes(app, picker_workbench)
    route = next(route for route in app.routes if route.path == '/api/directories/pick')
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        await route.endpoint(picker_module.DirectoryPickBody(), Request())
    assert error.value.status_code == 499
    assert cancelled.is_set()


def test_directory_picker_inherits_host_origin_and_token_guards(tmp_path, monkeypatch):
    from raccoon.bootstrap.runtime import create_runtime
    from raccoon.config.settings import AppConfig
    from raccoon.bootstrap.web_app import create_web_app
    from tests.runtime_fixtures import FakeMcpManager
    from tests.web_fixtures import WebProvider

    monkeypatch.setenv('RACCOON_DISABLE_GLOBAL_SKILLS', '1')
    runtime = create_runtime(project_root=tmp_path, tools=[], provider=WebProvider([]),
        app_config=AppConfig(env={}, project_config={'memory': {'enabled': False}}),
        mcp_manager_factory=lambda configs: FakeMcpManager())
    app = create_web_app(runtime)
    calls = []
    async def pick(initial):
        calls.append(initial)
        return str(tmp_path)
    app.state.directory_picker.pick = pick
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        assert client.post('/api/directories/pick', json={}).status_code == 403
        token = client.get('/api/state').json()['token']
        client.headers['X-Raccoon-Token'] = token
        assert client.post('/api/directories/pick', json={}, headers={'Origin': 'https://evil.test'}).status_code == 403
        assert client.post('/api/directories/pick', json={}, headers={'Host': 'evil.test'}).status_code == 403
        assert calls == []
        result = client.post('/api/directories/pick', json={})
        assert result.status_code == 200 and result.json() == {'path': str(tmp_path)}
        assert calls == [tmp_path]


@pytest.mark.parametrize('selected,error', [('C:/selected', None), ('', None), (None, RuntimeError('no desktop'))])
def test_gui_helper_returns_utf8_json_and_destroys_hidden_root(monkeypatch, capsys, selected, error):
    import raccoon.interfaces.web._directory_picker_gui as _directory_picker_gui
    import sys
    destroyed = []
    class Root:
        def withdraw(self):
            pass
        def attributes(self, *args):
            pass
        def destroy(self):
            destroyed.append(True)
    def askdirectory(**options):
        assert options['mustexist'] is True
        assert options['initialdir'] == 'C:/项目'
        if error:
            raise error
        return selected
    monkeypatch.setitem(sys.modules, 'tkinter', SimpleNamespace(Tk=Root,
        filedialog=SimpleNamespace(askdirectory=askdirectory)))
    monkeypatch.setattr(sys, 'argv', ['helper', 'C:/项目'])
    _directory_picker_gui.main()
    result = json.loads(capsys.readouterr().out)
    assert destroyed == [True]
    if error:
        assert 'no desktop' in result['error']
    else:
        assert result == {'path': selected or None}

