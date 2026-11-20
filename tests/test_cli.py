"""Web 启动器测试；交互 CLI、config 和 MCP 子命令已移除。"""

import sys
from types import ModuleType

import pytest

from raccoon.interfaces.cli.main import main


@pytest.fixture
def web_server(monkeypatch):
    calls = []
    module = ModuleType("raccoon.bootstrap.web")
    module.run_web_server = lambda **kwargs: calls.append(kwargs)
    monkeypatch.setitem(sys.modules, "raccoon.bootstrap.web", module)
    return module, calls


def test_main_launches_web_with_defaults(web_server):
    _, calls = web_server
    assert main([]) == 0
    assert calls == [{
        "project": ".", "data_root": None, "model": None,
        "session_id": None, "port": 8765, "open_browser": True,
    }]


def test_main_passes_web_launch_options(web_server):
    _, calls = web_server
    assert main([
        "--project", "project", "--data-root", "sessions",
        "--session-id", "existing", "--model", "provider/model",
        "--port", "9000", "--no-browser",
    ]) == 0
    assert calls == [{
        "project": "project", "data_root": "sessions", "model": "provider/model",
        "session_id": "existing", "port": 9000, "open_browser": False,
    }]


@pytest.mark.parametrize("arguments", [
    ["--message", "hello"], ["--agent-name", "reader"],
    ["config", "show"], ["mcp", "list"],
])
def test_main_rejects_retired_commands_before_startup(web_server, arguments):
    _, calls = web_server
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2
    assert calls == []


@pytest.mark.parametrize("port", ["0", "65536", "invalid"])
def test_main_rejects_invalid_port_before_startup(web_server, port):
    _, calls = web_server
    with pytest.raises(SystemExit) as error:
        main(["--port", port])
    assert error.value.code == 2
    assert calls == []


def test_main_reports_startup_error(web_server, capsys):
    module, _ = web_server

    def fail(**kwargs):
        raise RuntimeError("startup failed")

    module.run_web_server = fail
    assert main([]) == 1
    assert "startup failed" in capsys.readouterr().err


def test_web_entrypoint_rejects_removed_main_agent_option(web_server, monkeypatch, capsys):
    from raccoon.interfaces.web import __main__ as web_main

    monkeypatch.setattr("sys.argv", ["raccoon-web", "--agent-name", "reader"])
    with pytest.raises(SystemExit) as error:
        web_main.main()
    assert error.value.code == 2
    assert "--agent-name" in capsys.readouterr().err
    assert web_server[1] == []
