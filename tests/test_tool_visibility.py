from dataclasses import replace

import pytest

from raccoon.bootstrap.agent_session import new_session
from raccoon.modules.agent.run import AgentRun
from raccoon.modules.agent.runtime_capabilities import AgentRuntimeCapabilities
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.types import Tool, make_text_result
from raccoon.runtime.model_types import ProviderCapabilities, ToolCall, ToolDefinition
from tests.scripted_providers import FakeProvider


@pytest.mark.parametrize("name, capability", [
    ("think", "expose_think_tool"),
    ("ask_user", "allow_user_input"),
    ("web_search", "expose_web_search_tool"),
    ("task_create", "expose_planning_tools"),
    ("task_update", "expose_planning_tools"),
    ("task_revise", "expose_planning_tools"),
    ("task_list", "expose_planning_tools"),
    ("delegate", "enable_delegate_tool"),
    ("view_image", "supports_vision"),
])
def test_hidden_tool_is_rejected_with_the_same_reason_and_can_be_enabled(tmp_path, name, capability):
    class Provider(FakeProvider):
        capabilities = ProviderCapabilities(supports_tools=True, supports_vision=False)

    tool = Tool(ToolDefinition(name, "Example", {"type": "object"}), lambda: make_text_result(name, "ok"))
    session = new_session(store=SessionStore(tmp_path), tools=[tool], include_session_tools=False)
    settings = AgentRuntimeCapabilities()
    if capability != "supports_vision":
        settings = replace(settings, **{capability: False})
    run = AgentRun(session=session, provider=Provider([]), runtime_capabilities=settings)
    reason = run._tool_unavailability_reason(name)
    assert reason
    assert name not in {item.name for item in run._discoverable_tool_definitions()}
    error = run._validate_tool_call(ToolCall("call", name, {}))
    assert not error.ok
    assert error.error == reason
    assert error.data["tool_not_routed"] is True

    if capability == "supports_vision":
        run.provider.capabilities = ProviderCapabilities(supports_tools=True, supports_vision=True)
    else:
        run.runtime_capabilities = replace(settings, **{capability: True})
        if capability == "enable_delegate_tool":
            run.enable_delegate_tool = True
    assert run._tool_unavailability_reason(name) is None
    assert name in {item.name for item in run._discoverable_tool_definitions()}
    assert run._validate_tool_call(ToolCall("call", name, {})) is None
