from __future__ import annotations

import json
import asyncio
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from raccoon.integrations.browser import community as browser
from raccoon.interfaces.cli import community_browser as browser_cli
from raccoon.integrations.mcp.models import McpLocalServerConfig
from raccoon.integrations.mcp.transport import SdkMcpTransportFactory


VERSION = {"webSocketDebuggerUrl": "ws://127.0.0.1:9224/devtools/browser/known"}


def save_state(root, version=VERSION):
    path = root / ".raccoon/browser/shared-session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(version), encoding="utf-8")


def test_reuses_recorded_browser_without_launching(tmp_path, monkeypatch):
    save_state(tmp_path)
    monkeypatch.setattr(browser, "_version", lambda: VERSION)
    spawn = Mock()
    monkeypatch.setattr(browser.subprocess, "Popen", spawn)
    assert browser.ensure_browser(tmp_path) == browser.CDP_ENDPOINT
    spawn.assert_not_called()


def test_rejects_foreign_debugging_endpoint(tmp_path, monkeypatch):
    save_state(tmp_path, {"webSocketDebuggerUrl": "ws://127.0.0.1:9224/devtools/browser/other"})
    monkeypatch.setattr(browser, "_version", lambda: VERSION)
    spawn = Mock()
    monkeypatch.setattr(browser.subprocess, "Popen", spawn)
    with pytest.raises(RuntimeError, match="another browser"):
        browser.ensure_browser(tmp_path)
    spawn.assert_not_called()


@pytest.mark.parametrize("saved", [[], None, "invalid"])
def test_corrupt_record_does_not_attach_to_an_unidentified_browser(tmp_path, monkeypatch, saved):
    save_state(tmp_path, saved)
    monkeypatch.setattr(browser, "_version", lambda: VERSION)
    with pytest.raises(RuntimeError, match="another browser"):
        browser.ensure_browser(tmp_path)


def test_launches_existing_profile_and_records_browser_identity(tmp_path, monkeypatch):
    versions = iter([None, VERSION])
    monkeypatch.setattr(browser, "_version", lambda: next(versions))
    chrome = tmp_path / "chrome.exe"
    monkeypatch.setattr(browser, "_chrome_executable", lambda profile: chrome)
    spawn = Mock()
    monkeypatch.setattr(browser.subprocess, "Popen", spawn)
    monkeypatch.setattr(browser, "_browser_command_line", lambda version: [f"--user-data-dir={tmp_path / '.raccoon/browser/playwright'}"])
    profile = tmp_path / ".raccoon/browser/playwright"
    profile.mkdir(parents=True)
    marker = profile / "existing-login-data"
    marker.write_text("preserve", encoding="utf-8")
    assert browser.ensure_browser(tmp_path) == browser.CDP_ENDPOINT
    args = spawn.call_args.args[0]
    assert args[0] == str(chrome)
    assert f"--user-data-dir={profile}" in args
    assert "--remote-debugging-port=9224" in args
    assert "--remote-debugging-address=127.0.0.1" in args
    assert marker.read_text() == "preserve"
    state = json.loads((profile.parent / "shared-session.json").read_text())
    assert state == VERSION


def test_newly_observed_foreign_browser_is_not_trusted_or_given_extension(tmp_path, monkeypatch):
    versions = iter([None, VERSION])
    monkeypatch.setattr(browser, "_version", lambda: next(versions))
    monkeypatch.setattr(browser, "_chrome_executable", lambda profile: tmp_path / "chrome.exe")
    monkeypatch.setattr(browser.subprocess, "Popen", Mock())
    monkeypatch.setattr(browser, "_browser_command_line", lambda version: [f"--user-data-dir={tmp_path / 'unrelated-profile'}"])
    restore = Mock(side_effect=AssertionError("must not install Bridge in a foreign browser"))
    monkeypatch.setattr(browser, "_restore_bridge", restore)
    with pytest.raises(RuntimeError, match="another browser"):
        browser.ensure_browser(tmp_path)
    restore.assert_not_called()
    assert not (tmp_path / ".raccoon/browser/shared-session.json").exists()


def test_startup_timeout_preserves_profile_and_does_not_claim_success(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "_version", lambda: None)
    monkeypatch.setattr(browser, "_chrome_executable", lambda profile: tmp_path / "chrome.exe")
    monkeypatch.setattr(browser.subprocess, "Popen", Mock())
    ticks = iter([0, 100])
    monkeypatch.setattr(browser.time, "monotonic", lambda: next(ticks))
    profile = tmp_path / ".raccoon/browser/playwright"
    profile.mkdir(parents=True)
    marker = profile / "existing-login-data"
    marker.touch()
    with pytest.raises(RuntimeError, match="Chrome did not start"):
        browser.ensure_browser(tmp_path)
    assert marker.exists()
    assert not (profile.parent / "shared-session.json").exists()


def test_mcp_attaches_to_existing_browser_and_forwards_exit_code(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reuse = Mock(return_value=browser.CDP_ENDPOINT)
    monkeypatch.setattr(browser, "_recorded_endpoint", reuse)
    ensure = Mock(side_effect=AssertionError("MCP child must not launch Chrome inside SDK job"))
    monkeypatch.setattr(browser, "ensure_browser", ensure)
    monkeypatch.setattr(browser.shutil, "which", lambda name: "npx")
    run = Mock(return_value=Mock(returncode=7))
    monkeypatch.setattr(browser.subprocess, "run", run)
    assert browser_cli.main(["mcp"]) == 7
    args = run.call_args.args[0]
    assert "@playwright/mcp@0.0.83" in args
    assert args[args.index("--cdp-endpoint") + 1] == browser.CDP_ENDPOINT
    assert "--user-data-dir" not in args
    assert run.call_args.kwargs["stdin"] is browser.sys.stdin
    assert run.call_args.kwargs["stdout"] is browser.sys.stdout
    assert run.call_args.kwargs["stderr"] is browser.sys.stderr
    reuse.assert_called_once_with(tmp_path)
    ensure.assert_not_called()


def test_factory_starts_shared_browser_before_sdk_child_is_created(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ensure = Mock(return_value=browser.CDP_ENDPOINT)
    monkeypatch.setattr(browser, "ensure_browser", ensure)
    factory = SdkMcpTransportFactory()
    config = McpLocalServerConfig(name="playwright", command=(browser.sys.executable, "-m", "raccoon.interfaces.cli.community_browser", "mcp"))
    factory.create(config)
    ensure.assert_called_once_with(tmp_path)


def test_explicit_project_root_is_used_when_raccoon_runs_elsewhere(tmp_path, monkeypatch):
    project = tmp_path / "project with spaces"
    project.mkdir()
    monkeypatch.chdir(tmp_path)
    ensure = Mock(return_value=browser.CDP_ENDPOINT)
    monkeypatch.setattr(browser, "ensure_browser", ensure)
    config = McpLocalServerConfig(name="playwright", command=(sys.executable, "-m", "raccoon.interfaces.cli.community_browser", "mcp", "--root", str(project)))
    SdkMcpTransportFactory().create(config)
    ensure.assert_called_once_with(project)
    reuse = Mock(return_value=browser.CDP_ENDPOINT)
    monkeypatch.setattr(browser, "_recorded_endpoint", reuse)
    monkeypatch.setattr(browser.shutil, "which", lambda name: "npx")
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(browser.subprocess, "run", run)
    assert browser_cli.main(["mcp", "--root", str(project)]) == 0
    reuse.assert_called_once_with(project)
    assert str(project / ".raccoon/browser/artifacts") in run.call_args.args[0]


def test_other_mcp_servers_do_not_start_community_browser(monkeypatch):
    ensure = Mock(side_effect=AssertionError("unrelated MCP server"))
    monkeypatch.setattr(browser, "ensure_browser", ensure)
    SdkMcpTransportFactory().create(McpLocalServerConfig(name="echo", command=("python", "echo.py")))
    ensure.assert_not_called()


def test_installed_bridge_is_restored_when_reusing_chrome(tmp_path, monkeypatch):
    save_state(tmp_path)
    extension = tmp_path / ".raccoon/tools/opencli/extension"
    extension.mkdir(parents=True)
    (extension / "manifest.json").write_text("{}")
    monkeypatch.setattr(browser, "_version", lambda: VERSION)
    monkeypatch.setattr(browser.shutil, "which", lambda name: "node")
    run = Mock(return_value=Mock(returncode=0, stdout='{"id":"extension-id"}', stderr=""))
    monkeypatch.setattr(browser.subprocess, "run", run)
    assert browser.ensure_browser(tmp_path) == browser.CDP_ENDPOINT
    assert run.call_count == 1
    assert run.call_args.args[0][-3:-1] == [VERSION["webSocketDebuggerUrl"], "Extensions.loadUnpacked"]
    assert json.loads(run.call_args.args[0][-1]) == {"path": str(extension)}


def test_bridge_restore_failure_is_actionable_and_keeps_browser_record(tmp_path, monkeypatch):
    save_state(tmp_path)
    extension = tmp_path / ".raccoon/tools/opencli/extension"
    extension.mkdir(parents=True)
    (extension / "manifest.json").write_text("{}")
    monkeypatch.setattr(browser, "_version", lambda: VERSION)
    monkeypatch.setattr(browser.shutil, "which", lambda name: "node")
    monkeypatch.setattr(browser.subprocess, "run", Mock(return_value=Mock(returncode=1, stderr="test failure")))
    with pytest.raises(RuntimeError, match="Browser Bridge"):
        browser.ensure_browser(tmp_path)
    assert json.loads((tmp_path / ".raccoon/browser/shared-session.json").read_text()) == VERSION


def test_mcp_child_without_running_shared_browser_requires_host_start(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(browser, "_version", lambda: None)
    spawn = Mock(side_effect=AssertionError("must not spawn Chrome"))
    monkeypatch.setattr(browser.subprocess, "Popen", spawn)
    assert browser_cli.main(["mcp"]) == 1
    assert "community_browser start" in capsys.readouterr().err
    spawn.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows SDK Job Object lifecycle")
def test_host_started_process_survives_real_sdk_transport_close(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    versions = iter([None, VERSION])
    monkeypatch.setattr(browser, "_version", lambda: next(versions))
    monkeypatch.setattr(browser, "_chrome_executable", lambda profile: Path(sys.executable))
    monkeypatch.setattr(browser, "_browser_command_line", lambda version: [f"--user-data-dir={tmp_path / '.raccoon/browser/playwright'}"])
    original_popen = subprocess.Popen
    processes = []

    def spawn_synthetic_chrome(command, **kwargs):
        process = original_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        processes.append(process)
        return process

    # Replace only Chrome startup; restore Popen before entering the real SDK.
    monkeypatch.setattr(browser.subprocess, "Popen", spawn_synthetic_chrome)
    factory = SdkMcpTransportFactory()
    config = McpLocalServerConfig(name="playwright", command=(sys.executable, "-m", "raccoon.interfaces.cli.community_browser", "mcp"))
    transport = factory.create(config)
    monkeypatch.setattr(browser.subprocess, "Popen", original_popen)
    fixture = Path(__file__).parent / "fixtures/mcp_echo_server.py"
    transport._config = replace(config, command=(sys.executable, str(fixture)))

    async def check():
        await transport.connect()
        try:
            assert any(tool.name == "echo" for tool in await transport.list_tools())
        finally:
            await transport.close()

    try:
        asyncio.run(check())
        assert len(processes) == 1
        assert processes[0].poll() is None
    finally:
        for process in processes:
            process.terminate()
            process.wait(timeout=5)


def test_missing_chrome_reports_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(browser.shutil, "which", lambda name: None)
    monkeypatch.setattr(browser.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="Chrome executable"):
        browser._chrome_executable(tmp_path)
