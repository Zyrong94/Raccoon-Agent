"""Runtime creation is shared and independent of Web display state."""
import asyncio
from types import SimpleNamespace
import pytest

from raccoon.app.conversations import ConversationService, SessionKey
from raccoon.modules.events import EventSourceAdapter


def test_duplicate_message_is_queued_once_and_conflicting_content_is_rejected(tmp_path):
    async def run():
        service = ConversationService()
        session = SimpleNamespace(store=SimpleNamespace(root=tmp_path), session_id='chat',
                                  data=SimpleNamespace(metadata={}))
        key = service.register(SimpleNamespace(session=session, background_manager=None, close=lambda: None))
        source = EventSourceAdapter(main_session_id='chat')
        def message(text):
            return source.user_message(source='web', message_id='one', conversation_id='chat', text=text)
        try:
            receipt = service.submit_event(message('hello'), key)
            assert service.submit_event(message('hello'), key) is receipt
            with pytest.raises(ValueError, match='同一事件 ID'):
                service.submit_event(message('different'), key)
            assert list(service.queues[key]) == [receipt]
        finally:
            await service.close()
    asyncio.run(run())


def test_concurrent_preparation_reuses_one_runtime_and_close_releases_it(tmp_path):
    calls, closed = [], []
    def factory(**options):
        calls.append(options)
        return SimpleNamespace(session=SimpleNamespace(store=SimpleNamespace(root=tmp_path), session_id='chat', data=SimpleNamespace(metadata={})),
                               background_manager=None, last_pending_input=None,
                               close=lambda: closed.append('chat'))
    async def run():
        service = ConversationService()
        options = {'project_root': tmp_path, 'data_root': tmp_path, 'session_id': 'chat'}
        first, second = await asyncio.gather(service.get_or_create_session(factory, **options),
                                             service.get_or_create_session(factory, **options))
        assert first is second
        assert len(calls) == 1
        assert service.get_loaded_session(SessionKey(tmp_path.resolve(), 'chat')) is first
        await service.close_session(SessionKey(tmp_path.resolve(), 'chat'))
        assert service.sessions == {}
        assert closed == ['chat']
    asyncio.run(run())


def test_ordinary_answer_resumes_the_waiting_turn(tmp_path):
    from raccoon.app.session_runtime import SessionRuntime
    from raccoon.bootstrap.agent_session import new_session
    from raccoon.modules.sessions.store import SessionStore
    from raccoon.modules.tools.ask_user import create_ask_user_tool
    from raccoon.runtime.model_types import ToolCall
    from tests.scripted_providers import FakeProvider, response

    async def run():
        tool = create_ask_user_tool()
        store = SessionStore(tmp_path)
        session = new_session(store=store, session_id="chat", tools=[tool])
        provider = FakeProvider([
            response("", calls=[ToolCall("ask", "ask_user", {"question": "Continue?"})]),
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
            event = source.user_message(source="web", message_id="message",
                                        conversation_id="chat", text="Please ask first.")
            receipt = service.submit_event(event, key)
            request = await asyncio.wait_for(waiting, timeout=5)
            active_turn = runtime.active_run
            assert not receipt.finished.is_set()
            assert active_turn is not None
            service.answer_run("chat", request.id, "Yes", key=key)
            await asyncio.wait_for(receipt.finished.wait(), timeout=5)
            assert receipt.status == "succeeded"
            assert receipt.response.content == "Done"
            assert len(provider.requests) == 2
            assert any("Yes" in (message.content or "") for message in provider.requests[-1].messages
                       if message.role == "tool")
        finally:
            await service.close()

    asyncio.run(run())


def test_stopping_one_session_clears_its_queue_without_blocking_another(tmp_path):
    from raccoon.app.session_runtime import SessionRuntime
    from raccoon.bootstrap.agent_session import new_session
    from raccoon.modules.sessions.store import SessionStore
    from tests.scripted_providers import FakeProvider, response

    async def run():
        store = SessionStore(tmp_path)
        service = ConversationService()
        slow = FakeProvider([response("First")], block_first=True)
        fast = FakeProvider([response("Second")])
        first = service.register(SessionRuntime(new_session(store=store, session_id="first"), slow))
        second = service.register(SessionRuntime(new_session(store=store, session_id="second"), fast))

        def submit(key, message_id, text):
            source = EventSourceAdapter(main_session_id=key.session_id)
            event = source.user_message(source="web", message_id=message_id, conversation_id=key.session_id, text=text)
            event.payload["mode"] = "follow_up"
            return service.submit_event(event, key)

        try:
            active = submit(first, "start", "Start the first task")
            assert await asyncio.to_thread(slow.entered.wait, 5)
            queued = submit(first, "later", "This should stay queued")
            independent = submit(second, "independent", "Run another task")
            await asyncio.wait_for(independent.finished.wait(), timeout=5)
            assert independent.status == "succeeded" and not active.finished.is_set()
            service.stop_session(first)
            slow.release.set()
            await asyncio.wait_for(active.finished.wait(), timeout=5)
            assert active.status == queued.status == "cancelled"
            assert len(slow.requests) == 1
        finally:
            slow.release.set()
            await service.close()

    asyncio.run(run())
