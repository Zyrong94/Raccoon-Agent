"""A scheduled instruction shares the target conversation queue and history."""

import asyncio

import pytest

from raccoon.app.conversations import ConversationService
from raccoon.app.session_runtime import SessionRuntime
from raccoon.application.conversations.scheduled_main import ScheduledTurn
from raccoon.bootstrap.agent_session import new_session
from raccoon.modules.events import EventSourceAdapter
from raccoon.modules.scheduling.store import ScheduleStore
from raccoon.modules.sessions.store import SessionStore
from tests.scripted_providers import FakeProvider, response


def test_timer_waits_for_current_chat_and_uses_the_same_session(tmp_path):
    async def scenario():
        service = ConversationService()
        root = tmp_path / ".raccoon"
        schedule = ScheduleStore(root / "scheduling.sqlite3", now=lambda: 1800000000)
        session = new_session(store=SessionStore(root), project_root=tmp_path)
        provider = FakeProvider([response("Chat done"), response("Timer done")], block_first=True)
        runtime = SessionRuntime(session, provider)
        key = service.register(runtime)
        source = EventSourceAdapter(main_session_id=session.session_id)
        try:
            chat = service.submit_event(source.user_message(
                source="local", message_id="chat", conversation_id=session.session_id, text="Ordinary chat",
            ), key)
            assert await asyncio.to_thread(provider.entered.wait, 5)
            task = schedule.create(
                title="Check project", prompt="Run the scheduled check", kind="interval", spec={"seconds": 3600},
                timezone="UTC", project=str(tmp_path), data_root=str(root),
                origin_session=session.session_id, channel="qq", target="owner",
            )
            run = schedule.trigger(task["id"])
            scheduled = ScheduledTurn(root, session.session_id, service, runtime, store=schedule)
            dispatched = await scheduled.dispatch("scheduled", {"run_id": run["id"]})
            assert dispatched["status"] == "queued"
            assert len(provider.requests) == 1
            provider.release.set()

            await asyncio.wait_for(service.wait(chat), timeout=5)
            timer = next(receipt for receipt in service.history(key) if receipt.event.id == dispatched["event_id"])
            result = await asyncio.wait_for(service.wait(timer), timeout=5)
            assert result.content == "Timer done"
            assert schedule.get_run(run["id"])["status"] == "succeeded"
            history = [
                message for message in session.store.load(session.session_id).messages
                if message.metadata.get("source_kind") != "agent_status"
            ]
            assert [message.role for message in history] == ["user", "assistant", "user", "assistant"]
            assert history[2].parts[0].content == "Run the scheduled check"
            assert history[2].metadata["scheduled_task_id"] == task["id"]
            assert "Chat done" in [message.content for message in provider.requests[1].messages]
        finally:
            provider.release.set()
            await service.close()
            schedule.close()

    asyncio.run(scenario())



def test_scheduled_permissions_restore_the_conversation_after_failure(tmp_path):
    from raccoon.modules.permissions.types import PermissionAction, PermissionGrant, PermissionScopeType

    root = tmp_path / ".raccoon"
    schedule = ScheduleStore(root / "scheduling.sqlite3")
    session = new_session(store=SessionStore(root), project_root=tmp_path)
    runtime = SessionRuntime(session, FakeProvider([]))
    original = session.permission_manager
    sandbox_mode = session.sandbox_access.mode
    turn = ScheduledTurn(root, session.session_id, None, runtime, store=schedule)
    task = {"id": "timer", "data_root": str(root), "permission_mode": "bypass"}
    try:
        with pytest.raises(RuntimeError, match="failed"):
            with turn._permissions(task, runtime, run_id="occurrence"):
                assert session.mode == "bypass"
                assert session.scheduled_run_id == session.subagent_scope == "occurrence"
                session.permission_manager.grants.add(PermissionGrant(
                    "timer-only", "allow", PermissionAction.WRITE_PATH, PermissionScopeType.EXACT_PATH,
                    str(tmp_path / "timer.txt"), "test",
                ))
                raise RuntimeError("failed")
        assert session.permission_manager is original and original.grants.list() == []
        assert session.mode == "ask" and session.sandbox_access.mode == sandbox_mode
        assert session.scheduled_run_id is None and session.subagent_scope is None
    finally:
        runtime.close()
        schedule.close()
