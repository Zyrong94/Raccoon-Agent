"""Session owners keep concurrent requests, permissions and lifecycle isolated."""
import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from raccoon.bootstrap.runtime import create_runtime
from raccoon.config.settings import AppConfig
from raccoon.modules.permissions.types import PermissionAction
from raccoon.runtime.model_types import ToolCall, ToolDefinition
from raccoon.modules.tools.types import Tool, ToolPermissionSpec, make_text_result
from raccoon.bootstrap.web_app import create_web_app
from tests.runtime_fixtures import FakeMcpManager
from tests.web_fixtures import WebProvider, response


class ControlledProvider(WebProvider):
    def __init__(self, name):
        super().__init__([response(name + ' done')] * 10)
        self.label = name
        self.entered = threading.Event()
        self.release = threading.Event()
        self.fail = False

    async def acomplete(self, request):
        self.entered.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        if self.fail:
            raise RuntimeError(self.label + ' failure')
        return response(self.label + ' done')


def until(client, predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        state = client.get('/api/state').json()
        if predicate(state):
            return state
        time.sleep(0.01)
    pytest.fail('session state did not settle')


@pytest.fixture
def concurrent_web(tmp_path, monkeypatch):
    monkeypatch.setenv('RACCOON_DISABLE_GLOBAL_SKILLS', '1')
    providers, runtimes = [], []

    def factory(**kwargs):
        provider = ControlledProvider(chr(ord('A') + len(providers)))
        providers.append(provider)
        tool = Tool(ToolDefinition('network', 'permission demo', {}),
                    lambda: make_text_result('network', 'approved'),
                    ToolPermissionSpec(action=PermissionAction.NETWORK_REQUEST, target_value='https://example.com'))
        runtime = create_runtime(**kwargs, provider=provider, tools=[tool],
            app_config=AppConfig(env={}, project_config={'memory': {'enabled': False}}),
            mcp_manager_factory=lambda configs: FakeMcpManager())
        runtimes.append(runtime)
        return runtime

    runtime = factory(project_root=tmp_path)
    app = create_web_app(runtime, runtime_factory=factory)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        client.headers['X-Raccoon-Token'] = client.get('/api/state').json()['token']
        try:
            yield client, providers, runtimes, app.state.workbench
        finally:
            for provider in providers:
                provider.release.set()
    assert all(runtime._closed for runtime in runtimes)


def test_two_sessions_run_independently_and_cancel_only_the_target(concurrent_web):
    client, providers, _, _ = concurrent_web
    initial = client.get("/api/state").json()
    project, first = initial["project"]["id"], initial["session"]["id"]
    first_scope = {"project_id": project, "session_id": first}
    assert client.post("/api/messages", json={
        **first_scope, "message": "Run A", "submission_id": "start-a",
    }).status_code == 202
    assert providers[0].entered.wait(5)

    draft = client.post("/api/sidebar/new", json={"project_id": project}).json()
    second = draft["session"]["id"]
    assert first != second
    assert client.post("/api/messages", json={
        "project_id": project, "session_id": second, "message": "Run B", "submission_id": "start-b",
    }).status_code == 202
    assert providers[1].entered.wait(5)

    assert client.post("/api/cancel", json={**first_scope, "submission_id": "stop-a"}).status_code == 200
    current = client.get("/api/state").json()
    assert current["session"]["id"] == second and current["busy"]
    providers[1].release.set()
    finished = until(client, lambda state: not state["busy"])
    assert [message["content"] for message in finished["messages"]] == ["Run B", "B done"]


def test_approval_can_target_a_chat_while_another_chat_is_selected(concurrent_web):
    client, providers, _, _ = concurrent_web
    provider = providers[0]
    provider.responses = iter([response("", [ToolCall("network", "network", {})]), response("Approved")])
    provider.acomplete = WebProvider.acomplete.__get__(provider)
    initial = client.get("/api/state").json()
    project, first = initial["project"]["id"], initial["session"]["id"]
    scope = {"project_id": project, "session_id": first}
    assert client.post("/api/messages", json={
        **scope, "submission_id": "first-message", "message": "Network request",
    }).status_code == 202
    pending = until(client, lambda state: state["pending"] is not None)["pending"]

    second = client.post("/api/sidebar/new", json={"project_id": project}).json()["session"]["id"]
    answer = {"request_id": pending["id"], "answer": "allow_once"}
    assert client.post("/api/input", json={
        **scope, **answer, "session_id": second, "submission_id": "wrong",
    }).status_code == 409
    assert client.post("/api/input", json={
        **scope, **answer, "submission_id": "approval",
    }).status_code == 202
    assert client.get("/api/state").json()["session"]["id"] == second
    assert client.post(f"/api/sessions/{first}/activate").status_code == 200
    assert until(client, lambda state: not state["busy"])["messages"][-1]["content"] == "Approved"
