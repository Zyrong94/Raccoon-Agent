"""压缩切点和恢复投影的行为约束，不依赖旧 checkpoint/分层压缩实现。"""
import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from raccoon.modules.context.compaction import CompactionSettings, prepare_compaction, generate_compaction
from raccoon.modules.context.context_builder import ContextBuilder, InvalidCompactionBoundaryError
from raccoon.modules.context.summary import SummaryError
from raccoon.modules.sessions.compaction import CompactionRecord
from raccoon.modules.sessions.models import AgentMessage, MessagePart, SessionData
from raccoon.runtime.model_types import ChatResponse, MainRequestOptions, TokenUsage


def message(identity, role="user", *, kind="text", content="text", **metadata):
    return AgentMessage(id=identity, role=role, parts=[
        MessagePart(id="part_" + identity, message_id=identity, kind=kind, content=content, metadata=metadata),
    ])


def record(boundary):
    return CompactionRecord("compact", "session", "2026-11-16T00:00:00Z", "earlier summary", boundary, 100)


def transaction():
    return [
        message("call", "assistant", kind="tool_call", tool_call_id="tool_1", tool_name="echo", arguments={}),
        message("result", "tool", kind="tool_result", content="result", tool_call_id="tool_1", tool_name="echo", ok=True),
    ]


def test_compaction_projection_preserves_tool_pair_and_original_history():
    view = SessionData("session", messages=[message("old", content="old transcript"), *transaction()])
    original = deepcopy(view)
    prepared = prepare_compaction(view, CompactionSettings(keep_recent_tokens=1), 100)
    assert prepared.first_kept_message_id == "call"
    candidate = replace(view, compactions=[record(prepared.first_kept_message_id)])
    projected = ContextBuilder().build_provider_messages(candidate)
    assert [item.role for item in projected] == ["user", "assistant", "tool"]
    assert projected[0].content.endswith("earlier summary")
    assert projected[1].tool_calls[0].id == projected[2].tool_call_id == "tool_1"
    assert view == original


@pytest.mark.parametrize("boundary", ["result", "missing"])
def test_projection_rejects_orphan_result_or_missing_boundary(boundary):
    view = SessionData("session", messages=transaction(), compactions=[record(boundary)])
    with pytest.raises(InvalidCompactionBoundaryError):
        ContextBuilder().build_provider_messages(view)


def test_compaction_cannot_hide_pending_async_call():
    pending = message("pending", "assistant", kind="tool_call", tool_call_id="async_1", tool_name="echo", arguments={}, async_execution=True)
    view = SessionData("session", messages=[message("old"), pending, message("new")])
    prepared = prepare_compaction(view, CompactionSettings(keep_recent_tokens=1), 100)
    assert prepared.first_kept_message_id == "pending"
    with pytest.raises(InvalidCompactionBoundaryError, match="pending async"):
        ContextBuilder().build_provider_messages(replace(view, compactions=[record("new")]))


def test_compaction_keeps_undelivered_background_result_in_tail():
    pending = message("background", background_delivery="pending")
    view = SessionData("session", messages=[message("old"), pending, message("new")])
    prepared = prepare_compaction(view, CompactionSettings(keep_recent_tokens=1), 100)
    assert prepared.first_kept_message_id == "background"


def test_short_history_has_no_compaction_candidate():
    view = SessionData("session", messages=[message("only")])
    assert prepare_compaction(view, CompactionSettings(), 100) is None


def test_latest_compaction_controls_projection_after_record_round_trip():
    latest = record("new")
    latest.usage = TokenUsage(10, 5, 15)
    restored = CompactionRecord.from_dict(latest.to_dict())
    view = SessionData("session", messages=[message("old"), message("new", content="keep this")], compactions=[record("old"), restored])
    projected = ContextBuilder().build_provider_messages(view)
    assert restored == latest
    assert len(projected) == 2
    assert projected[-1].content.endswith("keep this")


@pytest.mark.parametrize("content,reason", [("", "stop"), ("partial", "length")])
def test_incomplete_summary_is_rejected_without_changing_history(content, reason):
    view = SessionData("session", messages=[message("old"), message("new")])
    original = deepcopy(view)
    prepared = prepare_compaction(view, CompactionSettings(keep_recent_tokens=1), 100)
    provider = SimpleNamespace(acomplete=AsyncMock(return_value=ChatResponse("fake", "fake", content, finish_reason=reason)))
    with pytest.raises(SummaryError):
        asyncio.run(generate_compaction(prepared, session_id="session", provider=provider, request_options=MainRequestOptions()))
    assert view == original


def test_compaction_uses_only_the_context_summary_model(tmp_path):
    from raccoon.runtime.model_provider import AuxiliaryModels
    from raccoon.bootstrap.agent_session import new_session
    from raccoon.modules.agent.run import AgentRun
    from raccoon.modules.sessions.store import SessionStore
    from tests.scripted_providers import FakeProvider, response

    store = SessionStore(tmp_path)
    session = new_session(store=store)
    store.append_user_message(session.data, "Earlier question", user_timezone="UTC")
    store.append_assistant_response(session.data, response("Earlier answer"))
    store.append_user_message(session.data, "Latest question", user_timezone="UTC")
    main = FakeProvider([])
    summary = FakeProvider([response("Earlier conversation summary")])
    main_options = MainRequestOptions(extra_body={"reasoning_effort": "high"})
    settings = CompactionSettings(keep_recent_tokens=1)

    without_summary = AgentRun(session=session, provider=main, request_options=main_options, compaction_settings=settings)
    with pytest.raises(SummaryError, match="RACCOON_CONTEXT_SUMMARY"):
        asyncio.run(without_summary.compact_context())

    run = AgentRun(
        session=session, provider=main, request_options=main_options,
        auxiliary_models=AuxiliaryModels(context_summary=summary),
        compaction_settings=settings,
    )
    result = asyncio.run(run.compact_context())

    assert result.summary == "Earlier conversation summary"
    assert main.requests == []
    assert "reasoning_effort" not in summary.requests[0].extra_body
