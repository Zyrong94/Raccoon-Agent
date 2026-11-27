import pytest

from raccoon.bootstrap.agent_session import new_session
from raccoon.modules.agent.run import AgentRun
from raccoon.modules.agent.tool_execution import _dispatch_background
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.builtin import create_builtin_tools
from raccoon.modules.tools.delegate import create_delegate_tool
from raccoon.modules.tools.types import Tool, make_text_result
from raccoon.modules.agent.background import BackgroundJobManager
from raccoon.runtime.model_types import ToolCall, ToolDefinition
from tests.scripted_providers import AsyncProvider


def test_builtin_background_flags_match_the_selected_tools(tmp_path):
    tools = create_builtin_tools(
        tmp_path, include_mutation_tools=True, include_execution_tools=True, include_network_tools=True,
    )
    tools.append(create_delegate_tool(None))
    assert {tool.name for tool in tools if tool.supports_background} == {
        "ls", "tree", "view", "read_multi", "grep", "glob", "git_status", "git_diff", "git_log",
        "diagnostics", "shell", "python_exec", "web_fetch", "web_search", "search_papers", "delegate",
    }


@pytest.mark.parametrize("native", [False, True])
def test_background_declaration_uses_tool_flag_instead_of_name(tmp_path, native):
    manager = BackgroundJobManager()
    try:
        enabled = Tool(
            ToolDefinition("custom_slow_tool", "Slow tool", {"type": "object"}),
            lambda: make_text_result("custom_slow_tool", "done"), supports_background=True,
        )
        disabled = Tool(
            ToolDefinition("view", "Custom view", {"type": "object"}),
            lambda: pytest.fail("Disabled tool must not execute in the background"),
        )
        session = new_session(store=SessionStore(tmp_path), tools=[enabled, disabled], background_manager=manager)
        run = AgentRun(session=session, provider=AsyncProvider([]), background_manager=manager, native_async_tools=native)
        definition = run._augment_tool_definition(enabled.definition)
        assert definition.async_execution is native
        assert ("run_in_background" in definition.parameters["properties"]) is (not native)
        assert "task_handle" in definition.parameters["properties"]
        assert enabled.definition.parameters == {"type": "object"}
        assert run._augment_tool_definition(disabled.definition) is disabled.definition
        result = _dispatch_background(run, ToolCall("denied", "view", {}), label=None, task_id=None, native=native)
        assert not result.ok
        assert result.data["background_rejected"] == "not_allowed"
        assert manager.list(session_id=session.session_id) == []
        session.allow_background_execution = False
        assert run._augment_tool_definition(enabled.definition) is enabled.definition
    finally:
        manager.shutdown()
