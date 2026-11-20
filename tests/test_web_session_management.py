"""Deleting a session removes only files belonging to that conversation."""

from tests.web_fixtures import web


def test_delete_removes_owned_files_but_preserves_project_outputs(web, tmp_path):
    client, runtime, _ = web
    current = client.get("/api/state").json()
    project, target = current["project"]["id"], current["session"]["id"]
    runtime.session.store.append_user_message(runtime.session.data, "Existing conversation", user_timezone="UTC")
    selected = client.post("/api/sidebar/new", json={"project_id": project}).json()["session"]["id"]
    assert selected != target
    root = runtime.catalog.root
    files = [root / "attachments" / target / "image.png",
             root / "official-mcp-apps" / project / target / "state.bin"]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("session data", encoding="utf-8")
    output = tmp_path / "report.html"
    output.write_text("project output", encoding="utf-8")

    result = client.request("DELETE", f"/api/sessions/{target}", json={
        "project_id": project, "session_id": target, "submission_id": "delete",
    })
    assert result.status_code == 200, result.text
    assert all(not path.exists() for path in files)
    assert not (root / "sessions" / f"{target}.jsonl").exists()
    assert output.read_text(encoding="utf-8") == "project output"
    assert client.get("/api/state").json()["session"]["id"] == selected


def test_delete_refuses_a_session_leased_by_another_runtime(web):
    from raccoon.runtime.session_locks import session_lock

    client, runtime, _ = web
    state = client.get("/api/state").json()
    target = state["session"]["id"]
    with session_lock(runtime.catalog.root, target):
        result = client.request("DELETE", f"/api/sessions/{target}", json={
            "project_id": state["project"]["id"], "session_id": target, "submission_id": "leased",
        })
    assert result.status_code == 409
    assert (runtime.catalog.sessions_dir / f"{target}.jsonl").exists()



def test_delete_rejects_redirected_attachment_directory_before_touching_log(tmp_path, monkeypatch):
    import stat
    from pathlib import Path
    from types import SimpleNamespace

    import pytest

    from raccoon.modules.sessions.deletion import delete_session_data
    from raccoon.modules.sessions.store import SessionStore

    store = SessionStore(tmp_path)
    data = store.create()
    directory = store.root / "attachments" / data.session_id
    directory.mkdir(parents=True)
    log = store.sessions_dir / f"{data.session_id}.jsonl"
    original = log.read_bytes()
    lstat = Path.lstat

    def flagged(path, *args, **kwargs):
        info = lstat(path, *args, **kwargs)
        if path == directory:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return info

    monkeypatch.setattr(Path, "lstat", flagged)
    with pytest.raises(ValueError, match="目录联接"):
        delete_session_data(store.root, data.session_id)

    assert log.read_bytes() == original
    assert directory.exists()
