import asyncio
import json
import threading
from pathlib import Path

import pytest
from tests.web_fixtures import response, wait_idle, web


def test_chat_is_persisted_and_opaque_model_state_is_not_exposed(web):
    client, runtime, _ = web
    initial = client.get("/api/state").json()
    target = initial["session"]["id"]
    assert client.post("/api/messages", json={
        "message": "你好", "project_id": initial["project"]["id"],
        "session_id": target, "submission_id": "chat",
    }).status_code == 202
    state = wait_idle(client)
    assert state["messages"][-1]["content"] == "你好，工作台"
    assert "DO_NOT_EXPOSE" not in json.dumps(state)
    assert runtime.session.store.load(target).messages[-1].parts[0].content == "你好，工作台"


def test_web_follow_up_waits_for_the_current_turn(web, monkeypatch):
    client, _, provider = web
    initial = client.get("/api/state").json()
    scope = {"project_id": initial["project"]["id"], "session_id": initial["session"]["id"]}
    entered = threading.Event()
    release = threading.Event()
    requests = []

    async def complete(request):
        requests.append(request)
        if len(requests) == 1:
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
        return response("done")

    monkeypatch.setattr(provider, "acomplete", complete)
    assert client.post("/api/messages", json={
        **scope, "submission_id": "first", "message": "start",
    }).status_code == 202

    try:
        assert entered.wait(5)
        assert client.post("/api/messages", json={
            **scope, "submission_id": "second", "message": "follow up", "mode": "follow_up",
        }).status_code == 202
        queued = client.get("/api/state").json()["pending_messages"]
        assert [item["text"] for item in queued] == ["follow up"]
        assert len(requests) == 1
    finally:
        release.set()

    assert [message["content"] for message in wait_idle(client)["messages"] if message["role"] == "user"] == [
        "start", "follow up",
    ]
    assert len(requests) == 2


def test_directory_picker_lists_folders_without_reading_files(web, tmp_path):
    client, _, _ = web
    (tmp_path / "选择项目").mkdir()
    (tmp_path / "file.txt").write_text("not a directory")
    result = client.post("/api/directories", json={"path": str(tmp_path)}).json()
    assert result["path"] == str(tmp_path)
    assert "选择项目" in [d["name"] for d in result["directories"]]
    assert "file.txt" not in [d["name"] for d in result["directories"]]
    assert client.post("/api/directories", json={"path": "relative/path"}).status_code == 400


def test_local_api_rejects_external_hosts_origins_and_missing_write_token(web):
    client, _, _ = web
    assert client.get("/api/state", headers={"Host": "attacker.example"}).status_code == 403
    assert client.get("/api/state", headers={"Origin": "https://attacker.example"}).status_code == 403
    token = client.headers.pop("X-Raccoon-Token")
    assert client.post("/api/messages", json={"message": "evil"}).status_code == 403
    client.headers["X-Raccoon-Token"] = token
    state = client.get("/api/state").json()
    result = client.post("/api/messages", json={
        "project_id": state["project"]["id"], "session_id": state["session"]["id"],
        "submission_id": "blank-message", "message": " ",
    })
    assert result.status_code == 422
    assert client.get("/").headers["x-frame-options"] == "DENY"


def test_artifact_validation_rejects_outside_hidden_and_custom_store(tmp_path):
    from raccoon.application.delivery.artifacts import validate_artifact_path
    project = tmp_path / "project"
    project.mkdir()
    reports = project / "custom-data" / "reports"
    reports.mkdir(parents=True)
    for path in [tmp_path / "outside.md", project / ".private" / "data.json", reports.parent / "permissions.json"]:
        path.parent.mkdir(exist_ok=True)
        path.write_text("private", encoding="utf-8")
        with pytest.raises(ValueError):
            validate_artifact_path(path, project=project, reports=reports)
    report = reports / "report.md"
    report.write_text("public report", encoding="utf-8")
    assert validate_artifact_path(report, project=project, reports=reports) == report.resolve()
