import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

import start


@pytest.mark.parametrize("installed", [False, True])
def test_source_launcher_prepares_missing_dependencies_and_forwards_project(monkeypatch, installed):
    web = start.WEB
    python = start.ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    monkeypatch.setattr(Path, "is_file", lambda path: installed)
    monkeypatch.setattr(sys, "argv", ["start.py", "--project", "my project"])
    monkeypatch.setattr(start.shutil, "which", lambda command: command)
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "22.0.0")
    run = Mock(return_value=Mock(returncode=0 if installed else 1))
    monkeypatch.setattr(subprocess, "run", run)
    launch = Mock()
    launch.return_value.wait.return_value = 0
    monkeypatch.setattr(subprocess, "Popen", launch)

    assert start.main() == 0
    setup_commands = [call.args[0] for call in run.call_args_list if call.kwargs.get("check")]
    if installed:
        assert setup_commands == []
    else:
        assert setup_commands == [
            [sys.executable, "-m", "venv", python.parents[1]],
            [python, "-m", "pip", "install", "-e", ".[web]"],
            ["npm.cmd" if os.name == "nt" else "npm", "ci"],
        ]
    launch.assert_called_once_with(["node", str(web / "scripts/start.mjs"), "--dev", "--open", "--project", "my project"])
