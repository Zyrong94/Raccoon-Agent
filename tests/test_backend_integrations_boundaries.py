"""Integration moves preserve shared model identity and supported entry points."""

import importlib
import runpy
import sys
from unittest.mock import Mock

import pytest

from raccoon.integrations.browser import community as browser
from raccoon.integrations.mcp.models import McpLocalServerConfig
from raccoon.integrations.mcp.transport import SdkMcpTransportFactory


@pytest.mark.parametrize(
    ("export_path", "canonical_path", "names"),
    [
        ("raccoon.integrations.providers", "raccoon.runtime.model_types", ("ChatMessage", "ChatRequest", "ChatResponse", "ToolCall", "ToolDefinition")),
        ("raccoon.integrations.providers.errors", "raccoon.runtime.model_errors", ("ProviderError", "ProviderErrorKind")),
        ("raccoon.integrations.providers.factory", "raccoon.config.models", ("ProviderConfigError",)),
        ("raccoon.integrations.mcp", "raccoon.integrations.mcp.models", ("McpLocalServerConfig", "McpToolDescription")),
    ],
)
def test_public_exports_keep_shared_class_identity(export_path, canonical_path, names):
    exported = importlib.import_module(export_path)
    canonical = importlib.import_module(canonical_path)
    for name in names:
        assert getattr(exported, name) is getattr(canonical, name)


@pytest.mark.parametrize("module", ["raccoon.interfaces.cli.community_browser"])
def test_browser_module_help_does_not_start_browser(module, monkeypatch, capsys):
    ensure = Mock(side_effect=AssertionError("help must not launch Chrome"))
    monkeypatch.setattr(browser, "ensure_browser", ensure)
    monkeypatch.setattr(sys, "argv", [module, "--help"])
    monkeypatch.delitem(sys.modules, module, raising=False)
    with pytest.raises(SystemExit) as result:
        runpy.run_module(module, run_name="__main__")
    assert result.value.code == 0
    assert "{start,mcp}" in capsys.readouterr().out
    ensure.assert_not_called()


@pytest.mark.parametrize("module", ["raccoon.interfaces.cli.community_browser"])
def test_browser_mcp_entry_point_starts_host_before_transport(module, tmp_path, monkeypatch):
    ensure = Mock(return_value=browser.CDP_ENDPOINT)
    monkeypatch.setattr(browser, "ensure_browser", ensure)
    transport = SdkMcpTransportFactory().create(McpLocalServerConfig(
        name="playwright",
        command=(sys.executable, "-m", module, "mcp", "--root", str(tmp_path)),
    ))
    ensure.assert_called_once_with(tmp_path)
    assert transport._session is None
