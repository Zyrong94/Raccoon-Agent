"""Subagent operations share ConversationService and preserve the stored boundary."""
import asyncio

import pytest

from raccoon.runtime.model_types import MessagePart
from raccoon.app.conversations import ConversationService
from raccoon.app.session_runtime import SessionRuntime
from raccoon.bootstrap.agent_session import prepare_session
from raccoon.config.subagents_config import SubagentConfig
from raccoon.modules.collaboration.sessions import SubagentRequest, create_subagent_session
from raccoon.modules.collaboration.coordination import owned_subagent, spawn_subagent, send_subagent
from raccoon.modules.permissions.manager import PermissionManager
from raccoon.modules.permissions.policy import DefaultPermissionPolicy
from raccoon.modules.sessions.store import SessionStore
from raccoon.runtime.model_provider import ChatProvider
from raccoon.runtime.model_types import ChatResponse


class OfflineProvider(ChatProvider):
    name = 'offline'
    model = 'scripted'
    async def acomplete(self, request):
        return ChatResponse(
            provider=self.name,
            model=self.model,
            finish_reason='stop',
            parts=[MessagePart(kind="text", content='finished')],
        )


def parent_runtime(tmp_path, identity='parent'):
    store = SessionStore(tmp_path)
    session = prepare_session(store=store, data=store.create(session_id=identity), tools=[],
        permission_manager=PermissionManager(policy=DefaultPermissionPolicy(tmp_path)), user_timezone='UTC')
    session.subagent_configs = {'reviewer': SubagentConfig(name='reviewer', tools=())}
    return SessionRuntime(session=session, provider=OfflineProvider(), tools=[])


def test_creation_uses_parent_identity_and_freezes_the_tool_boundary(tmp_path):
    parent = parent_runtime(tmp_path)
    subagent = create_subagent_session(parent.session, SubagentRequest('reviewer', 'Review this'))
    assert subagent.data.metadata['parent_session_id'] == 'parent'
    assert subagent.subagent_config.tools == ()
    assert subagent.allowed_tool_names == set()
    assert not subagent.allow_background_execution
    assert subagent.permission_manager is not parent.session.permission_manager


def test_controls_reject_another_parent_and_another_permission_scope(tmp_path):
    parent = parent_runtime(tmp_path)
    subagent = create_subagent_session(parent.session, SubagentRequest('reviewer', 'Review this'))
    other = parent_runtime(tmp_path, 'other')
    with pytest.raises(ValueError, match='不属于'):
        owned_subagent(other, subagent.session_id)
    parent.session.subagent_scope = 'timer-scope'
    with pytest.raises(ValueError, match='作用域'):
        owned_subagent(parent, subagent.session_id)


def test_spawn_follow_up_and_close_use_the_shared_runtime_registry(tmp_path):
    async def scenario():
        service = ConversationService()
        parent = parent_runtime(tmp_path)
        parent_key = service.register(parent)
        service.start()
        try:
            result = await spawn_subagent(parent, SubagentRequest('reviewer', 'Review this'))
            assert result.ok
            keys = service.subagent_keys(parent_key)
            assert len(keys) == 1
            runtime = service.get_loaded_session(keys[0])
            assert runtime.session.session_id == result.subagent_session_id
            follow_up = await send_subagent(parent, result.subagent_session_id, 'Review again', event_id='follow-up')
            assert follow_up['ok'] and follow_up['delivery'] == 'processed'
            assert service.get_loaded_session(keys[0]) is runtime
            await service.close_session(parent_key)
            assert service.sessions == {}
            assert parent._closed and runtime._closed
        finally:
            await service.close()
    asyncio.run(scenario())



def test_cancel_parent_clears_running_and_queued_subagent_messages(tmp_path):
    from tests.scripted_providers import FakeProvider, response

    async def scenario():
        service = ConversationService()
        parent = parent_runtime(tmp_path)
        parent.session.subagent_configs["reviewer"] = SubagentConfig(
            name="reviewer", tools=(), allow_background=True,
        )
        key = service.register(parent)
        service.start()
        slow = FakeProvider([response("Should not return")], block_first=True)
        try:
            result = await spawn_subagent(parent, SubagentRequest("reviewer", "Review"))
            assert result.ok
            parent.set_model(slow, reasoning_effort=parent.reasoning_effort, context_window=parent.context_window)
            child_id = result.subagent_session_id
            await send_subagent(parent, child_id, "First", mode="follow_up", event_id="first")
            assert await asyncio.to_thread(slow.entered.wait, 5)
            await send_subagent(parent, child_id, "Queued", mode="follow_up", event_id="queued")
            child_key = service.subagent_keys(key)[0]
            service.stop_session(key)
            slow.release.set()
            await asyncio.wait_for(service.stop_and_wait(key), timeout=5)
            statuses = {receipt.event.id: receipt.status for receipt in service.history(child_key)}
            assert statuses["first"] == statuses["queued"] == "cancelled"
            assert len(slow.requests) == 1
        finally:
            slow.release.set()
            await service.close()

    asyncio.run(scenario())
