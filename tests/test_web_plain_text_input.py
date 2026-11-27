"""Web slash text uses the real message path; model and MCP are offline fakes."""

import asyncio
import json
import time
from raccoon.runtime.model_types import MessagePart
from threading import Event

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from raccoon.bootstrap.runtime import create_runtime
from raccoon.bootstrap.web_app import create_web_app
from raccoon.config.settings import AppConfig
from raccoon.config.models import build_model_catalog
from raccoon.runtime.model_provider import ChatProvider
from raccoon.runtime.model_types import ChatResponse, ProviderCapabilities
from tests.web_fixtures import wait_idle


class RecordingProvider(ChatProvider):
    name = "fake"
    model = "test"
    capabilities = ProviderCapabilities()

    def __init__(self):
        self.requests = []

    async def acomplete(self, request):
        self.requests.append(request)
        return ChatResponse(
            provider=self.name,
            model=self.model,
            parts=[MessagePart(kind="text", content="received")],
        )


class OfflineMcp:
    def connect_all_in_background(self):
        pass

    def tools(self):
        return ()

    def statuses(self):
        return ()

    def close(self):
        pass


@pytest.fixture
def plain_web(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    provider = RecordingProvider()
    config = AppConfig(env={}, project_config={"memory": {"enabled": False}})

    def factory(**kwargs):
        return create_runtime(**kwargs, provider=provider, tools=[], app_config=config,
                              mcp_manager_factory=lambda configs: OfflineMcp())

    runtime = factory(project_root=tmp_path / "project")
    with TestClient(create_web_app(runtime, runtime_factory=factory),
                    base_url="http://127.0.0.1:8765") as client:
        state = client.get("/api/state").json()
        client.headers["X-Raccoon-Token"] = state["token"]
        yield client, runtime, provider


def wait_for_reply(client):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = client.get("/api/state").json()
        if not state["busy"] and any(message["role"] == "assistant" for message in state["messages"]):
            return state
        time.sleep(0.02)
    pytest.fail("ordinary message did not complete")


def test_busy_web_message_queues_different_model_and_permission_for_its_own_turn(plain_web, monkeypatch):
    client, runtime, first = plain_web
    second = RecordingProvider()
    second.model = "other"
    runtime.model_catalog = build_model_catalog({
        "default_model": "fake/test",
        "providers": {"fake": {"api_key_env": "UNUSED"}},
        "models": {"fake/test": {}, "fake/other": {}},
    })
    monkeypatch.setattr("raccoon.app.session_runtime.create_provider_for_model", lambda config, profile: second)
    entered, release, second_entered = Event(), Event(), Event()
    observed = []

    def observe(provider):
        observed.append((provider.model, runtime.session.permission_manager.mode.value,
                         runtime.session.sandbox_access.unrestricted))

    async def first_reply(request):
        observe(first)
        entered.set()
        assert release.wait(10)
        observe(first)
        return ChatResponse(
            provider=first.name,
            model=first.model,
            parts=[MessagePart(kind="text", content="first")],
        )

    async def second_reply(request):
        observe(second)
        second_entered.set()
        return ChatResponse(
            provider=second.name,
            model=second.model,
            parts=[MessagePart(kind="text", content="second")],
        )

    monkeypatch.setattr(first, "acomplete", first_reply)
    monkeypatch.setattr(second, "acomplete", second_reply)
    state = client.get("/api/state").json()
    scope = {"project_id": state["project"]["id"], "session_id": state["session"]["id"]}
    try:
        assert client.post("/api/messages", json={
            **scope, "submission_id": "first", "message": "first question",
            "model_ref": "fake/test", "permission_mode": "ask",
        }).status_code == 202
        assert entered.wait(5)
        assert client.post("/api/messages", json={
            **scope, "submission_id": "second", "message": "second question", "mode": "steer",
            "model_ref": "fake/other", "permission_mode": "bypass",
        }).status_code == 202
        assert client.get("/api/state").json()["pending_messages"][0]["text"] == "second question"
        assert not second_entered.is_set()
    finally:
        release.set()
    assert second_entered.wait(5)
    final = wait_idle(client)
    assert [row["content"] for row in final["messages"] if row["role"] == "assistant"] == ["first", "second"]
    assert observed == [("test", "ask", False), ("test", "ask", False), ("other", "bypass", True)]
    assert runtime.model_ref == "fake/test"
    assert not runtime.session.sandbox_access.unrestricted
    saved = [message.metadata["execution_settings"] for message in runtime.session.data.messages
             if "execution_settings" in message.metadata]
    assert saved == [
        {"model_ref": "fake/test", "permission_mode": "ask"},
        {"model_ref": "fake/other", "permission_mode": "bypass"},
    ]


def test_cold_history_and_submission_are_independent_of_runtime_preparation(plain_web, monkeypatch):
    client, _, _ = plain_web
    app = client.app.state.workbench
    initial = client.get("/api/state").json()
    cold = client.post("/api/sidebar/new", json={}).json()
    scope = {"project_id": cold["project"]["id"], "session_id": cold["session"]["id"]}
    entered, release = Event(), Event()
    prepare = app.prepare_session

    async def delayed_prepare(project_id, session_id, **options):
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        return await prepare(project_id, session_id, **options)

    monkeypatch.setattr(app, "prepare_session", delayed_prepare)
    try:
        assert cold["execution_ready"] is False and cold["model"] == initial["model"]
        assert not entered.is_set()
        invalid = client.post("/api/messages", json={**scope, "submission_id": "invalid",
                              "message": "hi", "model_ref": "missing/model"})
        assert invalid.status_code == 400
        assert not entered.is_set()
        accepted = client.post("/api/messages", json={**scope, "submission_id": "first", "message": "hi"})
        assert accepted.status_code == 202
        assert entered.wait(5)
        queued = client.get("/api/state").json()
        assert queued["execution_ready"] is False and queued["busy"] is True
        assert queued["pending_messages"][0]["text"] == "hi"
    finally:
        release.set()
    assert wait_idle(client, scope=scope)["messages"][-1]["content"] == "received"


@pytest.mark.parametrize("with_attachment", [False, True])
def test_sse_delivers_committed_message_before_reply_and_only_state_at_finish(plain_web, monkeypatch, with_attachment):
    client, runtime, provider = plain_web
    state = client.get("/api/state").json()
    scope = {"project_id": state["project"]["id"], "session_id": state["session"]["id"]}
    attachment_ids = []
    if with_attachment:
        upload = client.post("/api/attachments", params={**scope, "name": "note.txt"},
                             content=b"notes", headers={"Content-Type": "text/plain"})
        assert upload.status_code == 201
        attachment_ids.append(upload.json()["id"])
    started, release = Event(), Event()
    complete = provider.acomplete

    async def delayed_reply(request):
        started.set()
        assert release.wait(10), "test did not release the fake model"
        return await complete(request)

    event_endpoint = next(route.endpoint for route in client.app.routes if route.path == "/api/events")

    async def read_replay(cursor):
        request = Request({"type": "http", "query_string": f"cursor={cursor}".encode(), "headers": []})
        response = await event_endpoint(request)
        events = []
        try:
            async with asyncio.timeout(3):
                async for frame in response.body_iterator:
                    payload = next(line[6:] for line in frame.splitlines() if line.startswith("data: "))
                    event = json.loads(payload)
                    if event["kind"] == "ready":
                        return events
                    events.append(event)
        finally:
            await response.body_iterator.aclose()

    monkeypatch.setattr(provider, "acomplete", delayed_reply)
    try:
        response = client.post("/api/messages", json={
            **scope, "submission_id": "delayed", "message": "show before the reply", "attachment_ids": attachment_ids,
        })
        assert response.status_code == 202
        assert started.wait(5)
        events = client.portal.call(read_replay, state["event_seq"])
        user = next(event["messages"][0] for event in events if event["kind"] == "message_committed")
        assert user["role"] == "user" and user["content"] == "show before the reply"
        if with_attachment:
            assert user["attachments"][0]["name"] == "note.txt"
        assert user["id"] in {message.id for message in runtime.session.store.load(scope["session_id"]).messages}
        assert client.get("/api/state").json()["messages"] == [user]
        assert not any(event["kind"] == "snapshot" for event in events)
    finally:
        release.set()

    final = wait_for_reply(client)
    events = client.portal.call(read_replay, state["event_seq"])
    assert final["messages"] == [
        row for event in events if event["kind"] == "message_committed" for row in event["messages"]
    ]
    assert [row["role"] for row in final["messages"]] == ["user", "assistant"]
    settled = next(event for event in events if event["kind"] == "run_state" and event["settled"])
    assert settled["state"]["busy"] is False
    assert settled["state"]["status"] == "completed"
    assert "messages" not in settled["state"]
    assert not any(event["kind"] == "snapshot" for event in events)
    assert client.portal.call(read_replay, events[-1]["seq"]) == []


@pytest.mark.parametrize("text", ["/help", "/model fake/other", "/research explain this", "/tmp/example"])
def test_slash_text_reaches_model_without_command_dispatch(plain_web, text):
    client, runtime, provider = plain_web
    original_session = runtime.session.session_id
    state = client.get("/api/state").json()
    assert client.post("/api/messages", json={
        "project_id": state["project"]["id"], "session_id": original_session,
        "submission_id": "slash", "message": text,
    }).status_code == 202
    state = wait_for_reply(client)
    assert any(message["role"] == "user" and message["content"] == text for message in state["messages"])
    assert provider.requests
    assert runtime.session.session_id == original_session
    assert runtime.provider is provider
    assert "commands" not in state and "commands_revision" not in state


def test_web_command_routes_are_removed_but_session_actions_remain(plain_web):
    client, runtime, _ = plain_web
    state = client.get("/api/state").json()
    assert client.get("/api/commands").status_code == 404
    assert client.post("/api/commands", json={"message": "/new"}).status_code == 404
    envelope = {"project_id": state["project"]["id"], "session_id": state["session"]["id"],
                "path": "/commands", "method": "POST", "body": {"message": "/new"}}
    assert client.post("/api/session-command", json=envelope).status_code == 404
    scope = {"project_id": state["project"]["id"], "session_id": state["session"]["id"]}
    assert client.patch(f"/api/sessions/{scope['session_id']}", json={**scope, "submission_id": "rename", "title": "renamed"}).status_code == 200
    assert client.get("/api/state").json()["session"]["title"] == "renamed"
    assert client.post("/api/messages", json={**scope, "submission_id": "chat", "message": "hello"}).status_code == 202
    wait_for_reply(client)
    created = client.post("/api/sidebar/new", json={"project_id": scope["project_id"]})
    assert created.status_code == 200
    assert created.json()["session"]["id"] != state["session"]["id"]
    assert client.post(f"/api/sessions/{state['session']['id']}/activate").status_code == 200


def test_slash_text_accepts_normal_attachments(plain_web):
    client, _, provider = plain_web
    state = client.get("/api/state").json()
    scope = {"project_id": state["project"]["id"], "session_id": state["session"]["id"]}
    upload = client.post("/api/attachments", params={**scope, "name": "note.txt"},
                         content=b"attachment evidence", headers={"Content-Type": "text/plain"})
    assert upload.status_code == 201
    sent = client.post("/api/messages", json={**scope, "submission_id": "attachment", "message": "/summarize this",
                       "attachment_ids": [upload.json()["id"]]})
    assert sent.status_code == 202
    final = wait_for_reply(client)
    message = next(message for message in final["messages"] if message["role"] == "user")
    assert message["content"] == "/summarize this"
    assert message["attachments"][0]["name"] == "note.txt"
    assert provider.requests


def test_message_edit_still_forks_session_without_command_handler(plain_web):
    client, runtime, _ = plain_web
    initial = client.get("/api/state").json()
    assert client.post("/api/messages", json={
        "project_id": initial["project"]["id"], "session_id": initial["session"]["id"],
        "submission_id": "original", "message": "original",
    }).status_code == 202
    state = wait_for_reply(client)
    original_session = state["session"]["id"]
    user_message = next(message for message in state["messages"] if message["role"] == "user")
    body = {"project_id": state["project"]["id"], "session_id": original_session,
            "message_id": user_message["id"], "request_id": "edit-slash-text", "submission_id": "edit",
            "history_revision": state["history_revision"], "message": "/revised"}
    result = client.post("/api/messages/edit", json=body)
    assert result.status_code == 202
    revision_id = result.json()["session"]["id"]
    assert revision_id != original_session
    final = wait_idle(client, scope={"project_id": body["project_id"], "session_id": revision_id})
    assert any(message["role"] == "user" and message["content"] == "/revised" for message in final["messages"])
    original = runtime.session.store.load(original_session)
    assert any(part.content == "original" for message in original.messages for part in message.parts)
