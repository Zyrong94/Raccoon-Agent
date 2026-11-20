"""Configuration writes preserve the latest saved revision and package data."""

import pytest

from raccoon.config.management import ConfigurationConflict, ManagedConfigStore
from raccoon.config.model_store import ModelConfigStore


def test_model_save_rejects_an_invalid_draft_and_a_stale_revision(tmp_path):
    store = ModelConfigStore(tmp_path / "models.toml")
    fields = {
        "provider_id": "example", "model_id": "coding", "base_url": "https://example.test/v1",
        "api_key_env": "RACCOON_TEST_KEY", "label": "Coding",
    }
    first = store.save(fields)
    latest = store.save({**fields, "label": "Updated"}, identifier=first["id"], revision=first["revision"])
    saved = store.path.read_bytes()

    with pytest.raises(ConfigurationConflict):
        store.save(fields, identifier=first["id"], revision=first["revision"])
    with pytest.raises(ValueError):
        store.save({**fields, "api_key_env": "not an environment name"},
                   identifier=latest["id"], revision=latest["revision"])

    assert store.path.read_bytes() == saved
    assert store.records() == [latest]


def test_editing_project_skill_preserves_its_assets_and_rejects_stale_save(tmp_path):
    store = ManagedConfigStore(root=tmp_path / "managed")
    scope = {"scope": "project", "project_path": tmp_path}
    draft = {"name": "review", "description": "Review source"}
    first = store.save("skills", draft, "Original instructions", files={"notes.txt": b"Keep me"}, **scope)
    second = store.save("skills", {**draft, "description": "Review carefully"}, "Updated instructions",
                        identifier="review", revision=first["revision"], **scope)
    folder = tmp_path / ".agents" / "skills" / "review"
    assert (folder / "notes.txt").read_bytes() == b"Keep me"
    assert "Updated instructions" in (folder / "SKILL.md").read_text(encoding="utf-8")

    with pytest.raises(ConfigurationConflict):
        store.save("skills", draft, "Stale draft", identifier="review", revision=first["revision"], **scope)

    store.delete("skills", "review", second["revision"], **scope)
    assert not folder.exists()
