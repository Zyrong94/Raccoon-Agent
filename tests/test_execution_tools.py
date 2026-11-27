"""执行类工具行为测试。"""

from __future__ import annotations

from functools import partial
from tests.tool_harness import execute_test_tool as execute_tool

import subprocess
import sys

from raccoon.runtime.processes import ProcessManager
from raccoon.modules.permissions.manager import PermissionManager
from raccoon.modules.permissions.policy import DefaultPermissionPolicy
from raccoon.modules.permissions.types import PermissionMode
from raccoon.modules.tools.diagnostics import create_diagnostics_tool
from raccoon.modules.tools.shell import create_shell_tool
from raccoon.modules.tools.registry import ToolRegistry
from raccoon.modules.tools import create_builtin_tools
from raccoon.runtime.subprocess import CommandResult


def test_shell_executes_command_inside_root(tmp_path):
    tools = create_builtin_tools(tmp_path, include_execution_tools=True)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "shell", {"command": "echo hello"})

    assert result.ok is True
    assert result.content == "hello"
    assert result.data["exit_code"] == 0
    assert result.data["cwd"] == "."


def test_shell_nonzero_exit_returns_stdout_and_stderr_to_model(tmp_path, monkeypatch):
    def fake_run(*args, **kwargs):
        return CommandResult(
            exit_code=2,
            stdout="partial stdout\n",
            stderr="compiler error\n",
            stdout_truncated=False,
            stderr_truncated=False,
            ok=False,
        )

    monkeypatch.setattr("raccoon.runtime.execution_sandbox.run_command", fake_run)
    tools = create_builtin_tools(tmp_path, include_execution_tools=True)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "shell", {"command": "compile"})

    assert result.ok is False
    assert result.error == "命令退出码为 2"
    assert "partial stdout" in result.content
    assert "compiler error" in result.content


def test_shell_rejects_cwd_outside_root(tmp_path):
    tools = create_builtin_tools(tmp_path, include_execution_tools=True)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "shell", {"command": "echo hi", "cwd": ".."})

    assert result.ok is False
    assert "超出项目目录" in result.error


def test_shell_timeout_returns_partial_output_to_model(tmp_path, monkeypatch):
    def fake_run(*args, **kwargs):
        return CommandResult(
            exit_code=-1,
            stdout="partial stdout\n",
            stderr="partial stderr\n",
            stdout_truncated=False,
            stderr_truncated=False,
            ok=False,
            error="命令执行超时",
        )

    monkeypatch.setattr("raccoon.runtime.execution_sandbox.run_command", fake_run)
    tools = create_builtin_tools(tmp_path, include_execution_tools=True)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "shell", {"command": "slow"})

    assert result.ok is False
    assert result.error == "命令执行超时"
    assert "命令执行超时" in result.content
    assert "partial stdout" in result.content
    assert "partial stderr" in result.content


def test_shell_truncates_large_stdout(tmp_path):
    tools = create_builtin_tools(tmp_path, include_execution_tools=True)
    registry = ToolRegistry(tools)
    command = subprocess.list2cmdline([sys.executable, "-c", "print('abcdef', end='')"])

    result = execute_tool(registry, "shell", {"command": command, "max_output_chars": 3})

    assert result.ok is True
    assert result.data["stdout"].startswith("ab")
    assert result.data["stdout"].endswith("f")
    assert "中间省略 3 个字符" in result.data["stdout"]
    assert result.data["stdout_truncated"] is True


def test_execution_tools_accept_explicit_non_sensitive_environment(tmp_path):
    tools = create_builtin_tools(tmp_path, include_execution_tools=True)
    registry = ToolRegistry(tools)

    shell_command = subprocess.list2cmdline(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ['RACCOON_TEST_FLAG'])",
        ]
    )
    shell_result = execute_tool(registry, 
        "shell",
        {
            "command": shell_command,
            "env": {"RACCOON_TEST_FLAG": "shell-visible"},
        },
    )
    assert shell_result.ok is True
    assert shell_result.data["stdout"].strip() == "shell-visible"
    assert shell_result.data["env_keys"] == ["RACCOON_TEST_FLAG"]


def test_execution_tools_reject_explicit_sensitive_environment(tmp_path):
    tools = create_builtin_tools(tmp_path, include_execution_tools=True)
    registry = ToolRegistry(tools)

    for tool_name, arguments in (
        ("shell", {"command": "echo no", "env": {"OPENAI_API_KEY": "secret"}}),
        ("diagnostics", {"env": {"DATABASE_PASSWORD": "secret"}}),
    ):
        result = execute_tool(registry, tool_name, arguments)

        assert result.ok is False
        assert "拒绝传入敏感环境变量" in result.error
        assert result.data["rejected_env_keys"]


def test_process_tools_start_wait_for_readiness_read_logs_and_stop(tmp_path):
    manager = ProcessManager(log_root=tmp_path / ".raccoon" / "processes")
    tools = create_builtin_tools(
        tmp_path,
        include_execution_tools=True,
        process_manager=manager,
    )
    registry = ToolRegistry(tools)
    script = (
        "import sys,time; "
        "print('booting', flush=True); "
        "print('READY', file=sys.stderr, flush=True); "
        "time.sleep(60)"
    )
    command = subprocess.list2cmdline([sys.executable, "-c", script])

    try:
        started = execute_tool(registry, 
            "process_start",
            {
                "command": command,
                "label": "demo service",
                "ready_pattern": "READY",
                "ready_timeout_seconds": 5,
                "env": {"RACCOON_TEST_FLAG": "visible"},
            },
        )

        assert started.ok is True
        process = started.data["process"]
        process_id = process["process_id"]
        assert process["status"] == "running"
        assert process["ready"] is True
        assert started.data["env_keys"] == ["RACCOON_TEST_FLAG"]

        status = execute_tool(registry, "process_status", {"process_id": process_id})
        logs = execute_tool(registry, "process_logs", {"process_id": process_id})

        assert status.ok is True
        assert status.data["process"]["status"] == "running"
        assert "booting" in logs.content
        assert "READY" in logs.content

        stopped = execute_tool(registry, "process_stop", {"process_id": process_id})
        assert stopped.ok is True
        assert stopped.data["process"]["status"] == "exited"
    finally:
        manager.shutdown()


def test_process_start_reports_readiness_timeout_without_losing_process(tmp_path):
    manager = ProcessManager(log_root=tmp_path / ".raccoon" / "processes")
    tools = create_builtin_tools(
        tmp_path,
        include_execution_tools=True,
        process_manager=manager,
    )
    registry = ToolRegistry(tools)
    command = subprocess.list2cmdline([sys.executable, "-c", "import time; time.sleep(60)"])

    try:
        result = execute_tool(registry, 
            "process_start",
            {
                "command": command,
                "ready_pattern": "NEVER_READY",
                "ready_timeout_seconds": 1,
            },
        )

        assert result.ok is False
        assert result.data["readiness_timed_out"] is True
        assert result.data["process"]["status"] == "running"
        assert "process_logs/process_status" in result.content
    finally:
        manager.shutdown()


def test_process_start_rejects_sensitive_environment(tmp_path):
    manager = ProcessManager(log_root=tmp_path / ".raccoon" / "processes")
    tools = create_builtin_tools(
        tmp_path,
        include_execution_tools=True,
        process_manager=manager,
    )
    registry = ToolRegistry(tools)

    result = execute_tool(registry, 
        "process_start",
        {"command": "echo no", "env": {"RACCOON_API_TOKEN": "secret"}},
    )

    assert result.ok is False
    assert result.data["rejected_env_keys"] == ["RACCOON_API_TOKEN"]
    assert manager.list() == []


def test_diagnostics_runs_pytest(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        return CommandResult(
            exit_code=0,
            stdout="ok\n",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            ok=True,
        )

    monkeypatch.setattr("raccoon.runtime.execution_sandbox.run_command", fake_run)
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "diagnostics")

    assert result.ok is True
    assert result.content == "ok"
    assert result.data["command"] == "python -m pytest -q"


def test_diagnostics_failure_returns_test_output_to_model(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        return CommandResult(
            exit_code=1,
            stdout="FAILED tests/test_demo.py::test_value\n",
            stderr="AssertionError: expected 2\n",
            stdout_truncated=False,
            stderr_truncated=False,
            ok=False,
        )

    monkeypatch.setattr("raccoon.runtime.execution_sandbox.run_command", fake_run)
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "diagnostics")

    assert result.ok is False
    assert result.error == "诊断命令退出码为 1"
    assert "FAILED tests/test_demo.py::test_value" in result.content
    assert "AssertionError: expected 2" in result.content


def test_diagnostics_requires_permission_confirmation(tmp_path):
    calls = []
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)
    manager = PermissionManager(policy=DefaultPermissionPolicy(tmp_path))
    permissioned = partial(execute_tool, registry, permission_manager=manager)

    result = permissioned("diagnostics", {"command": "touch should_not_run"})

    assert result.ok is True
    assert result.data["requires_user_input"] is True
    assert result.data["permission_request"]["action"] == "execute_shell"
    assert calls == []




def test_shell_runs_task_virtual_environment_python(tmp_path):
    import os
    import shlex
    import venv
    task_env = tmp_path / "task-env"
    venv.EnvBuilder(with_pip=False).create(task_env)
    executable = task_env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    script = tmp_path / "task.py"
    script.write_text("import sys\nsys.stdout.reconfigure(encoding='utf-8')\nprint(sys.prefix)\nprint('中文 ϕ')\n", encoding="utf-8")
    command = (subprocess.list2cmdline if os.name == "nt" else shlex.join)([str(executable), str(script)])
    tools = create_builtin_tools(tmp_path, include_execution_tools=True)
    registry = ToolRegistry(tools)
    result = execute_tool(registry, "shell", {"command": command})
    assert result.ok, result.content
    assert str(task_env) in result.data["stdout"]
    assert "中文 ϕ" in result.data["stdout"]


def test_shell_python_requires_confirmation_in_aggressive_mode(tmp_path, monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Must not execute before approval")
    monkeypatch.setattr("raccoon.runtime.execution_sandbox.run_command", forbidden)
    tools = create_builtin_tools(tmp_path, include_execution_tools=True)
    registry = ToolRegistry(tools)
    manager = PermissionManager(policy=DefaultPermissionPolicy(tmp_path), mode=PermissionMode.AUTO)
    permissioned = partial(execute_tool, registry, permission_manager=manager)
    result = permissioned("shell", {"command": "python -c print(42)"})
    assert result.data["requires_user_input"]
    assert result.data["permission_request"]["action"] == "execute_shell"
    assert calls == []


def test_python_exec_runs_without_shell_quoting(tmp_path):
    tools = create_builtin_tools(tmp_path, include_execution_tools=True)
    registry = ToolRegistry(tools)
    result = execute_tool(registry, "python_exec", {"code": "from pathlib import Path; Path('result.txt').write_text('a & b', encoding='utf-8'); print(42)"})
    assert result.ok
    assert result.data["stdout"].strip() == "42"
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "a & b"


def test_python_exec_returns_failure_output(tmp_path):
    tools = create_builtin_tools(tmp_path, include_execution_tools=True)
    registry = ToolRegistry(tools)
    result = execute_tool(registry, "python_exec", {"code": "import sys; print('before'); print('failure', file=sys.stderr); sys.exit(2)"})
    assert not result.ok
    assert result.data["exit_code"] == 2
    assert "before" in result.content and "failure" in result.content


def test_python_exec_rejects_outside_cwd_and_sensitive_env(tmp_path):
    tools = create_builtin_tools(tmp_path, include_execution_tools=True)
    registry = ToolRegistry(tools)
    assert not execute_tool(registry, "python_exec", {"code": "print(42)", "cwd": ".."}).ok
    assert not execute_tool(registry, "python_exec", {"code": "print(42)", "env": {"OPENAI_API_KEY": "secret"}}).ok


def test_python_exec_requires_confirmation_before_execution(tmp_path):
    tools = create_builtin_tools(tmp_path, include_execution_tools=True)
    registry = ToolRegistry(tools)
    manager = PermissionManager(policy=DefaultPermissionPolicy(tmp_path), mode=PermissionMode.AUTO)
    permissioned = partial(execute_tool, registry, permission_manager=manager)
    result = permissioned("python_exec", {"code": "from pathlib import Path; Path('must-not-exist').touch()"})
    assert result.data["requires_user_input"] is True
    assert not (tmp_path / "must-not-exist").exists()
