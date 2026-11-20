"""工具层 registry 和通用行为测试。"""

from __future__ import annotations

from tests.tool_harness import execute_session_tool

from raccoon.modules.tools.execution import execute_tool

import pytest

from raccoon.runtime.processes import ProcessManager
from raccoon.runtime.model_types import ToolDefinition
from raccoon.modules.tools import ToolRegistry, create_builtin_registry
from raccoon.modules.tools.session_registry import create_session_tool_registry
from raccoon.modules.tools.types import Tool, make_text_result


def test_builtin_tool_descriptions_are_agent_facing_english(tmp_path):
    registry = create_builtin_registry(
        tmp_path,
        include_mutation_tools=True,
        include_execution_tools=True,
        include_network_tools=True,
    )
    descriptions = {definition.name: definition.description for definition in registry.definitions()}

    assert descriptions["view"].startswith("Read a UTF-8 text file")
    assert "Use this instead of shell commands like cat" in descriptions["view"]
    assert descriptions["grep"].startswith("Search file contents")
    assert "literal text" in descriptions["grep"]
    assert descriptions["apply_patch"].startswith("Apply a structured patch")
    assert "Prefer this for multi-file edits" in descriptions["apply_patch"]
    assert descriptions["shell"].startswith("Run a shell command")
    assert "Prefer dedicated tools" in descriptions["shell"]
    assert "evidence returned to the model" in descriptions["diagnostics"]


def test_builtin_registry_contains_read_only_tools(tmp_path):
    registry = create_builtin_registry(tmp_path)

    assert registry.names() == [
        "ls",
        "view",
        "grep",
        "glob",
        "tree",
        "git_status",
        "git_diff",
        "git_log",
        "diagnostics",
        "think",
        "read_multi",
        "view_image",
        "ask_user",
    ]
    assert [definition.name for definition in registry.definitions()] == registry.names()
    assert [tool.name for tool in registry.tools()] == registry.names()

    definitions = {definition.name: definition for definition in registry.definitions()}
    assert definitions["view"].parameters == {
        "type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
        "required": ["path"],
    }
    assert definitions["grep"].parameters["required"] == ["pattern"]
    assert definitions["glob"].parameters["required"] == ["pattern"]


def test_builtin_registry_can_hide_ask_user_for_noninteractive_runs(tmp_path):
    registry = create_builtin_registry(tmp_path, include_ask_user=False)

    assert "ask_user" not in registry.names()
    assert registry.names() == [
        "ls",
        "view",
        "grep",
        "glob",
        "tree",
        "git_status",
        "git_diff",
        "git_log",
        "diagnostics",
        "think",
        "read_multi",
        "view_image",
    ]


def test_builtin_registry_can_include_mutation_tools_when_explicitly_enabled(tmp_path):
    registry = create_builtin_registry(tmp_path, include_mutation_tools=True)

    assert registry.names() == [
        "ls",
        "view",
        "grep",
        "glob",
        "tree",
        "git_status",
        "git_diff",
        "git_log",
        "diagnostics",
        "think",
        "read_multi",
        "view_image",
        "ask_user",
        "write",
        "edit",
        "delete",
        "apply_patch",
    ]


def test_builtin_registry_can_include_network_tools_when_explicitly_enabled(tmp_path):
    registry = create_builtin_registry(tmp_path, include_network_tools=True)

    assert registry.names() == [
        "ls",
        "view",
        "grep",
        "glob",
        "tree",
        "git_status",
        "git_diff",
        "git_log",
        "diagnostics",
        "think",
        "read_multi",
        "view_image",
        "ask_user",
        "web_fetch",
        "web_search",
        "search_papers",
    ]


def test_builtin_registry_can_keep_fetch_while_hiding_web_search(tmp_path):
    registry = create_builtin_registry(
        tmp_path,
        include_network_tools=True,
        include_web_search=False,
    )

    assert "web_fetch" in registry.names()
    assert "web_search" not in registry.names()


def test_builtin_registry_can_include_execution_tools_when_explicitly_enabled(tmp_path):
    registry = create_builtin_registry(tmp_path, include_execution_tools=True)

    assert registry.names() == [
        "ls",
        "view",
        "grep",
        "glob",
        "tree",
        "git_status",
        "git_diff",
        "git_log",
        "diagnostics",
        "think",
        "read_multi",
        "view_image",
        "ask_user",
        "shell",
        "python_exec",
    ]

    result = execute_tool(registry, "python_exec", {"code": "print(42)"})
    assert result.ok
    assert result.data["stdout"].strip() == "42"


def test_builtin_registry_includes_process_tools_when_manager_is_supplied(tmp_path):
    manager = ProcessManager(log_root=tmp_path / ".raccoon" / "processes")
    registry = create_builtin_registry(
        tmp_path,
        include_execution_tools=True,
        process_manager=manager,
    )

    assert registry.names()[-6:] == [
        "shell",
        "python_exec",
        "process_start",
        "process_status",
        "process_logs",
        "process_stop",
    ]


def test_registry_returns_error_for_unknown_tool():
    registry = ToolRegistry()

    result = execute_tool(registry, "missing_tool", {})

    assert result.ok is False
    assert result.error == "未知工具：missing_tool"


def test_tool_refresh_preserves_session_tools_and_session_authorization(tmp_path):
    from raccoon.bootstrap.agent_session import new_session
    from raccoon.modules.sessions.store import SessionStore
    from raccoon.runtime.model_types import ToolCall

    def tool(name, content):
        return Tool(ToolDefinition(name, content, {"type": "object"}), lambda: make_text_result(name, content))

    old, denied = tool("lookup", "old"), tool("forbidden", "denied")
    session = new_session(store=SessionStore(tmp_path), session_id="allowed", tools=[old, denied], include_session_tools=False)
    session.allowed_tool_names = {"lookup", "session_control"}
    registry = session.tool_registry
    control = tool("session_control", "bound")
    registry.register(control)
    registry.register(tool("temporary", "temporary"), supplied=True)
    updated = tool("lookup", "updated")
    registry.replace_supplied_tools([updated, denied])
    assert registry.get("session_control") is control
    assert registry.get("temporary") is None
    assert not execute_session_tool(session, ToolCall("denied", "forbidden", {})).ok
    assert execute_session_tool(session, ToolCall("ok", "lookup", {})).content == "updated"
    registry.replace_supplied_tools([])
    assert registry.tools() == [control]


def test_session_registry_adds_four_authoritative_task_plan_tools(tmp_path):
    from raccoon.modules.sessions.store import SessionStore

    store = SessionStore(tmp_path)
    registry = create_session_tool_registry(data=store.create(), store=store)

    for name in ("task_create", "task_update", "task_revise", "task_list", "load_skill"):
        assert name in registry.names()
    descriptions = {definition.name: definition.description for definition in registry.definitions()}
    assert "stable task ID" in descriptions["task_update"]
    assert "wording" in descriptions["task_revise"]


@pytest.mark.parametrize(
    "reserved_name",
    ["task_create", "task_update", "task_revise", "task_list", "session_status", "load_skill"],
)
def test_session_registry_rejects_supplied_task_plan_tool_override(
    tmp_path,
    reserved_name: str,
) -> None:
    supplied = Tool(
        definition=ToolDefinition(
            name=reserved_name,
            description="fake",
            parameters={"type": "object", "properties": {}},
        ),
        executor=lambda: make_text_result(reserved_name, "fake"),
    )

    from raccoon.modules.sessions.store import SessionStore
    store = SessionStore(tmp_path)
    with pytest.raises(ValueError, match="reserved"):
        create_session_tool_registry(data=store.create(), store=store, tools=[supplied])
