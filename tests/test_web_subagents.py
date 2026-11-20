"""Project subagent history is scoped to its parent and needs no runtime."""

from tests.web_fixtures import response, web


def test_web_subagent_history_is_read_only_and_cannot_be_opened_as_project_chat(web):
    client, runtime, _ = web
    workbench = client.app.state.workbench
    state = client.get("/api/state").json()
    parent = state["session"]["id"]
    scope = {"project_id": state["project"]["id"], "session_id": parent}
    store = runtime.session.store
    subagent = store.create(parent_session_id=parent, delegate_agent_name="reviewer", delegate_task="Review code")
    store.append_assistant_response(subagent, response("Review complete"))
    unrelated = store.create()
    before = set(workbench.conversations.sessions)
    path = store.sessions_dir / f"{subagent.session_id}.jsonl"
    saved = path.read_bytes()

    result = client.get("/api/subagents", params=scope)
    assert result.status_code == 200
    assert [item["subagent_session_id"] for item in result.json()["subagents"]] == [subagent.session_id]
    route = f"/api/subagents/{subagent.session_id}"
    result = client.get(route, params=scope)
    assert result.json()["messages"][-1]["content"] == "Review complete"
    assert "DO_NOT_EXPOSE" not in result.text
    assert client.get(route, params={**scope, "session_id": unrelated.session_id}).status_code == 400
    assert client.post(f"/api/sessions/{subagent.session_id}/activate").status_code == 409
    assert client.get("/api/state").json()["session"]["id"] == parent
    assert set(workbench.conversations.sessions) == before
    assert path.read_bytes() == saved


def test_main_subagent_read_does_not_start_a_host(web):
    client, _, _ = web
    bridge = client.app.state.main_bridge
    assert bridge.owner is None
    assert client.get("/api/main-session/subagents", params={
        "session_id": "main", "generation": "old",
    }).status_code == 503
    assert bridge.owner is None
