"""Runtime assembly owns its MCP connection and discovered tools."""

import pytest

from raccoon.application.conversations.model_switching import create_auxiliary_models
from raccoon.bootstrap.runtime import create_runtime
from raccoon.config.settings import AppConfig
from raccoon.integrations.mcp.models import McpServerStatus, McpToolDescription
from tests.runtime_fixtures import FakeMcpManager
from tests.scripted_providers import FakeProvider


def test_routing_environment_uses_the_configured_model_without_a_catalog_entry():
    config = AppConfig(env={
        "RACCOON_MESSAGE_ROUTING_API_KEY": "offline",
        "RACCOON_MESSAGE_ROUTING_BASE_URL": "https://routing.example/v1",
        "RACCOON_MESSAGE_ROUTING_MODEL": "vendor/quick",
    })
    models = create_auxiliary_models(config)
    provider = models.message_routing
    assert provider.model == "vendor/quick"
    assert provider._base_url == "https://routing.example/v1"
    assert provider._api_key == "offline"
    assert models.permission_review is None
    assert models.context_summary is None


def test_partial_routing_environment_reports_missing_fields_without_the_key():
    config = AppConfig(env={"RACCOON_PERMISSION_REVIEW_API_KEY": "private-test-value"})
    with pytest.raises(ValueError) as error:
        create_auxiliary_models(config)
    assert "RACCOON_PERMISSION_REVIEW_BASE_URL" in str(error.value)
    assert "RACCOON_PERMISSION_REVIEW_MODEL" in str(error.value)
    assert "private-test-value" not in str(error.value)


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
        assert {"write", "mcp__demo__ping", "background_status", "background_cancel"} <= set(names)
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


def test_runtime_uses_three_independent_models_for_internal_requests(tmp_path):
    roles = ("message_routing", "permission_review", "context_summary")
    env = {}
    for role in roles:
        prefix = "RACCOON_" + role.upper()
        env[prefix + "_API_KEY"] = "offline-" + role
        env[prefix + "_BASE_URL"] = "https://example.test/" + role
        env[prefix + "_MODEL"] = "model-" + role
    config = AppConfig(
        env=env,
        project_config={"memory": {"enabled": False}, "event_routing_model": "legacy/other"},
    )
    provider = FakeProvider([])
    runtime = create_runtime(
        project_root=tmp_path, provider=provider, app_config=config, tools=[],
        mcp_manager_factory=lambda _: FakeMcpManager(),
    )
    try:
        assert runtime.provider is provider
        runtime.update_model_catalog({})
        for role in roles:
            model = getattr(runtime.auxiliary_models, role)
            assert model.model == "model-" + role
            assert model._api_key == "offline-" + role
            assert model._base_url == "https://example.test/" + role
    finally:
        runtime.close()
