"""Removed custom main Agent routes cannot access or mutate saved role files."""

from tests.web_fixtures import web  # noqa: F401


def test_main_agent_crud_routes_are_removed_without_touching_saved_files(web):
    client, runtime, _ = web
    folder = runtime.catalog.root / "agents" / "legacy"
    folder.mkdir(parents=True)
    files = {"config.yaml": "name: legacy\n", "SOUL.md": "Saved instructions\n"}
    for name, content in files.items():
        (folder / name).write_text(content, encoding="utf-8")
    body = {"config": {"name": "legacy"}, "soul": "Changed", "revision": "0" * 64}
    for method, path in [("GET", "/api/agents"), ("POST", "/api/agents"),
                         ("PUT", "/api/agents/legacy"), ("DELETE", "/api/agents/legacy")]:
        response = client.request(method, path, json=body if method != "GET" else None)
        assert response.status_code == 404, response.text
    assert {name: (folder / name).read_text(encoding="utf-8") for name in files} == files
    assert not any(route.path.startswith("/api/agents") for route in client.app.routes)
    paths = {route.path for route in client.app.routes}
    assert "/api/subagents" in paths
    assert "/api/main-session/subagents" in paths
    assert "/api/configuration/{domain}/save" in paths
    state = client.get("/api/state").json()
    configuration = client.post("/api/configuration", json={
        "project_id": state["project"]["id"], "session_id": state["session"]["id"],
        "domains": ["skills", "subagents"],
    })
    assert configuration.status_code == 200
    assert {"skills", "subagents"} <= configuration.json().keys()
