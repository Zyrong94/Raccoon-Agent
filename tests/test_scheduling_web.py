"""The Web scheduler binds new jobs to a project session and checks revisions."""

from tests.web_fixtures import web


def test_web_schedule_creation_binds_the_current_project_and_session(web):
    client, runtime, _ = web
    initial = client.get("/api/state").json()
    body = {
        "project_id": initial["project"]["id"], "session_id": initial["session"]["id"],
        "submission_id": "schedule-1", "title": "Project check", "prompt": "Check the project",
        "kind": "interval", "spec": {"seconds": 3600}, "timezone": "UTC",
    }
    response = client.post("/api/scheduled-tasks", json=body)
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["origin_session"] == runtime.session.session_id
    assert task["permission_mode"] is None
    assert task["execution"] == "project"
    assert client.post("/api/scheduled-tasks", json={
        **body, "submission_id": "invalid", "target": "arbitrary recipient",
    }).status_code == 422
    route = f"/api/scheduled-tasks/{task['id']}"
    assert client.patch(route, json={"revision": 99, "title": "Stale"}).status_code == 409
    assert client.post(f"{route}/pause", json={"revision": task["revision"]}).json()["status"] == "paused"
