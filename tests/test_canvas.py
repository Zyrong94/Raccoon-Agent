import json

import pytest
from fastapi.testclient import TestClient

from raccoon.bootstrap.runtime import create_runtime
from raccoon.bootstrap.web_app import create_web_app
from raccoon.runtime.model_types import ToolCall
from tests.runtime_fixtures import FakeMcpManager
from tests.web_fixtures import WebProvider, response, wait_idle, web


def open_canvas(web, tmp_path, path="report.html"):
    client, runtime, provider = web
    (tmp_path / path).write_text('<h1>Canvas</h1><button onclick="this.textContent=\'已点击\'">点击</button>', encoding="utf-8")
    provider.responses = iter([response("", [ToolCall("canvas1", "open_canvas", {"path": path, "title": "交互报告"})]), response("已打开")])
    state = client.get("/api/state").json()
    assert client.post("/api/messages", json={
        "project_id": state["project"]["id"], "session_id": state["session"]["id"],
        "submission_id": "open-canvas", "message": "打开报告画布",
    }).status_code == 202
    return wait_idle(client)


def test_canvas_publishes_existing_html_and_restores_after_session_switch_and_restart(web, tmp_path):
    client, runtime, _ = web
    state = open_canvas(web, tmp_path)
    assert state.get("canvas") is not None
    canvas = state["canvas"]
    assert canvas["title"] == "交互报告"
    assert canvas["request_id"]
    artifact = next(a for a in state["artifacts"] if a["id"] == canvas["artifact_id"])
    assert artifact["name"] == "report.html"
    assert client.get(artifact["content_url"]).text.startswith("<h1>Canvas</h1>")
    original = state["session"]["id"]
    assert client.post("/api/sidebar/new", json={"project_id": state["project"]["id"]}).json()["canvas"] is None
    restored = client.post(f"/api/sessions/{original}/activate").json()
    assert restored["canvas"] == canvas
    runtime2 = create_runtime(project_root=tmp_path, session_id=original, resume_session=True,
                              provider=WebProvider([]), tools=[], mcp_manager_factory=lambda configs: FakeMcpManager())
    with TestClient(create_web_app(runtime2), base_url="http://127.0.0.1:8765") as restarted:
        assert restarted.get("/api/state").json()["canvas"] == canvas


def test_canvas_bundle_serves_relative_resources_with_opaque_origin_and_confines_paths(web, tmp_path):
    client, runtime, _ = web
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "app.js").write_text("document.body.dataset.ready='yes'", encoding="utf-8")
    (folder / "style.css").write_text("body { color: teal; }", encoding="utf-8")
    (folder / ".private.js").write_text("secret", encoding="utf-8")
    (folder / "private.py").write_text("secret", encoding="utf-8")
    (tmp_path / "outside.js").write_text("secret", encoding="utf-8")
    state = open_canvas(web, tmp_path, "site/report.html")
    assert state.get("canvas") is not None
    artifact = state["artifacts"][0]
    base = artifact["canvas_url"].rsplit("/", 1)[0] + "/"
    page = client.get(artifact["canvas_url"])
    assert page.status_code == 200
    assert "sandbox allow-scripts" in page.headers["content-security-policy"]
    assert "allow-same-origin" not in page.headers["content-security-policy"]
    script = client.get(base + "app.js", headers={"Origin": "null"})
    assert script.status_code == 200
    assert script.headers["content-type"].split(";")[0] in {"application/javascript", "text/javascript"}
    assert script.headers["access-control-allow-origin"] == "*"
    assert "allow-credentials" not in json.dumps(dict(script.headers))
    assert client.get(base + "style.css").status_code == 200
    for name in ["missing.js", ".private.js", "private.py", "%2e%2e/outside.js", "%2e%2e%5coutside.js"]:
        assert client.get(base + name).status_code == 404, name
    assert client.get("/api/state", headers={"Origin": "null"}).status_code == 403


def test_canvas_bundle_does_not_follow_symlinks_outside_entry_directory(web, tmp_path):
    folder = tmp_path / "site"
    folder.mkdir()
    target = tmp_path / "private.js"
    target.write_text("private", encoding="utf-8")
    try:
        (folder / "escape.js").symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this Windows host")
    state = open_canvas(web, tmp_path, "site/report.html")
    assert state.get("canvas") is not None
    client, _, _ = web
    base = state["artifacts"][0]["canvas_url"].rsplit("/", 1)[0]
    assert client.get(f"{base}/escape.js").status_code == 404
