"""Changing the permission mode targets the selected conversation."""

from tests.web_fixtures import web


def test_permission_picker_changes_only_the_permission_mode(web):
    client, _, _ = web
    before = client.get("/api/state").json()
    scope = {"project_id": before["project"]["id"], "session_id": before["session"]["id"]}
    for mode in ("auto", "bypass", "ask"):
        result = client.post("/api/mode", json={**scope, "mode": mode, "submission_id": f"mode-{mode}"})
        assert result.status_code == 200
        assert result.json()["permission_mode"] == mode
        state = client.get("/api/state").json()
        assert state["permission_mode"] == mode
        assert state["model"] == before["model"]
        assert state["messages"] == before["messages"]
