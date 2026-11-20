from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from raccoon.interfaces.qq.api import QQApi, QQApiError
from raccoon.interfaces.qq.gateway import QQGateway
from raccoon.interfaces.qq.state import QQState
from raccoon.runtime import UserInputOption, UserInputRequest
from tests.qq_fixtures import channel, packet


def test_unbound_sender_never_enters_agent_and_pairing_is_persistent(tmp_path):
    async def scenario():
        bot, state, runtime = channel(tmp_path)
        await bot.receive(packet())
        assert not runtime.events
        await bot.receive(packet("/pair wrong", message_id="bad"))
        await bot.receive(packet("/pair 中文", message_id="unicode-pair"))
        assert state.get("owner") is None
        await bot.receive(packet("/pair test-pairing", message_id="pair"))
        assert state.get("owner") == "owner"
        await bot.receive(packet("intruder", sender="other", message_id="other"))
        await bot.receive(packet(message_id="after-pairing"))
        await bot.receive(packet(message_id="after-pairing"))
        assert len(runtime.events) == 1
        event = runtime.events[0]
        assert event.session_id == "main" and event.payload["content"] == {"text": "hello"}
        state.close()
        reopened = QQState(tmp_path / "qq.db")
        assert reopened.get("owner") == "owner"
        reopened.close()
    asyncio.run(scenario())


def test_gateway_identify_resume_and_invalid_session(monkeypatch, tmp_path):
    import raccoon.interfaces.qq.gateway as module

    class Socket:
        def __init__(self, packets):
            self.packets, self.sent = packets, []
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def recv(self):
            return json.dumps({"op": 10, "d": {"heartbeat_interval": 30000}})
        async def send(self, value):
            self.sent.append(json.loads(value))
        def __aiter__(self):
            return self
        async def __anext__(self):
            if not self.packets:
                raise StopAsyncIteration
            return json.dumps(self.packets.pop(0))

    async def scenario():
        state = QQState(tmp_path / "qq.db")
        async def token():
            return "fake-token"
        async def url():
            return "wss://example.test"
        gateway = QQGateway(SimpleNamespace(token=token, gateway_url=url), state)
        first = Socket([{"op": 0, "t": "READY", "s": 1, "d": {"session_id": "gateway-session"}}, packet()])
        monkeypatch.setattr(module, "connect", lambda *a, **kw: first)
        await gateway.connect_once()
        assert first.sent[0]["op"] == 2 and first.sent[0]["d"]["intents"] == 1 << 25
        assert state.get("gateway_seq") == 2 and state.next_inbound() is not None
        second = Socket([{"op": 9, "d": False}])
        monkeypatch.setattr(module, "connect", lambda *a, **kw: second)
        await gateway.connect_once()
        assert second.sent[0]["op"] == 6 and second.sent[0]["d"]["seq"] == 2
        assert state.get("gateway_session") is None
        state.close()
    asyncio.run(scenario())


def test_expired_pairing_is_rejected(tmp_path):
    async def scenario():
        bot, state, runtime = channel(tmp_path)
        bot.pairing_expires = 0
        await bot.receive(packet("/pair test-pairing"))
        assert state.get("owner") is None
        state.close()
    asyncio.run(scenario())


def test_session_rotation_discards_admitted_private_messages(tmp_path):
    from raccoon.interfaces.qq.session import prepare_main_session

    async def scenario():
        old_id, old_service = prepare_main_session(tmp_path)
        await old_service.close()
        path = tmp_path / "channels" / "qq.sqlite3"
        state = QQState(path)
        try:
            state.admit(packet("old work"))
            assert state.next_inbound() is not None
        finally:
            state.close()

        new_id, new_service = prepare_main_session(tmp_path, new_session=True)
        await new_service.close()
        state = QQState(path)
        try:
            assert new_id != old_id
            assert state.next_inbound() is None
        finally:
            state.close()

    asyncio.run(scenario())


def test_permission_requires_owner_current_id_and_explicit_option(tmp_path):
    async def scenario():
        bot, state, runtime = channel(tmp_path)
        state.set("owner", "owner")
        pending = UserInputRequest("req-1", "permission_confirmation", "write?",
                                   [UserInputOption("allow_once", "仅本次"), UserInputOption("deny", "拒绝")])
        runtime.pending_inputs["main"] = pending
        for index, text in enumerate(["/permission wrong allow_once", "/permission req-1 always", "allow_once"]):
            await bot.receive(packet(text, message_id=f"bad-{index}"))
        await bot.receive(packet("/permission req-1 allow_once", sender="other", message_id="other"))
        assert runtime.answers == []
        bot._sync_permission(pending)
        await bot.receive(packet(f"/permission {bot.permission_code} allow_once", message_id="approved"))
        assert runtime.answers == [("main", "req-1", "allow_once")]
        await bot.receive(packet("/permission req-1 allow_once", message_id="approved"))
        assert len(runtime.answers) == 1
        state.close()
    asyncio.run(scenario())


def test_inbound_failure_is_replayable_and_gateway_does_not_advance_seq(tmp_path):
    async def scenario():
        bot, state, runtime = channel(tmp_path)
        state.set("owner", "owner")
        state.set("gateway_seq", 1)
        gateway = QQGateway(None, state)
        gateway.admit(packet())
        assert state.get("gateway_seq") == 2
        assert state.next_inbound() is not None
        original = runtime.submit_event
        async def fail(event):
            raise OSError("disk")
        runtime.submit_event = fail
        with pytest.raises(OSError):
            await bot.drain_one()
        assert state.next_inbound() is not None
        runtime.submit_event = original
        await bot.drain_one()
        assert state.next_inbound() is None
        assert len(runtime.events) == 1
        def failed_admission(*args):
            raise OSError("disk full")
        state.admit = failed_admission
        with pytest.raises(OSError):
            gateway.admit(packet(message_id="m2", seq=3))
        assert state.get("gateway_seq") == 2
        state.close()
    asyncio.run(scenario())


def test_response_outbox_splits_and_retains_msg_sequence_across_restart(tmp_path):
    bot, state, runtime = channel(tmp_path)
    state.set("owner", "owner")
    state.set("reply_msg_id", "m1")
    bot.on_response("main", SimpleNamespace(content="x" * 2100, finish_reason="stop"))
    bot.on_response("main", SimpleNamespace(content="last", finish_reason="stop"))
    rows = state.pending_outbound()
    assert len(rows) == 3
    assert [row["msg_seq"] for row in rows] == [1, 2, 3]
    assert "".join(row["content"] for row in rows) == "x" * 2100 + "last"
    state.close()
    reopened = QQState(tmp_path / "qq.db")
    assert len(reopened.pending_outbound()) == 3
    reopened.close()


def test_api_refresh_and_send_use_official_auth_without_leaking_secrets():
    async def scenario():
        seen = []
        def handler(request):
            seen.append(request)
            if request.url.host == "bots.qq.com":
                return httpx.Response(200, json={"access_token": "fake-token", "expires_in": "7200"})
            if request.url.path == "/gateway/bot":
                return httpx.Response(200, json={"url": "wss://api.sgroup.qq.com/websocket"})
            return httpx.Response(200, json={"id": "reply"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            api = QQApi("123", "fake-secret", client)
            assert (await api.gateway_url()).startswith("wss://")
            await api.send_text("owner", "m1", 2, "hi")
            assert len([r for r in seen if r.url.host == "bots.qq.com"]) == 1
            assert seen[-1].headers["Authorization"] == "QQBot fake-token"
            assert json.loads(seen[-1].content)["msg_seq"] == 2
        def error(request):
            return httpx.Response(401, json={"code": 42, "message": "fake-secret fake-token"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(error)) as client:
            with pytest.raises(QQApiError) as exc:
                await QQApi("123", "fake-secret", client).gateway_url()
            assert "fake-secret" not in str(exc.value) and "fake-token" not in str(exc.value)
    asyncio.run(scenario())



def test_qq_delivers_a_live_session_response_and_keeps_its_history(tmp_path):
    from raccoon.app.conversations import ConversationService
    from raccoon.app.session_runtime import SessionRuntime
    from raccoon.bootstrap.agent_session import new_session
    from raccoon.modules.sessions.store import SessionStore
    from tests.scripted_providers import FakeProvider, response

    async def scenario():
        service = ConversationService()
        bot, state, _ = channel(tmp_path, runtime=service)
        store = SessionStore(tmp_path / "data")
        session = new_session(store=store, session_id="main")
        key = service.register(
            SessionRuntime(session, FakeProvider([response("QQ reply")])),
            on_response=bot.on_response,
        )
        try:
            state.set("owner", "owner")
            await bot.receive(packet("请回答"))
            await asyncio.wait_for(service.wait(service.history(key)[0]), timeout=5)
            assert state.pending_outbound()[-1]["content"] == "QQ reply"
            roles = [
                message.role for message in store.load("main").messages
                if message.metadata.get("source_kind") != "agent_status"
            ]
            assert roles == ["user", "assistant"]
        finally:
            await service.close()
            state.close()

    asyncio.run(scenario())
