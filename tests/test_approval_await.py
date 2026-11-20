"""Exercise real file approval through the live conversation service."""

import asyncio

import pytest

from raccoon.app.conversations import ConversationService
from raccoon.app.session_runtime import SessionRuntime
from raccoon.bootstrap.agent_session import new_session
from raccoon.modules.events import EventSourceAdapter
from raccoon.modules.events.models import EventError
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.write import create_write_tool
from raccoon.runtime.model_types import ToolCall
from tests.scripted_providers import FakeProvider, response


@pytest.mark.parametrize("outcome", ["allow", "deny", "changed", "cancel"])
def test_write_approval_stays_bound_to_the_original_live_request(tmp_path, outcome):
    async def scenario():
        tool = create_write_tool(tmp_path)
        store = SessionStore(tmp_path / "history")
        session = new_session(store=store, session_id="chat", project_root=tmp_path,
                              tools=[tool], user_timezone="UTC")
        target = tmp_path / "note.txt"
        provider = FakeProvider([
            response("", calls=[ToolCall("write-1", "write", {"path": target.name, "content": "approved"})]),
            response("Done"),
        ])
        loop = asyncio.get_running_loop()
        waiting = loop.create_future()

        def notify(request, answer):
            if request is not None and answer is None:
                loop.call_soon_threadsafe(waiting.set_result, request)

        runtime = SessionRuntime(session=session, provider=provider, tools=[tool],
                                 input_event_handler=notify)
        service = ConversationService()
        key = service.register(runtime)
        source = EventSourceAdapter(main_session_id="chat")
        try:
            event = source.user_message(source="web", message_id="write-msg",
                                        conversation_id="chat", text="Write a note")
            receipt = service.submit_event(event, key)
            request = await asyncio.wait_for(waiting, timeout=5)
            assert request.kind == "permission_confirmation"
            assert not receipt.finished.is_set() and not target.exists()
            with pytest.raises(EventError):
                service.answer_run("chat", "another-request", "allow_once", key=key)

            if outcome == "cancel":
                service.stop_session(key)
            else:
                if outcome == "changed":
                    target.write_text("changed by user", encoding="utf-8")
                answer = "deny" if outcome == "deny" else "allow_once"
                service.answer_run("chat", request.id, answer, key=key)
            await asyncio.wait_for(receipt.finished.wait(), timeout=5)
            with pytest.raises(EventError):
                service.answer_run("chat", request.id, "allow_once", key=key)

            if outcome == "allow":
                assert target.read_text(encoding="utf-8") == "approved"
            elif outcome == "changed":
                assert target.read_text(encoding="utf-8") == "changed by user"
            else:
                assert not target.exists()
            assert receipt.status == ("cancelled" if outcome == "cancel" else "succeeded")
            results = [part for message in store.load("chat").messages for part in message.parts
                       if part.kind == "tool_result"]
            assert len(results) == 1
            assert results[0].metadata["tool_call_id"] == "write-1"
            assert results[0].metadata["ok"] == (outcome == "allow")
        finally:
            await service.close()

    asyncio.run(scenario())
