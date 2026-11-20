"""Exercise pending inputs and cancellation with a live session worker."""

import asyncio

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


def test_unread_follow_up_runs_once_after_older_queued_message(tmp_path):
    async def scenario():
        service = ConversationService()
        provider = FakeProvider(
            [response("First answer"), response("Queued answer"), response("An example")],
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

            result = await asyncio.wait_for(service.wait(late), timeout=5)
            assert result.content == "An example"
            assert first.response.content == "First answer"
            assert queued.response.content == "Queued answer"
            assert service.submit_event(follow_up, key) is late
            assert classified == [late.event.id]
            assert notified == [first.event.id, queued.event.id, late.event.id]
            assert len(provider.requests) == 3
            for request in provider.requests[:2]:
                assert all("Add an example" not in item.content for item in request.messages)
            assert sum("Add an example" in item.content for item in provider.requests[-1].messages) == 1
            stored = store.load(session.session_id).messages
            assert [item.metadata["source_event_id"] for item in stored if "source_event_id" in item.metadata] == [
                first.event.id, queued.event.id, late.event.id,
            ]
        finally:
            provider.release.set()
            await service.close()

    asyncio.run(scenario())
