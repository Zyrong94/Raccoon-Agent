"""Web auto routing classifies a busy conversation and queues the whole input."""

import asyncio
import threading

from raccoon.modules.events import EventSourceAdapter
from tests.web_fixtures import response, wait_idle, web


def test_busy_web_message_uses_auto_routing_for_its_target_session(web, monkeypatch):
    client, runtime, provider = web
    entered = threading.Event()
    release = threading.Event()
    classified = threading.Event()
    requests = []
    decisions = []

    async def complete(request):
        requests.append(request)
        if len(requests) == 1:
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
        return response("Done")

    async def classify(event, snapshot, *, runner):
        decisions.append((event.session_id, event.payload["content"]["text"], runner))
        classified.set()
        return "queue"

    monkeypatch.setattr(provider, "acomplete", complete)
    monkeypatch.setattr("raccoon.interfaces.web.conversation_actions.classify_web_auto_event", classify)
    state = client.get("/api/state").json()
    scope = {"project_id": state["project"]["id"], "session_id": state["session"]["id"]}
    try:
        assert client.post("/api/messages", json={
            **scope, "submission_id": "first", "message": "Start", "mode": "auto",
        }).status_code == 202
        assert entered.wait(5)
        assert client.post("/api/messages", json={
            **scope, "submission_id": "second", "message": "Next", "mode": "auto",
        }).status_code == 202
        assert classified.wait(5)
        assert decisions == [(scope["session_id"], "Next", runtime)]
        assert len(requests) == 1
    finally:
        release.set()
    assert [message["content"] for message in wait_idle(client)["messages"] if message["role"] == "user"] == [
        "Start", "Next",
    ]


def test_parallel_classification_with_an_attachment_uses_the_regular_queue(monkeypatch):
    from types import SimpleNamespace

    from raccoon.app.conversations import SessionSnapshot
    from raccoon.application.conversations.auto_routing import classify_web_auto_event

    async def classify(event, snapshot):
        return "parallel"

    monkeypatch.setattr("raccoon.modules.events.semantic_routing.build_event_router",
                        lambda runner: SimpleNamespace(classify=classify))
    event = EventSourceAdapter(main_session_id="chat").user_message(
        source="web", message_id="file", conversation_id="chat", text="Read this",
    )
    event.payload["mode"] = "auto"
    event.payload["content"]["attachments"] = [{"kind": "file"}]
    assert asyncio.run(classify_web_auto_event(event, SessionSnapshot("chat", ()), runner=None)) == "queue"
