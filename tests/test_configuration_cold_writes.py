from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from raccoon.config.management import ManagedConfigStore
from raccoon.interfaces.web import configuration


def test_project_skill_writes_and_mcp_rejection_do_not_prepare_chat(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    store = ManagedConfigStore(tmp_path / "managed")
    validate = Mock(wraps=store.validate)
    monkeypatch.setattr(store, "validate", validate)
    monkeypatch.setattr(configuration, "ManagedConfigStore", lambda: store)
    workbench = SimpleNamespace(
        workspace=SimpleNamespace(projects={"p": {"path": str(project)}}),
        configuration_runtime=SimpleNamespace(project_path=tmp_path / "another-project"),
        displays={}, resolve_session=Mock(), require_active_project=Mock(),
        prepare_session=AsyncMock(side_effect=AssertionError("unexpected Agent initialization")),
    )
    app = FastAPI()
    app.state.project_receipts = {}
    configuration.install_configuration_routes(app, workbench)
    body = {"project_id": "p", "session_id": "B", "scope": "project", "submission_id": "save",
            "config": {"name": "sample", "description": "Sample"}, "content": "Example instructions"}
    with TestClient(app) as client:
        response = client.post("/api/configuration/skills/save", json=body)
        assert response.status_code == 200
        validate.assert_called_once_with("skills", body["config"], body["content"])
        file = project / ".agents" / "skills" / "sample" / "SKILL.md"
        assert file.is_file()
        response = client.request("DELETE", "/api/configuration/skills/sample", json={
            "project_id": "p", "session_id": "B", "scope": "project", "submission_id": "delete",
            "revision": response.json()["revision"],
        })
        assert response.status_code == 200 and not file.exists()
        assert client.post("/api/configuration/mcp/save", json={**body, "submission_id": "mcp"}).status_code == 403
    workbench.prepare_session.assert_not_awaited()
    workbench.resolve_session.assert_called_with("p", session_id="B")
    workbench.require_active_project.assert_called_with("p")
