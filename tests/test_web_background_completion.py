"""Background results stay scoped to their original Web conversation."""

import time

import pytest

from raccoon.modules.tools.types import make_text_result
from tests.web_fixtures import web


def test_background_result_updates_its_original_chat_after_switching(web):
    client, runtime, _ = web
    initial = client.get("/api/state").json()
    project = initial["project"]["id"]
    source = initial["session"]["id"]
    runtime.session.store.append_user_message(runtime.session.data, "Existing conversation", user_timezone="UTC")
    other = client.post("/api/sidebar/new", json={"project_id": project}).json()["session"]["id"]
    assert other != source
    runtime.background_manager.start(
        lambda: make_text_result("view", "Evidence"), session_id=source, tool_name="view",
    )

    target = "/api/session-view"
    params = {"project_id": project, "session_id": source}
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        history = client.get(target, params=params).json()
        if not history["busy"] and any(message["role"] == "assistant" for message in history["messages"]):
            break
        time.sleep(0.01)
    else:
        pytest.fail("background result was not delivered")

    selected = client.get("/api/state").json()
    assert selected["session"]["id"] == other
    assert selected["messages"] == []
    assert history["messages"][-1]["content"] == "你好，工作台"
