"""真实 MCP stdio 与远程配置边界的集成测试。"""

from __future__ import annotations

from tests.tool_harness import execute_test_tool as execute_tool
from raccoon.modules.tools.permissions import preflight_tool_permission

import sys
from pathlib import Path

from raccoon.integrations.mcp.adapter import adapt_mcp_tool
from raccoon.integrations.mcp.manager import McpManager
from raccoon.integrations.mcp.models import McpLocalServerConfig, McpRemoteServerConfig, McpToolDescription
from raccoon.modules.permissions.manager import PermissionManager
from raccoon.modules.permissions.policy import DefaultPermissionPolicy
from raccoon.modules.permissions.types import PermissionConfirmationChoice
from raccoon.modules.permissions.types import PermissionDecisionKind
from raccoon.modules.tools.registry import ToolRegistry


def test_stdio_echo_tool_requires_confirmation_then_executes_after_explicit_allow(tmp_path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
    manager = McpManager(
        (
            McpLocalServerConfig(
                name="echo",
                command=(sys.executable, str(fixture)),
                timeout_ms=5_000,
            ),
        )
    )
    try:
        manager.connect_all()
        assert manager.doctor("echo").state == "connected"
        discovered = dict(manager.tools())["echo"]
        assert discovered.name == "echo"

        tool = adapt_mcp_tool(manager, "echo", discovered)
        permissions = PermissionManager(policy=DefaultPermissionPolicy(tmp_path))
        registry = ToolRegistry([tool])

        paused = execute_tool(registry, "mcp__echo__echo", {"message": "hello MCP"}, permission_manager=permissions)

        assert paused.data["requires_user_input"] is True
        assert paused.data["permission_request"]["action"] == "mcp_tool"
        assert paused.data["permission_request"]["target"] == "echo/echo"

        arguments = {"message": "hello MCP"}
        pending = preflight_tool_permission(permissions, tool, arguments)
        assert pending is not None
        request, _ = pending
        allowed = permissions.resolve_confirmation(request, PermissionConfirmationChoice.ALLOW_ONCE.value)
        assert allowed.kind == PermissionDecisionKind.ALLOW

        result = execute_tool(registry, "mcp__echo__echo", arguments)

        assert result.ok is True
        assert result.content == "hello MCP"
    finally:
        manager.close()


class _RemoteTransport:
    async def connect(self) -> None:
        return None

    async def list_tools(self) -> tuple[McpToolDescription, ...]:
        return (McpToolDescription("echo", "Echo text."),)

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        return {"content": [{"type": "text", "text": str(arguments["message"])}]}

    async def close(self) -> None:
        return None


class _CapturingRemoteFactory:
    def __init__(self) -> None:
        self.config: McpRemoteServerConfig | None = None

    def create(self, config: McpLocalServerConfig | McpRemoteServerConfig) -> _RemoteTransport:
        assert isinstance(config, McpRemoteServerConfig)
        self.config = config
        return _RemoteTransport()


def test_remote_config_forwards_url_and_headers_without_leaking_header_value() -> None:
    secret = "Bearer secret-value-that-must-not-appear"
    factory = _CapturingRemoteFactory()
    manager = McpManager(
        (
            McpRemoteServerConfig(
                name="remote",
                url="https://example.test/mcp",
                headers={"Authorization": secret},
            ),
        ),
        transport_factory=factory,
    )
    try:
        manager.connect_all()

        assert factory.config is not None
        assert factory.config.url == "https://example.test/mcp"
        assert factory.config.headers == {"Authorization": secret}
        status = manager.doctor("remote")
        assert status is not None
        assert status.state == "connected"
        assert secret not in repr(status)
        assert secret not in str(manager.statuses())
    finally:
        manager.close()


def test_remote_bearer_token_environment_variable_becomes_authorization_header() -> None:
    factory = _CapturingRemoteFactory()
    manager = McpManager(
        (
            McpRemoteServerConfig(
                name="remote",
                url="https://example.test/mcp",
                bearer_token_env_var="REMOTE_MCP_TOKEN",
            ),
        ),
        transport_factory=factory,
        environment={"REMOTE_MCP_TOKEN": "secret-value-that-must-not-appear"},
    )
    try:
        manager.connect_all()

        assert factory.config is not None
        assert factory.config.headers == {"Authorization": "Bearer secret-value-that-must-not-appear"}
        assert manager.doctor("remote").state == "connected"
        assert "secret-value-that-must-not-appear" not in str(manager.statuses())
    finally:
        manager.close()


def test_remote_bearer_token_environment_variable_reports_missing_variable_name() -> None:
    manager = McpManager(
        (
            McpRemoteServerConfig(
                name="remote",
                url="https://example.test/mcp",
                bearer_token_env_var="REMOTE_MCP_TOKEN",
            ),
        ),
        transport_factory=_CapturingRemoteFactory(),
        environment={},
        retry_attempts=1,
    )
    try:
        manager.connect_all()

        status = manager.doctor("remote")
        assert status.state == "failed"
        assert status.error == "缺少环境变量：REMOTE_MCP_TOKEN"
    finally:
        manager.close()
