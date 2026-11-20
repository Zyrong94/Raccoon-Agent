"""Original command output behavior: bounded previews without automatic files."""

from raccoon.modules.tools.execution import execute_tool

import pytest
from raccoon.modules.tools.builtin import create_builtin_registry


@pytest.mark.parametrize("tool_name", ["shell", "python_exec"])
@pytest.mark.parametrize("exit_code", [0, 2])
def test_command_output_is_truncated_without_automatic_archive(tmp_path, tool_name, exit_code):
    registry = create_builtin_registry(tmp_path, include_execution_tools=True)
    if tool_name == "shell":
        args = {"command": f"echo HEAD {'x'*500} TAIL & exit {exit_code}"} if __import__("os").name == "nt" else {"command": f"echo HEAD {'x'*500} TAIL; exit {exit_code}"}
    else:
        args = {"code": f"import sys; print('HEAD' + 'x'*500 + 'TAIL'); sys.exit({exit_code})"}
    result = execute_tool(registry, tool_name, {**args, "max_output_chars": 100})
    assert result.ok is (exit_code == 0)
    assert result.data["stdout_truncated"] is True
    assert "HEAD" in result.data["stdout"] and "TAIL" in result.data["stdout"]
    assert "stdout_path" not in result.data and "output_save_error" not in result.data
    assert "完整 stdout 文本已保存" not in result.content
    assert "需要这些内容才能回答时，应先补读" not in result.content
    assert not (tmp_path / ".raccoon" / "tool-outputs").exists()
