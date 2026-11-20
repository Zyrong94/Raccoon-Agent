"""A subagent prompt trial uses the current model without executing anything."""

import asyncio
from types import SimpleNamespace

import pytest

from raccoon.config.subagents_config import SubagentConfig
from raccoon.config.testing import test_managed_subagent as run_prompt_trial
from raccoon.runtime.model_types import MainRequestOptions, ToolCall
from tests.scripted_providers import FakeProvider, response
from tests.web_fixtures import web


def test_subagent_trial_never_executes_tools_or_changes_history(web, monkeypatch):
    _, runtime, _ = web
    config = SubagentConfig(name="specialist", system_prompt="Saved rules", tools=(), skills=())
    monkeypatch.setattr("raccoon.config.testing.load_subagent_config", lambda *args, **kwargs: config)
    provider = FakeProvider([
        response("Trial answer"),
        response("", calls=[ToolCall("unexpected", "write", {"path": "unused"})]),
    ])
    runtime.set_model(provider, request_options=MainRequestOptions(max_tokens=2048), context_window=None)
    store = runtime.session.store
    before = store.list_events(runtime.session_id)
    display = SimpleNamespace(runtime=runtime)

    first = asyncio.run(run_prompt_trial(display, "specialist", "Try this prompt"))
    assert first["output"] == "Trial answer"
    assert first["ok"]
    request = provider.requests[0]
    assert [message.content for message in request.messages] == ["Saved rules", "Try this prompt"]
    assert request.tools == [] and request.tool_choice == "none" and request.max_tokens == 512
    assert not asyncio.run(run_prompt_trial(display, "specialist", "Try again"))["ok"]
    assert store.list_events(runtime.session_id) == before


def test_subagent_trial_rejects_unknown_tool_and_skill_before_request(web, monkeypatch):
    _, runtime, _ = web
    config = SubagentConfig(name="specialist", tools=("missing-tool",), skills=("missing-skill",))
    monkeypatch.setattr("raccoon.config.testing.load_subagent_config", lambda *args, **kwargs: config)
    provider = FakeProvider([])
    runtime.set_model(provider, request_options=MainRequestOptions(), context_window=None)
    with pytest.raises(ValueError, match="missing-tool.*missing-skill"):
        asyncio.run(run_prompt_trial(SimpleNamespace(runtime=runtime), "specialist", "Trial"))
    assert provider.requests == []
