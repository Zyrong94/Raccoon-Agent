from pathlib import Path



import pytest



from raccoon.config.management import ManagedConfigStore

from raccoon.config.management_sources import source_records

from raccoon.config.subagents_config import discover_subagent_configs, load_subagent_config

from raccoon.modules.skills.discovery import discover_all_skills
from raccoon.modules.skills.models import SkillSource
from tests.test_subagents_config import write_subagent





@pytest.fixture

def isolated_store(tmp_path, monkeypatch):

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    monkeypatch.setenv("RACCOON_SKILL_ROOTS", "")

    monkeypatch.delenv("RACCOON_DISABLE_GLOBAL_SKILLS", raising=False)

    return ManagedConfigStore()





def forbid_asset_scan(*_args, **_kwargs):

    raise AssertionError("Runtime discovery must not recursively scan package assets")





def write_skill(directory, name, description):

    directory.mkdir(parents=True)

    path = directory / "SKILL.md"

    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\nInstructions", encoding="utf-8")

    asset = directory / "assets" / "nested" / "large.bin"

    asset.parent.mkdir(parents=True)

    asset.write_bytes(b"asset")

    return path





def test_runtime_skills_skip_assets_and_prefer_current_project(isolated_store, tmp_path, monkeypatch):

    store = isolated_store

    store.save("skills", {"name": "shared", "description": "Managed override"}, "Instructions")

    project = tmp_path / "project"

    write_skill(project / ".agents" / "skills" / "shared", "shared", "Project original")

    project_path = write_skill(project / ".agents" / "skills" / "local", "local", "Project skill")

    write_skill(tmp_path / "home" / ".agents" / "skills" / "global", "global", "Global skill")

    write_skill(project / ".agents" / "skills" / "removed", "removed", "Deleted skill")

    deleted = next(record for record in source_records(store, project_path=project, domains={"skills"})["skills"] if record["id"] == "removed")

    store.delete("skills", "removed", deleted["revision"], scope="project", project_path=project)

    monkeypatch.setattr(Path, "rglob", forbid_asset_scan)



    skills = {skill.name: skill for skill in discover_all_skills(project).resolved().skills}

    assert set(skills) == {"shared", "local", "global"}

    assert skills["shared"].source == SkillSource.PROJECT_AGENT_SKILL

    assert skills["shared"].description == "Project original"

    assert skills["local"].source == SkillSource.PROJECT_AGENT_SKILL

    assert skills["global"].source == SkillSource.GLOBAL_AGENT_SKILL

    project_path.write_text("---\nname: local\ndescription: External update\n---\nInstructions", encoding="utf-8")

    assert next(skill for skill in discover_all_skills(project).skills if skill.name == "local").description == "External update"





def test_runtime_subagents_skip_assets_and_preserve_overrides_and_tombstones(isolated_store, tmp_path, monkeypatch):

    store = isolated_store

    store.save("subagents", {"name": "reader", "tools": []}, "Managed instructions")

    data_root = tmp_path / "data"

    write_subagent(data_root, "reader", soul="Original instructions")

    write_subagent(data_root, "local", soul="Local instructions")

    write_subagent(data_root, "removed", soul="Deleted instructions")

    deleted = next(record for record in source_records(store, data_root=data_root, domains={"subagents"})["subagents"] if record["id"] == "removed")

    store.delete("subagents", "removed", deleted["revision"], source=deleted)

    monkeypatch.setattr(Path, "rglob", forbid_asset_scan)



    configs = discover_subagent_configs(data_root=data_root)

    assert configs["reader"].system_prompt == "Managed instructions"

    assert configs["local"].system_prompt == "Local instructions"

    assert "reviewer" in configs and "removed" not in configs

    assert load_subagent_config("local", data_root=data_root).system_prompt == "Local instructions"





def test_configuration_edit_revision_still_tracks_nested_package_assets(isolated_store, tmp_path):

    project = tmp_path / "project"

    definition = write_skill(project / ".agents" / "skills" / "sample", "sample", "Instructions")

    first = source_records(isolated_store, project_path=project, domains={"skills"})["skills"][0]

    (definition.parent / "assets" / "nested" / "large.bin").write_bytes(b"asset changed externally")

    updated = source_records(isolated_store, project_path=project, domains={"skills"})["skills"][0]

    assert first["content"] == updated["content"]

    assert first["revision"] != updated["revision"]















def test_configuration_asset_safety_checks_remain_enabled(isolated_store, tmp_path, monkeypatch):

    from raccoon.config import management



    project = tmp_path / "project"

    definition = write_skill(project / ".agents" / "skills" / "sample", "sample", "Instructions")

    unsafe = definition.parent / "assets" / "nested" / "large.bin"

    original = management._safe



    def check(path):

        if path == unsafe:

            raise ValueError("Unsupported package link")

        original(path)



    monkeypatch.setattr(management, "_safe", check)

    listing = source_records(isolated_store, project_path=project, domains={"skills"})

    assert listing["skills"] == []

    assert listing["warnings"]





def test_registered_projects_do_not_contribute_skills(isolated_store, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    write_skill(first / ".agents/skills/first", "first", "First")
    write_skill(second / ".agents/skills/second", "second", "Second")
    isolated_store.register_source(first)
    isolated_store.register_source(second)
    records = source_records(isolated_store, project_path=second, domains={"skills"})["skills"]
    assert [record["id"] for record in records] == ["second"]
    assert source_records(isolated_store, domains={"skills"})["skills"] == []
