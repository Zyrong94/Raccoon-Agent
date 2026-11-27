"""Exercise the tool loop and task-plan finish behavior."""

import asyncio

from raccoon.app.payloads import MessageInput
from raccoon.app.session_runtime import SessionRuntime
from raccoon.bootstrap.agent_session import new_session
from raccoon.modules.planning.service import TaskPlanService
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.types import Tool, make_text_result
from raccoon.runtime.model_types import ToolCall, ToolDefinition
from tests.scripted_providers import FakeProvider, response


def test_agent_continues_after_saving_the_executed_tool_result(tmp_path):
    tool = Tool(ToolDefinition("echo", "Echo", {"type": "object"}), lambda: make_text_result("echo", "done"))
    store = SessionStore(tmp_path)
    session = new_session(store=store, tools=[tool])
    provider = FakeProvider([response("", calls=[ToolCall("call", "echo", {})]), response("Finished")])
    runtime = SessionRuntime(session, provider, tools=[tool])
    try:
        result = asyncio.run(runtime.run_turn(MessageInput("start", "Use echo"), lambda: ()))
    finally:
        runtime.close()
    assert result.content == "Finished"
    assert len(provider.requests) == 2
    history = store.load(session.session_id).messages
    tool_results = [part for message in history for part in message.parts if part.kind == "tool_result"]
    assert [part.metadata["tool_call_id"] for part in tool_results] == ["call"]


def test_unfinished_plan_gets_one_final_check_without_writing_extra_user_input(tmp_path):
    store = SessionStore(tmp_path)
    session = new_session(store=store)
    TaskPlanService(store=store, data=session.data).create(
        mode="linear", expected_revision=0, tasks=[{"id": "work", "content": "Review the change"}],
    )
    provider = FakeProvider([response("Candidate"), response("Final answer")])
    runtime = SessionRuntime(session, provider)
    try:
        result = asyncio.run(runtime.run_turn(MessageInput("start", "Review"), lambda: ()))
    finally:
        runtime.close()
    assert result.content == "Final answer"
    assert len(provider.requests) == 2
    roles = [
        message.role for message in store.load(session.session_id).messages
        if message.metadata.get("source_kind") != "agent_status"
    ]
    assert roles == ["user", "assistant"]
