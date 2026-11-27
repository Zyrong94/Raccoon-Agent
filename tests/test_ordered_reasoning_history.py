from openai.types.responses import Response
import asyncio
from copy import deepcopy

import pytest

from raccoon.bootstrap.agent_session import new_session
from raccoon.application.delivery.messages import display_messages
from raccoon.modules.agent.run import AgentRun
from raccoon.modules.context.compaction import CompactionSettings
from raccoon.modules.context.context_builder import ContextBuilder
from raccoon.modules.context.summary import SummaryError, serialize_messages
from raccoon.modules.sessions.errors import SessionUnsupportedSchemaError
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.types import ToolResult
from raccoon.runtime.model_provider import AuxiliaryModels
from tests.scripted_providers import FakeProvider, response as text_response
from tests.test_responses_provider import CALL, MESSAGE, REASONING, provider, response


def test_interleaved_parts_round_trip_preserves_message_boundaries_and_protocol_fields(tmp_path):
    first_message = deepcopy(MESSAGE)
    first_message["content"] = [
        {"type": "output_text", "text": "First", "annotations": []},
        {"type": "output_text", "text": " sentence.", "annotations": []},
    ]
    second_message = {**MESSAGE, "id": "msg_2", "phase": "final_answer"}
    second_reasoning = {**REASONING, "id": "rs_2", "encrypted_content": "second-ciphertext"}
    call = {**CALL, "arguments": '{ "text" : "hello" }', "async": True}
    output = [REASONING, first_message, second_reasoning, call, second_message]
    model, _ = provider(response(output, usage={"output_tokens_details": {"reasoning_tokens": 123}}))
    parsed = model._parse_response_params(Response.model_construct(**response(output, usage={"output_tokens_details": {"reasoning_tokens": 123}})))
    assert parsed.content == "First sentence.\nChecking."
    assert parsed.tool_calls[0].arguments == {"text": "hello"}

    store = SessionStore(tmp_path)
    data = store.create()
    store.append_assistant_response(data, parsed)
    restored = store.load(data.session_id)
    messages = ContextBuilder().build_provider_messages(restored)
    assert model._build_input_items(messages) == output
    assert model.estimate_replay_tokens(messages) == 123
    assert "provider_state" not in str(store.list_events(data.session_id))
    assert "opaque-state" not in serialize_messages(restored.messages)
    # Changing the single text source cannot resurrect a second copy of old text.
    next(part for part in restored.messages[0].parts if part.kind == "text").content = "Updated"
    sent = model._build_input_items(ContextBuilder().build_provider_messages(restored))
    assert sent[1]["content"][0]["text"] == "Updated"


def make_pending_history(tmp_path, summary_response):
    store = SessionStore(tmp_path)
    session = new_session(store=store)
    store.append_user_message(session.data, "Earlier task", user_timezone="UTC")
    store.append_assistant_response(session.data, text_response("Earlier result"))
    store.append_user_message(session.data, "Read the file", user_timezone="UTC")
    model, _ = provider()
    pending = model._parse_response_params(Response.model_construct(**response([REASONING, MESSAGE, {**CALL, "async": True}])))
    store.append_assistant_response(session.data, pending)
    store.append_user_message(session.data, "Continue when ready", user_timezone="UTC")
    summary = FakeProvider([summary_response, deepcopy(summary_response)])
    run = AgentRun(
        session=session, provider=model,
        auxiliary_models=AuxiliaryModels(context_summary=summary),
        compaction_settings=CompactionSettings(keep_recent_tokens=1),
    )
    return store, session, model, run


def test_successful_compaction_drops_all_old_reasoning_but_keeps_pending_async_call(tmp_path):
    store, session, model, run = make_pending_history(tmp_path, text_response("Earlier task complete"))
    record = asyncio.run(run.compact_context())
    restored = store.load(session.session_id)
    assert restored.compactions[-1].reasoning_cutoff_message_id == record.reasoning_cutoff_message_id
    assert any(part.kind == "reasoning" for message in restored.messages for part in message.parts)
    projected = ContextBuilder().build_provider_messages(restored)
    sent = model._build_input_items(projected)
    assert not any(item.get("type") == "reasoning" for item in sent)
    assert any(item.get("call_id") == "call_1" and item.get("async") for item in sent)
    assert model.estimate_replay_tokens(projected) == 0

    # A late native result still pairs with its retained call after reloading.
    call = next(call for message in projected for call in message.tool_calls)
    store.append_tool_result(restored, tool_call=call, result=ToolResult(name="echo", ok=True, content="late result"))
    fresh = {**REASONING, "id": "rs_fresh", "encrypted_content": "fresh-ciphertext"}
    store.append_assistant_response(restored, model._parse_response_params(Response.model_construct(**response([fresh, MESSAGE]))))
    sent = model._build_input_items(ContextBuilder().build_provider_messages(store.load(session.session_id)))
    assert [item for item in sent if item.get("type") == "reasoning"] == [fresh]
    assert any(item.get("type") == "function_call_output" and item["call_id"] == "call_1" for item in sent)

    # The next successful compaction also removes reasoning generated after the first.
    session.data = store.load(session.session_id)
    run.auxiliary_models = AuxiliaryModels(context_summary=FakeProvider([
        text_response("Updated summary"), text_response("Current task summary"),
    ]))
    asyncio.run(run.compact_context())
    messages = ContextBuilder().build_provider_messages(store.load(session.session_id))
    assert not any(item.get("type") == "reasoning" for item in model._build_input_items(messages))


def test_failed_compaction_does_not_clear_reasoning(tmp_path):
    store, session, model, run = make_pending_history(tmp_path, text_response(""))
    with pytest.raises(SummaryError):
        asyncio.run(run.compact_context())
    restored = store.load(session.session_id)
    assert restored.compactions == []
    sent = model._build_input_items(ContextBuilder().build_provider_messages(restored))
    assert REASONING in sent


def test_cancelled_compaction_does_not_clear_reasoning(tmp_path):
    store, session, model, run = make_pending_history(tmp_path, text_response("unused"))

    async def scenario():
        started = asyncio.Event()

        class WaitingSummary(FakeProvider):
            async def acomplete(self, request):
                started.set()
                await asyncio.Event().wait()

        run.auxiliary_models = AuxiliaryModels(context_summary=WaitingSummary([]))
        task = asyncio.create_task(run.compact_context())
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    restored = store.load(session.session_id)
    assert restored.compactions == []
    assert REASONING in model._build_input_items(ContextBuilder().build_provider_messages(restored))


def test_previous_session_schema_is_rejected_without_rewriting_the_file(tmp_path):
    store = SessionStore(tmp_path)
    data = store.create()
    path = store.sessions_dir / f"{data.session_id}.jsonl"
    original = path.read_text(encoding="utf-8").replace('"v4"', '"v3"')
    path.write_text(original, encoding="utf-8")
    with pytest.raises(SessionUnsupportedSchemaError):
        store.load(data.session_id)
    assert path.read_text(encoding="utf-8") == original


def test_reasoning_summary_is_displayed_from_parts_without_diagnostics_or_ciphertext(tmp_path):
    reasoning = {
        **REASONING,
        "summary": [{"type": "summary_text", "text": "先检查项目说明。"}],
    }
    model, _ = provider()
    result = model._parse_response_params(Response.model_construct(**response([reasoning, MESSAGE])))
    store = SessionStore(tmp_path)
    data = store.create()
    store.append_assistant_response(data, result)
    restored = store.load(data.session_id)

    assert "diagnostics" not in restored.messages[0].metadata
    assert not hasattr(result, "diagnostics")
    rows = display_messages(restored, include_reasoning=True)
    assert rows[0]["reasoning"] == "先检查项目说明。"
    assert "opaque-state" not in str(rows)
    assert "reasoning" not in display_messages(restored)[0]
