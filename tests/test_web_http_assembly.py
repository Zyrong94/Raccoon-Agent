"""HTTP wiring must retain the supplied owners and close them in order."""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from raccoon.interfaces.web.server import create_web_app


def test_http_factory_keeps_supplied_owners_and_lifecycle_order(tmp_path):
    calls = []

    async def record(name):
        calls.append(name)

    workbench = SimpleNamespace(
        start=lambda: record('workbench.start'),
        close=lambda: record('workbench.close'),
    )
    bridge = SimpleNamespace(close=lambda: record('bridge.close'))
    scheduling = SimpleNamespace(
        store=object(), start=lambda: record('scheduling.start'),
        stop_intake=lambda: record('scheduling.stop_intake'),
        close=lambda: record('scheduling.close'),
    )
    app = create_web_app(tmp_path, workbench=workbench, main_bridge=bridge, scheduling=scheduling)
    app.state.directory_picker = SimpleNamespace(close=lambda: record('picker.close'))
    assert app.state.workbench is workbench
    assert app.state.main_bridge is bridge
    assert app.state.scheduling is scheduling
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        assert client.get('/').status_code == 200
        assert client.post('/api/cancel').status_code == 403
    assert calls == ['workbench.start', 'scheduling.start', 'picker.close',
                     'scheduling.stop_intake', 'bridge.close', 'workbench.close', 'scheduling.close']
