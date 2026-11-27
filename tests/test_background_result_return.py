"""A finished background job wakes its own conversation and is recorded once."""

import asyncio
import pytest

from raccoon.app.conversations import ConversationService
from raccoon.app.session_runtime import SessionRuntime
from raccoon.bootstrap.agent_session import new_session, open_session
from raccoon.modules.agent.background import BackgroundJobManager
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.types import make_text_result
from tests.scripted_providers import FakeProvider, response


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("include_session_tools", [False, True])
def test_background_controls_are_bound_before_a_run_and_preserve_session_scope(
    tmp_path, restore, include_session_tools,
):
    manager = BackgroundJobManager()
    store = SessionStore(tmp_path)
    try:
        session = new_session(
            store=store, session_id="owner", background_manager=manager,
            include_session_tools=include_session_tools,
        )
        if restore:
            session = open_session(
                store=store, session_id="owner", background_manager=manager,
                include_session_tools=include_session_tools,
            )
        status = session.tool_registry.get("background_status")
        cancel = session.tool_registry.get("background_cancel")
        assert status is not None and cancel is not None
        session.tool_registry.replace_supplied_tools([])
        assert session.tool_registry.get("background_status") is status
        assert session.tool_registry.get("background_cancel") is cancel

        own = manager.start(lambda: make_text_result("view", "own"), session_id="owner", tool_name="view")
        other = manager.start(lambda: make_text_result("view", "other"), session_id="other", tool_name="view")
        assert status.executor(job_id=own.id).ok
        assert cancel.executor(job_id=own.id).ok
        assert not status.executor(job_id=other.id).ok
        assert not cancel.executor(job_id=other.id).ok
        assert {job["job_id"] for job in status.executor().data["jobs"]} == {own.id}
    finally:
        manager.shutdown()


def test_background_controls_are_not_added_without_a_manager_or_to_subagents(tmp_path):
    from raccoon.config.subagents_config import SubagentConfig

    store = SessionStore(tmp_path)
    manager = BackgroundJobManager()
    try:
        ordinary = new_session(store=store)
        child = new_session(
            store=store, background_manager=manager,
            subagent_config=SubagentConfig(name="reviewer"),
        )
        for session in (ordinary, child):
            assert session.tool_registry.get("background_status") is None
            assert session.tool_registry.get("background_cancel") is None
    finally:
        manager.shutdown()


def test_background_completion_wakes_the_owner_only_once(tmp_path):
    async def scenario():
        manager = BackgroundJobManager()
        store = SessionStore(tmp_path)
        session = new_session(store=store, background_manager=manager)
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
