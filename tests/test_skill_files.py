from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from raccoon.config.management import ConfigurationConflict, ManagedConfigStore
from raccoon.config.management_sources import source_records
from raccoon.config.skill_files import skill_records
from raccoon.interfaces.web.configuration import install_configuration_routes
from raccoon.modules.skills.discovery import discover_all_skills
from raccoon.modules.skills.migrate import migrate_managed_skills
from raccoon.modules.tools.registry import ToolRegistry


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("RACCOON_DISABLE_GLOBAL_SKILLS", raising=False)
    return ManagedConfigStore()


def write_skill(directory, name="review", text="Instructions"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(f"---\nname: {name}\ndescription: {text}\n---\n{text}", encoding="utf-8")
    return path


def test_three_roots_precedence_and_project_isolation(library, tmp_path, monkeypatch):
    shared = tmp_path / "home/.agents/skills/review"
    write_skill(shared, text="Shared")
    saved = library.save("skills", {"name": "review"}, "Raccoon")
    project = tmp_path / "project"
    other = tmp_path / "other"
    write_skill(project / ".agents/skills/review", text="Project")
    write_skill(other / ".agents/skills/foreign", name="foreign")
    library.register_source(other)
    write_skill(tmp_path / "home/.codex/skills/codex", name="codex")
    write_skill(library.root / "skills/old", name="old")
    write_skill(tmp_path / "extra/extra", name="extra")
    monkeypatch.setenv("RACCOON_SKILL_ROOTS", str(tmp_path / "extra"))
    catalog = discover_all_skills(project)
    assert [(skill.name, skill.description) for skill in catalog.skills] == [("review", "Project")]
    assert Path(saved["path"]) == library.skills_root / "review/SKILL.md"
    records = source_records(library, project_path=project, domains={"skills"})["skills"]
    assert {record["scope"] for record in records} == {"shared", "global", "project"}
    assert [record["scope"] for record in records if not record["overridden"]] == ["project"]
    assert {skill.name for skill in discover_all_skills(tmp_path / "empty").skills} == {"review"}
    assert discover_all_skills(tmp_path / "empty").skills[0].root == str(library.skills_root)


def test_import_edit_delete_preserves_assets_and_restores_shared_fallback(library, tmp_path):
    write_skill(tmp_path / "home/.agents/skills/review", text="Shared")
    source = tmp_path / "incoming"
    write_skill(source)
    (source / "reference.txt").write_text("Evidence", encoding="utf-8")
    saved = library.import_path("skills", source)[0]
    assert not (library.root / "registry.json").exists()
    edited = library.save("skills", {"name": "review", "description": "Updated"}, "Updated body",
                          identifier="review", revision=saved["revision"])
    assert (library.skills_root / "review/reference.txt").read_text() == "Evidence"
    assert (source / "SKILL.md").read_text().endswith("Instructions")
    library.delete("skills", "review", edited["revision"])
    assert discover_all_skills(tmp_path).skills[0].description == "Shared"
    assert library.deleted_ids("skills") == set()


def test_project_edit_stays_in_original_directory_and_checks_asset_revision(library, tmp_path):
    project = tmp_path / "project"
    path = write_skill(project / ".agents/skills/folder-name")
    asset = path.parent / "reference.txt"
    asset.write_text("before")
    original = skill_records(project)[0]
    asset.write_text("changed externally")
    with pytest.raises(ConfigurationConflict):
        library.save("skills", {"name": "review"}, "Edit", identifier="review",
                     revision=original["revision"], scope="project", project_path=project)
    fresh = skill_records(project)[0]
    saved = library.save("skills", {"name": "review"}, "Edit", identifier="review",
                         revision=fresh["revision"], scope="project", project_path=project)
    assert path.read_text().endswith("Edit")
    assert saved["path"] == str(path)
    assert not library.skills_root.exists()
    assert asset.read_text() == "changed externally"


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_shared_scope_cannot_be_modified(library, tmp_path, operation):
    path = write_skill(tmp_path / "home/.agents/skills/review")
    record = skill_records()[0]
    assert record["read_only"]
    with pytest.raises(ValueError, match="只读"):
        if operation == "save":
            library.save("skills", {"name": "review"}, "Changed", identifier="review",
                         revision=record["revision"], scope="shared")
        else:
            library.delete("skills", "review", record["revision"], scope="shared")
    assert path.read_text().endswith("Instructions")


def test_failed_package_edit_preserves_original_and_cleans_staging(library):
    saved = library.save("skills", {"name": "review"}, "Original")
    with pytest.raises(ValueError):
        library.save("skills", {"name": "review"}, "Broken", identifier="review",
                     revision=saved["revision"], files={"../outside.txt": b"bad"})
    assert (library.skills_root / "review/SKILL.md").read_text().endswith("Original")
    assert not list(library.skills_root.glob(".staging-*"))


def test_case_collision_and_unregistered_directory_cannot_be_overwritten(library):
    library.save("skills", {"name": "review"}, "Original")
    with pytest.raises(ConfigurationConflict):
        library.save("skills", {"name": "Review"}, "Replacement")
    folder = library.skills_root / "occupied"
    folder.mkdir()
    with pytest.raises(ConfigurationConflict):
        library.save("skills", {"name": "occupied"}, "Replacement")


def test_global_disable_affects_runtime_but_not_file_management(library, tmp_path, monkeypatch):
    monkeypatch.setenv("RACCOON_DISABLE_GLOBAL_SKILLS", "1")
    library.save("skills", {"name": "review"}, "Instructions")
    assert discover_all_skills(tmp_path).skills == []
    assert library.records("skills")[0]["id"] == "review"


def test_migration_preserves_originals_skips_deleted_and_never_overwrites(library):
    old = library.root / "skills"
    write_skill(old / "review")
    write_skill(old / "removed", name="removed")
    write_skill(old / "collision", name="collision", text="Old")
    (old / "review/script.py").write_text("print('hello')")
    library._write({"skills": {}, "_deleted": {"skills": ["removed"]}})
    library.save("skills", {"name": "collision"}, "Keep")
    assert migrate_managed_skills(library)[-1]["status"] == "ready"
    assert not (library.skills_root / "review").exists()
    report = {item["name"]: item["status"] for item in migrate_managed_skills(library, apply=True)}
    assert report == {"collision": "conflict", "removed": "skipped_deleted", "review": "copied"}
    assert (old / "review/script.py").exists()
    assert (library.skills_root / "review/script.py").exists()
    assert (library.skills_root / "collision/SKILL.md").read_text().endswith("Keep")
    assert not (library.skills_root / "removed").exists()


@pytest.fixture
def client(library, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    catalog = SimpleNamespace(root=tmp_path / "data")
    runtime = SimpleNamespace(project_path=project, session=SimpleNamespace(tool_registry=ToolRegistry()))
    workbench = SimpleNamespace(
        workspace=SimpleNamespace(projects={"p": {"path": str(project)}}, catalog=lambda _: catalog),
        configuration_runtime=runtime, displays={},
        resolve_session=lambda project_id, session_id: session_id,
        require_active_project=lambda project_id: None,
    )
    app = FastAPI()
    app.state.project_receipts = {}
    install_configuration_routes(app, workbench)
    with TestClient(app) as client:
        yield client, project, {"project_id": "p", "session_id": "s"}


def test_web_crud_uses_requested_scope_and_exposes_shared_readonly(client, library, tmp_path):
    client, project, target = client
    write_skill(tmp_path / "home/.agents/skills/review", text="Shared")
    for scope in ["global", "project"]:
        response = client.post("/api/configuration/skills/save", json={
            **target, "submission_id": uuid4().hex, "scope": scope,
            "config": {"name": "review"}, "content": scope,
        })
        assert response.status_code == 200, response.text
    response = client.post("/api/configuration", json={**target, "domains": ["skills"]})
    assert response.status_code == 200, response.text
    listing = response.json()
    records = {record["scope"]: record for record in listing["skills"]}
    assert set(records) == {"global", "project", "shared"}
    assert records["shared"]["read_only"]
    assert records["global"]["overridden"]
    assert not records["project"]["overridden"]
    shared = records["shared"]
    response = client.request("DELETE", "/api/configuration/skills/review", json={
        **target, "submission_id": "delete-shared", "scope": "shared", "revision": shared["revision"],
    })
    assert response.status_code == 400
    response = client.post("/api/configuration/skills/save", json={
        **target, "submission_id": "edit-project",
        "scope": "project", "id": "review", "revision": records["project"]["revision"],
        "config": {"name": "review"}, "content": "Updated project",
    })
    assert response.status_code == 200
    response = client.request("DELETE", "/api/configuration/skills/review", json={
        **target, "submission_id": "delete-project",
        "scope": "project", "revision": response.json()["revision"],
    })
    assert response.status_code == 200
    assert discover_all_skills(project).skills[0].root == str(library.skills_root)


def test_web_import_can_target_current_project(client, tmp_path, library):
    client, project, target = client
    source = tmp_path / "incoming"
    write_skill(source)
    response = client.post("/api/configuration/skills/import", json={
        **target, "submission_id": "import", "path": str(source), "scope": "project",
    })
    assert response.status_code == 200, response.text
    assert (project / ".agents/skills/review/SKILL.md").is_file()
    assert not library.skills_root.exists()


def test_web_skill_listing_does_not_need_or_write_the_old_registry(client, library):
    client, _, target = client
    library.save("skills", {"name": "review"}, "Instructions")
    library.root.mkdir(parents=True)
    registry = library.root / "registry.json"
    registry.write_text("invalid old registry", encoding="utf-8")
    response = client.post("/api/configuration", json={**target, "domains": ["skills"]})
    assert response.status_code == 200
    assert response.json()["skills"][0]["id"] == "review"
    assert registry.read_text() == "invalid old registry"
