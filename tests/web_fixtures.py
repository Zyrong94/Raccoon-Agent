"""Web API fixtures with a fake model and no MCP connections."""

import time

import pytest
from raccoon.runtime.model_types import MessagePart
from fastapi.testclient import TestClient

from raccoon.bootstrap.runtime import create_runtime
from raccoon.config.settings import AppConfig
from raccoon.runtime.model_provider import ChatProvider
from raccoon.runtime.model_types import ChatResponse, ProviderCapabilities
from tests.runtime_fixtures import FakeMcpManager


class WebProvider(ChatProvider):
    name = "fake"
    model = "web-model"
    capabilities = ProviderCapabilities()

    def __init__(self, responses):
        self.responses = iter(responses)

    def complete(self, request):
        return next(self.responses)

    async def acomplete(self, request):
        import asyncio
        await asyncio.sleep(0.03)
        return self.complete(request)


def response(content="完成", calls=None):
    return ChatResponse(
        provider="fake",
        model="web-model",
        parts=[MessagePart(kind="reasoning", metadata={"reasoning_item": {"encrypted_content": "DO_NOT_EXPOSE"}}), MessagePart(kind="text", content=content)] + [MessagePart.from_tool_call(call) for call in (calls or [])],
    )


def wait_idle(client, *, allow_pending=False, scope=None):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if scope is None:
            state = client.get("/api/state").json()
        else:
            response = client.get("/api/session-view", params=scope)
            assert response.status_code == 200, response.text
            state = response.json()
        if not state["busy"] or allow_pending and state["pending"] is not None:
            return state
        time.sleep(0.01)
    pytest.fail("turn did not finish")


@pytest.fixture
def web(tmp_path):
    from raccoon.bootstrap.web_app import create_web_app

    (tmp_path / "AGENTS.md").write_text("测试项目", encoding="utf-8")
    provider = WebProvider([response("你好，工作台")])
    def factory(**kwargs):
        item = create_runtime(**kwargs, provider=WebProvider([response("你好，工作台")] * 30), tools=[],
            app_config=AppConfig(env={}, project_config={'memory': {'enabled': False}}),
            mcp_manager_factory=lambda configs: FakeMcpManager())
        item.tools_provider = lambda: item.tools
        return item

    runtime = create_runtime(project_root=tmp_path, provider=provider, tools=[],
        app_config=AppConfig(env={}, project_config={'memory': {'enabled': False}}),
        mcp_manager_factory=lambda configs: FakeMcpManager())
    # These tests inject tools after runtime creation; production uses the MCP catalog.
    runtime.tools_provider = lambda: runtime.tools
    app = create_web_app(runtime, runtime_factory=factory)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        state = client.get("/api/state").json()
        client.headers["X-Raccoon-Token"] = state["token"]
        yield client, runtime, provider
