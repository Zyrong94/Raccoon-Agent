"""A finished background job wakes its own conversation and is recorded once."""

import asyncio

from raccoon.app.conversations import ConversationService
from raccoon.app.session_runtime import SessionRuntime
from raccoon.bootstrap.agent_session import new_session
from raccoon.modules.agent.background import BackgroundJobManager
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.types import make_text_result
from tests.scripted_providers import FakeProvider, response


def test_background_completion_wakes_the_owner_only_once(tmp_path):
    async def scenario():
        manager = BackgroundJobManager()
        store = SessionStore(tmp_path)
        session = new_session(store=store)
        provider = FakeProvider([response("Reported the result")])
        runtime = SessionRuntime(session, provider, background_manager=manager)
        service = ConversationService()
        finished = asyncio.Event()
        key = service.register(runtime, on_finish=lambda receipt: finished.set())
        service.start()
        try:
            job = manager.start(lambda: make_text_result("view", "Evidence from background"),
                                session_id=session.session_id, tool_name="view")
            await asyncio.wait_for(finished.wait(), timeout=5)
            manager.replay_completed(session.session_id)
            await asyncio.sleep(0)
            assert len(service.history(key)) == 1
            receipt = service.history(key)[0]
            assert receipt.response.content == "Reported the result"
            assert job.id == receipt.event.payload["origin"]["job_id"]
            assert sum(event.type == "background_notification" for event in store.list_events(session.session_id)) == 1
            assert len(provider.requests) == 1
        finally:
            await service.close()
            manager.shutdown()

    asyncio.run(scenario())
