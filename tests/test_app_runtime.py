"""Exercise pending inputs and cancellation with a live session worker."""

import asyncio
import threading

from raccoon.app.conversations import ConversationService
from raccoon.app.session_runtime import SessionRuntime
from raccoon.bootstrap.agent_session import new_session
from raccoon.modules.events import EventSourceAdapter
from raccoon.modules.sessions.store import SessionStore
from tests.scripted_providers import FakeProvider, response


def test_stop_keeps_session_owned_until_its_worker_returns(tmp_path):
    class BlockedWorker(FakeProvider):
        async def acomplete(self, request):
            # This callback runs in the session worker. Release it explicitly.
            return self.complete(request)

    async def scenario():
        service = ConversationService()
        provider = BlockedWorker([response("Should be discarded")], block_first=True)
        session = new_session(store=SessionStore(tmp_path))
        key = service.register(SessionRuntime(session, provider))
        source = EventSourceAdapter(main_session_id=key.session_id)
        event = source.user_message(
            source="local", message_id="start", conversation_id=key.session_id, text="Start",
        )
        try:
            receipt = service.submit_event(event, key)
            assert await asyncio.to_thread(provider.entered.wait, 5)
            follow_up = service.submit_event(source.user_message(
                source="local", message_id="late", conversation_id=key.session_id, text="Add an example",
            ), key)
            await asyncio.wait_for(service.dispatch_tasks[key], timeout=5)
            assert follow_up.status == "delivered"
            service.stop_session(key)
            assert follow_up.status == "cancelled"
            assert follow_up.finished.is_set()
            assert service.busy(key)
            assert not receipt.finished.is_set()
            provider.release.set()
            await asyncio.wait_for(receipt.finished.wait(), timeout=5)
            assert receipt.status == "cancelled"
            assert not service.busy(key)
            assert len(provider.requests) == 1
        finally:
            provider.release.set()
            await service.close()

    asyncio.run(scenario())


def test_messages_submitted_during_classification_reach_the_same_run_in_order(tmp_path):
    async def scenario():
        service = ConversationService()
        provider = FakeProvider([response("Draft"), response("Final")], block_first=True)
        session = new_session(store=SessionStore(tmp_path))
        deciding = asyncio.Event()
        release_decision = asyncio.Event()
        all_routed = asyncio.Event()
        decisions = []

        async def classify(event, snapshot):
            decisions.append(event.id)
            if len(decisions) == 1:
                deciding.set()
                await release_decision.wait()
            else:
                all_routed.set()
            return "steer"

        key = service.register(SessionRuntime(session, provider), classifier=classify)
        source = EventSourceAdapter(main_session_id=key.session_id)

        def submit(text):
            event = source.user_message(source="web", message_id=text, conversation_id=key.session_id, text=text)
            event.payload["mode"] = "auto"
            return service.submit_event(event, key)

        try:
            first = submit("Start")
            assert await asyncio.to_thread(provider.entered.wait, 5)
            second = submit("Windows")
            await asyncio.wait_for(deciding.wait(), 5)
            third = submit("PowerShell")
            release_decision.set()
            await asyncio.wait_for(all_routed.wait(), 5)
            assert [second.status, third.status] == ["delivered", "delivered"]
            provider.release.set()
            await asyncio.wait_for(service.wait_for_event(first), 5)
            assert first.response is second.response is third.response
            requested = []
            for message in provider.requests[1].messages:
                if message.role == "user" and isinstance(message.content, str):
                    requested.extend(line for line in message.content.splitlines()
                                     if line in {"Start", "Windows", "PowerShell"})
            assert requested == ["Start", "Windows", "PowerShell"]
        finally:
            release_decision.set()
            provider.release.set()
            await service.close()

    asyncio.run(scenario())


def test_message_after_input_closes_starts_a_new_turn(tmp_path):
    async def scenario():
        publishing = threading.Event()
        release = threading.Event()

        def on_response(answer):
            if answer.content == "First answer":
                publishing.set()
                assert release.wait(5)

        service = ConversationService()
        provider = FakeProvider([response("First answer"), response("Second answer")])
        session = new_session(store=SessionStore(tmp_path))
        key = service.register(SessionRuntime(session, provider, response_handler=on_response))
        source = EventSourceAdapter(main_session_id=key.session_id)
        try:
            first = service.submit_event(source.user_message(
                source="local", message_id="first", conversation_id=key.session_id, text="First",
            ), key)
            assert await asyncio.to_thread(publishing.wait, 5)
            assert not service.runs[key].accepting_inputs
            later = service.submit_event(source.user_message(
                source="local", message_id="second", conversation_id=key.session_id, text="Second",
            ), key)
            await asyncio.wait_for(service.dispatch_tasks[key], timeout=5)
            assert later.status == "queued"
            release.set()
            assert (await asyncio.wait_for(service.wait_for_event(later), timeout=5)).content == "Second answer"
            assert first.response.content == "First answer"
        finally:
            release.set()
            await service.close()

    asyncio.run(scenario())


def test_steered_input_is_included_before_final_reply_and_queued_task_runs_next(tmp_path):
    async def scenario():
        service = ConversationService()
        provider = FakeProvider(
            [response("Draft"), response("An example"), response("Queued answer")],
            block_first=True,
        )
        store = SessionStore(tmp_path)
        session = new_session(store=store)
        classified = []
        notified = []

        async def classify(event, snapshot):
            classified.append(event.id)
            return "steer"

        key = service.register(
            SessionRuntime(session, provider), classifier=classify,
            on_user_event=lambda event: notified.append(event.id),
        )
        source = EventSourceAdapter(main_session_id=key.session_id)

        def message(message_id, text, mode):
            event = source.user_message(
                source="web", message_id=message_id, conversation_id=key.session_id, text=text,
            )
            event.payload["mode"] = mode
            return event

        try:
            first = service.submit_event(message("first", "Explain the code", "auto"), key)
            assert await asyncio.to_thread(provider.entered.wait, 5)
            queued = service.submit_event(message("queued", "Explain the file layout", "follow_up"), key)
            follow_up = message("follow-up", "Add an example", "auto")
            late = service.submit_event(follow_up, key)
            await asyncio.wait_for(service.dispatch_tasks[key], timeout=5)
            assert late.status == "delivered"
            assert notified == [first.event.id]
            provider.release.set()

            result = await asyncio.wait_for(service.wait_for_event(late), timeout=5)
            assert result.content == "An example"
            await asyncio.wait_for(service.wait_for_event(queued), timeout=5)
            assert first.response is late.response
            assert late.status == "consumed"
            assert queued.response.content == "Queued answer"
            assert service.submit_event(follow_up, key) is late
            assert classified == [late.event.id]
            assert notified == [first.event.id, late.event.id, queued.event.id]
            assert len(provider.requests) == 3
            assert all("Add an example" not in item.content for item in provider.requests[0].messages)
            assert sum("Add an example" in item.content for item in provider.requests[1].messages) == 1
            assert all(item.content != "Draft" for request in provider.requests for item in request.messages)
            stored = store.load(session.session_id).messages
            assert [item.metadata["source_event_id"] for item in stored if "source_event_id" in item.metadata] == [
                first.event.id, late.event.id, queued.event.id,
            ]
        finally:
            provider.release.set()
            await service.close()

    asyncio.run(scenario())
