"""Cold personal drafts are isolated from project records and load on demand."""

from pathlib import Path

from tests.test_web_sidebar import sidebar_web
from tests.web_fixtures import wait_idle


def test_personal_draft_stays_cold_until_chat_and_keeps_its_own_history(sidebar_web):
    client, original, other, other_id, created, _ = sidebar_web
    (original.project_path / "AGENTS.md").write_text("PROJECT_ONLY", encoding="utf-8")
    draft = client.post("/api/sidebar/new", json={}).json()
    project = draft["project"]
    identity = draft["session"]["id"]
    assert project["projectless"] is True
    assert not draft["execution_ready"] and len(created) == 1
    assert Path(project["path"]) == original.catalog.root / "web-personal" / "workspace"

    explicit = client.post("/api/sidebar/new", json={"project_id": other_id, "draft_only": True}).json()
    assert Path(explicit["project"]["path"]) == other
    assert client.post("/api/sidebar/new", json={"draft_only": True}).json()["session"]["id"] == identity
    sent = client.post("/api/messages", json={
        "project_id": project["id"], "session_id": identity, "submission_id": "first",
        "message": "Chat without a project",
    })
    assert sent.status_code == 202
    assert wait_idle(client)["messages"][0]["content"] == "Chat without a project"
    assert not original.session.store.load(original.session_id).messages
