"""只读工具行为测试。"""

from __future__ import annotations

from functools import partial
from tests.tool_harness import execute_test_tool as execute_tool

from raccoon.modules.permissions.manager import PermissionManager
from raccoon.modules.permissions.policy import DefaultPermissionPolicy
import raccoon.runtime.git as git_utils
from raccoon.runtime.subprocess import CommandResult
from raccoon.runtime.sandbox import PathSandbox
import raccoon.modules.tools.grep as grep_module
from raccoon.modules.tools.registry import ToolRegistry
from raccoon.modules.tools import create_builtin_tools


def _completed(args, returncode=0, stdout="", stderr=""):
    return git_utils.subprocess.CompletedProcess(["git", *args], returncode, stdout, stderr)


def test_tree_shows_directory_structure(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme", encoding="utf-8")
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "tree", {"max_depth": 2})

    assert result.ok is True
    assert "README.md" in result.content
    assert "src/" in result.content
    assert "  src/app.py" in result.content


def test_tree_rejects_paths_outside_root(tmp_path):
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "tree", {"path": ".."})

    assert result.ok is False
    assert "超出项目目录" in result.error


def test_ls_returns_sorted_entries_and_respects_max_entries(tmp_path):
    (tmp_path / "zeta.txt").write_text("z", encoding="utf-8")
    (tmp_path / "alpha.txt").write_text("a", encoding="utf-8")
    (tmp_path / "src").mkdir()
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "ls", {"max_entries": 2})

    assert result.ok is True
    assert [entry["path"] for entry in result.data["entries"]] == ["alpha.txt", "src"]
    assert result.data["truncated"] is True


def test_view_reads_utf8_text_inside_root(tmp_path):
    target = tmp_path / "README.md"
    target.write_text("第一行\n第二行\n第三行\n", encoding="utf-8")
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "view", {"path": "README.md", "start_line": 2, "end_line": 2})

    assert result.ok is True
    assert result.content.startswith("2: 第二行")
    assert result.data["next_start_line"] == 3
    assert "start_line=3" in result.content
    assert result.data["path"] == "README.md"
    assert result.data["start_line"] == 2
    assert result.data["end_line"] == 2


def test_tools_reject_paths_outside_root(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "view", {"path": "../outside.txt"})

    assert result.ok is False
    assert "超出项目目录" in result.error


def test_sensitive_file_read_requires_permission_confirmation(tmp_path):
    secret = tmp_path / "private.key"
    secret.write_text("PRIVATE_KEY=secret\n", encoding="utf-8")
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)
    manager = PermissionManager(policy=DefaultPermissionPolicy(tmp_path))
    permissioned = partial(execute_tool, registry, permission_manager=manager)

    result = permissioned("view", {"path": "private.key"})

    assert result.ok is True
    assert result.data["requires_user_input"] is True
    assert result.data["permission_request"]["action"] == "read_path"
    assert "PRIVATE_KEY=secret" not in result.content


def test_read_multi_checks_all_requested_paths_before_reading(tmp_path):
    safe = tmp_path / "README.md"
    secret = tmp_path / "private.key"
    safe.write_text("safe\n", encoding="utf-8")
    secret.write_text("PRIVATE_KEY=secret\n", encoding="utf-8")
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)
    manager = PermissionManager(policy=DefaultPermissionPolicy(tmp_path))
    permissioned = partial(execute_tool, registry, permission_manager=manager)

    result = permissioned("read_multi", {"paths": ["README.md", "private.key"]})

    assert result.ok is True
    assert result.data["requires_user_input"] is True
    assert result.data["permission_request"]["action"] == "read_path"
    assert "PRIVATE_KEY=secret" not in result.content


def test_grep_finds_matching_lines(tmp_path):
    source = tmp_path / "raccoon.py"
    source.write_text("alpha\nRaccoon agent\nbeta\n", encoding="utf-8")
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "grep", {"pattern": "raccoon", "include": "*.py"})

    assert result.ok is True
    assert result.data["results"][0]["path"] == "raccoon.py"
    assert result.data["results"][0]["line"] == 2


def test_grep_with_rg_filters_sensitive_environment(tmp_path, monkeypatch):
    source = tmp_path / "raccoon.py"
    source.write_text("Raccoon agent\n", encoding="utf-8")
    captured = {}

    def fake_run_command(command, **kwargs):
        captured["env"] = kwargs.get("env")
        return CommandResult(
            exit_code=0,
            stdout=f"{source}:1:Raccoon agent\n",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            ok=True,
        )

    monkeypatch.setattr(grep_module.shutil, "which", lambda _name: "/usr/bin/rg")
    monkeypatch.setattr(grep_module, "run_command", fake_run_command)
    monkeypatch.setenv("SEARCH_API_KEY", "secret")
    monkeypatch.setenv("RACCOON_VISIBLE_TEST_FLAG", "visible")
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "grep", {"pattern": "raccoon", "include": "*.py"})

    assert result.ok is True
    assert "SEARCH_API_KEY" not in captured["env"]
    assert captured["env"]["RACCOON_VISIBLE_TEST_FLAG"] == "visible"


def test_grep_falls_back_to_python_when_rg_is_missing(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "raccoon.py"
    source.write_text("alpha\nRaccoon agent\nbeta\n", encoding="utf-8")
    monkeypatch.setattr(grep_module.shutil, "which", lambda _name: None)
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "grep", {"pattern": "raccoon", "include": "*.py"})

    assert result.ok is True
    assert result.data["engine"] == "python"
    assert result.data["results"][0]["path"] == "src/raccoon.py"
    assert result.data["results"][0]["line"] == 2


def test_glob_finds_matching_paths(tmp_path):
    (tmp_path / "raccoon").mkdir()
    (tmp_path / "raccoon" / "app.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme", encoding="utf-8")
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "glob", {"pattern": "**/*.py"})

    assert result.ok is True
    assert result.data["matches"] == ["raccoon/app.py"]


def test_glob_respects_max_results_after_sorting(tmp_path):
    (tmp_path / "c.py").write_text("c", encoding="utf-8")
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    (tmp_path / "b.py").write_text("b", encoding="utf-8")
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "glob", {"pattern": "*.py", "max_results": 2})

    assert result.ok is True
    assert result.data["matches"] == ["a.py", "b.py"]
    assert result.data["truncated"] is True


def test_git_status_reports_worktree_status(tmp_path, monkeypatch):
    def fake_run_git(_sandbox, args):
        if args == ["rev-parse", "--is-inside-work-tree"]:
            return _completed(args, stdout="true\n")
        if args == ["status", "--short"]:
            return _completed(args, stdout="?? README.md\n")
        return _completed(args, returncode=1, stderr="unexpected")

    monkeypatch.setattr(git_utils, "run_git", fake_run_git)
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "git_status")

    assert result.ok is True
    assert "?? README.md" in result.content
    assert result.data["clean"] is False


def test_git_status_returns_error_outside_git_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(
        git_utils,
        "run_git",
        lambda _sandbox, args: _completed(args, returncode=1, stderr="not a git repo"),
    )
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "git_status")

    assert result.ok is False
    assert "不是 git 仓库" in result.error


def test_git_diff_reports_changes(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_text("new\n", encoding="utf-8")

    def fake_run_git(_sandbox, args):
        if args == ["rev-parse", "--is-inside-work-tree"]:
            return _completed(args, stdout="true\n")
        if args == ["diff", "--", "README.md"]:
            return _completed(args, stdout="-old\n+new\n")
        return _completed(args, returncode=1, stderr="unexpected")

    monkeypatch.setattr(git_utils, "run_git", fake_run_git)
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "git_diff", {"path": "README.md"})

    assert result.ok is True
    assert "-old" in result.content
    assert "+new" in result.content
    assert result.data["path"] == "README.md"


def test_git_diff_returns_error_outside_git_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(
        git_utils,
        "run_git",
        lambda _sandbox, args: _completed(args, returncode=1, stderr="not a git repo"),
    )
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "git_diff")

    assert result.ok is False
    assert "不是 git 仓库" in result.error


def test_git_diff_truncates_large_diff(tmp_path, monkeypatch):
    def fake_run_git(_sandbox, args):
        if args == ["rev-parse", "--is-inside-work-tree"]:
            return _completed(args, stdout="true\n")
        if args == ["diff", "--", "."]:
            return _completed(args, stdout="abcdef")
        return _completed(args, returncode=1, stderr="unexpected")

    monkeypatch.setattr(git_utils, "run_git", fake_run_git)
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "git_diff", {"max_chars": 3})

    assert result.ok is True
    assert result.content == "abc\n\n[diff 已截断]"
    assert result.data["truncated"] is True


def test_git_diff_rejects_path_outside_root(tmp_path):
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)

    result = execute_tool(registry, "git_diff", {"path": "../outside.txt"})

    assert result.ok is False
    assert "超出项目目录" in result.error


def test_view_character_cap_continuation_has_no_missing_lines(tmp_path):
    lines = [str(i) + "中文" * 250 for i in range(350)]
    (tmp_path / "long.txt").write_text("\n".join(lines), encoding="utf-8")
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)
    start, received = 1, []
    while start is not None:
        result = execute_tool(registry, "view", {"path": "long.txt", "start_line": start})
        assert result.ok
        assert len(result.content) <= 50000
        end = result.data["end_line"]
        received.extend(range(start, end + 1))
        for i in range(start, end + 1):
            assert f"{i}: {lines[i-1]}" in result.content
        next_start = result.data["next_start_line"]
        if next_start is not None:
            assert next_start == end + 1
            assert f"start_line={next_start}" in result.content
        start = next_start
    assert received == list(range(1, 351))


def test_view_oversized_line_gives_actionable_error(tmp_path):
    (tmp_path / "long.txt").write_text("x" * 60000, encoding="utf-8")
    result = execute_tool(ToolRegistry(create_builtin_tools(tmp_path)), "view", {"path": "long.txt"})
    assert not result.ok
    assert "shell" in result.content
    assert len(result.content) <= 50000


def test_view_rejects_invalid_line_ranges(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)
    for args in ({"start_line": 0}, {"end_line": 0}, {"start_line": 3, "end_line": 2}):
        assert not execute_tool(registry, "view", {"path": "a.txt", **args}).ok
