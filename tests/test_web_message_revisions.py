"""Regeneration creates a new answer while keeping the original conversation."""

from tests.web_fixtures import response, wait_idle, web


def test_regenerate_uses_only_the_selected_turn_and_preserves_the_original(web):
    client, runtime, provider = web
    provider.responses = iter([response("First answer"), response("Original answer"), response("Later answer")])
    initial = client.get("/api/state").json()
    project = initial["project"]["id"]
    original = initial["session"]["id"]
    original_scope = {"project_id": project, "session_id": original}
    for index, text in enumerate(("Keep the prefix", "Regenerate this", "Omit this later turn")):
        result = client.post("/api/messages", json={
            **original_scope, "message": text, "submission_id": f"turn-{index}",
        })
        assert result.status_code == 202
        finished = wait_idle(client)
    user_messages = [message for message in finished["messages"] if message["role"] == "user"]
    log = runtime.session.store.sessions_dir / f"{original}.jsonl"
    saved = log.read_bytes()
    body = {
        **original_scope, "submission_id": "regenerate", "request_id": "revision-1",
        "history_revision": finished["history_revision"], "message_id": user_messages[1]["id"],
    }
    result = client.post("/api/messages/regenerate", json=body)
    assert result.status_code == 202, result.text
    revision_id = result.json()["session"]["id"]
    assert revision_id != original
    regenerated = wait_idle(client, scope={"project_id": project, "session_id": revision_id})
    assert [message["content"] for message in regenerated["messages"]] == [
        "Keep the prefix", "First answer", "Regenerate this", "你好，工作台",
    ]
    assert log.read_bytes() == saved
    assert {item["session_id"] for item in regenerated["versions"]["items"]} == {original, revision_id}
    retry = client.post("/api/messages/regenerate", json=body)
    assert retry.status_code == 202
    assert retry.json()["session"]["id"] == revision_id

    restored = client.post("/api/messages/version", json={
        "project_id": project, "session_id": revision_id, "target_session_id": original,
        "submission_id": "show-original", "history_revision": regenerated["history_revision"],
    })
    assert restored.status_code == 200, restored.text
    assert restored.json()["messages"][-1]["content"] == "Later answer"
    assert log.read_bytes() == saved
