"""Archiving a project hides its conversations while keeping their saved state."""

from raccoon.modules.sessions.store import SessionStore
from tests.test_web_sidebar import record, sidebar_web


def test_project_archive_preserves_individual_conversation_archives(sidebar_web):
    client, _, other, project, created, _ = sidebar_web
    root = other / ".raccoon"
    record(root, "active", other)
    record(root, "archived", other, web_archived=True)
    originals = {path: path.read_bytes() for path in (root / "sessions").glob("*.jsonl")}

    route = f"/api/workspaces/{project}"
    assert client.patch(route, json={"archived": True}).status_code == 200
    archive = client.get("/api/archive").json()
    row = next(item for item in archive["projects"] if item["id"] == project)
    assert {item["id"] for item in row["conversations"]} == {"active", "archived"}
    assert client.patch(route, json={"archived": False}).status_code == 200
    archive = client.get("/api/archive").json()
    row = next(item for item in archive["projects"] if item["id"] == project)
    assert {item["id"] for item in row["conversations"]} == {"archived"}
    assert all(path.read_bytes() == original for path, original in originals.items())
    assert len(created) == 1



def test_incomplete_family_restore_keeps_the_archive_visible(sidebar_web, monkeypatch):
    client, _, other, project, _, _ = sidebar_web
    root = other / ".raccoon"
    record(root, "original", other)
    record(root, "edited", other, web_archived=True, web_message_revision={
        "workspace_id": project, "family": "original", "state": "accepted",
    })
    append = SessionStore.append_session_metadata_updated

    def fail_final_write(store, data, **metadata):
        if data.session_id == "original" and metadata.get("web_archived") is False:
            raise OSError("archive log locked")
        append(store, data, **metadata)

    body = {"storage_project_id": project, "conversation_id": "original"}
    with monkeypatch.context() as patch:
        patch.setattr(SessionStore, "append_session_metadata_updated", fail_final_write)
        failed = client.post("/api/archive/restore", json=body)
    assert failed.status_code == 400
    archives = client.get("/api/archive").json()["projects"]
    assert [item["id"] for row in archives for item in row["conversations"]] == ["original"]
    assert client.post("/api/archive/restore", json=body).status_code == 200
    assert client.get("/api/archive").json()["projects"] == []
