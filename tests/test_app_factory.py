"""Runtime assembly owns its MCP connection and discovered tools."""

import pytest

from raccoon.bootstrap.runtime import create_runtime
from raccoon.config.settings import AppConfig
from raccoon.integrations.mcp.models import McpServerStatus, McpToolDescription
from tests.runtime_fixtures import FakeMcpManager
from tests.scripted_providers import FakeProvider


def test_runtime_lists_mcp_tools_and_closes_the_connection_once(tmp_path):
    manager = FakeMcpManager(
        tools=(("demo", McpToolDescription("ping", "Ping", {"type": "object", "properties": {}})),),
        statuses=(McpServerStatus("demo", "connected", tool_count=1),),
    )
    runtime = create_runtime(
        project_root=tmp_path, provider=FakeProvider([]),
        app_config=AppConfig(env={}, project_config={"memory": {"enabled": False}}),
        mcp_manager_factory=lambda _: manager,
    )
    try:
        assert manager.connect_calls == 1
        names = runtime.session.tool_registry.names()
        assert {"write", "mcp__demo__ping"} <= set(names)
        assert runtime.mcp_status()["servers"][0]["tools"] == ["ping"]
    finally:
        runtime.close()
    runtime.close()
    assert manager.close_calls == 1


def test_failed_session_assembly_closes_the_mcp_connection(tmp_path):
    (tmp_path / "AGENTS.md").write_bytes(b"\xff")
    manager = FakeMcpManager()

    with pytest.raises(UnicodeDecodeError):
        create_runtime(
            project_root=tmp_path, provider=FakeProvider([]), tools=[],
            app_config=AppConfig(env={}, project_config={"memory": {"enabled": False}}),
            mcp_manager_factory=lambda _: manager,
        )

    assert manager.connect_calls == manager.close_calls == 1


def test_runtime_uses_the_fixed_deepseek_model_for_internal_requests(tmp_path):
    models = {
        "providers": {"deepseek": {"base_url": "https://example.test/v1", "api_key_env": "TEST_DEEPSEEK_KEY"}},
        "models": {"deepseek/deepseek-flash": {"request": {"max_tokens": 1024}}},
    }
    config = AppConfig(
        env={"TEST_DEEPSEEK_KEY": "offline"},
        project_config={"memory": {"enabled": False}, "event_routing_model": "legacy/other"},
        model_config=models,
    )
    provider = FakeProvider([])
    runtime = create_runtime(
        project_root=tmp_path, provider=provider, app_config=config, tools=[],
        mcp_manager_factory=lambda _: FakeMcpManager(),
    )
    try:
        assert runtime.provider is provider
        assert (runtime.routing_provider.name, runtime.routing_provider.model) == ("deepseek", "deepseek-flash")
        assert runtime.routing_request_options.max_tokens == 1024

        runtime.update_model_catalog({"providers": models["providers"], "models": {}})
        assert runtime.routing_provider is None
        assert runtime.routing_request_options is None
    finally:
        runtime.close()
