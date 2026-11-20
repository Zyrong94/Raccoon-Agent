from pathlib import Path

import pytest

from raccoon.config.settings import AppConfig
from raccoon.modules.memory.config import load_memory_config


def load(tmp_path, raw=None, env=None):
    return load_memory_config(AppConfig(env=env or {}, project_config={"memory": raw or {}}), project_root=tmp_path, data_root=tmp_path / "data")


def test_default_on_global_single_user_and_disabled_creates_nothing(tmp_path):
    config = load(tmp_path)
    other = load(tmp_path / "other")
    assert config is not None
    assert config.storage_path == other.storage_path == Path.home() / ".raccoon" / "memory"
    assert config.namespace == "raccoon-personal"
    assert "OPENAI_API_KEY" in config.unavailable_reason
    assert load(tmp_path, {"enabled": False}) is None
    assert not (tmp_path / "data").exists()


def test_config_merge_keys_and_embedding_space_fingerprint(tmp_path):
    app = AppConfig(env={"LLM_KEY": "secret-a", "EMBED_KEY": "secret-b"},
        global_config={"memory": {"llm": {"model": "extract", "api_key_env": "LLM_KEY"}, "embedder": {"api_key_env": "EMBED_KEY"}}},
        project_config={"memory": {"storage": {"path": "shared-memory"}, "llm": {"base_url": "https://example.org/v1"}}})
    config = load_memory_config(app, project_root=tmp_path, data_root=tmp_path)
    assert config.storage_path == tmp_path / "shared-memory"
    assert config.llm.model == "extract"
    assert config.llm.base_url == "https://example.org/v1"
    assert config.llm.api_key == "secret-a"
    assert "secret-a" not in repr(config)
    assert config.unavailable_reason is None
    changed = load(tmp_path, {"embedder": {"model": "other-model"}})
    assert config.embedding_fingerprint != changed.embedding_fingerprint


@pytest.mark.parametrize("raw", [{"enabled": "yes"}, {"top_k": 0}, {"search_timeout_seconds": float("nan")}, {"llm": "bad"}, {"embedder": {"dimensions": True}}, {"storage": {"provider": "other"}}])
def test_structural_config_errors_are_explicit(tmp_path, raw):
    with pytest.raises(ValueError, match="memory"):
        load(tmp_path, raw)
