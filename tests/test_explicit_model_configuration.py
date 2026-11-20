"""Explicit model-library updates do not change the user's selected model."""
from copy import deepcopy
from types import SimpleNamespace

from raccoon.app.session_runtime import SessionRuntime
from raccoon.config.settings import AppConfig


def model_runtime():
    config = {"providers": {"local": {"base_url": "http://localhost:8000/v1", "api_key_env": "TEST_KEY"}},
              "models": {"local/a": {}}, "default_model": "local/a"}
    app_config = AppConfig(env={"TEST_KEY": "offline"}, model_config=config)
    session = SimpleNamespace(data=SimpleNamespace(metadata={}))
    provider = SimpleNamespace(name="local", model="a")
    runtime = SessionRuntime(session=session, provider=provider, app_config=app_config,
                             model_catalog=app_config.model_catalog(), model_ref="local/a")
    return runtime, config


def test_adding_model_updates_choices_without_rebuilding_selected_provider(monkeypatch):
    runtime, config = model_runtime()
    original = runtime.provider
    def forbidden(*args, **kwargs):
        raise AssertionError("Adding an unrelated model rebuilt the selected provider")
    monkeypatch.setattr("raccoon.app.session_runtime.create_provider_for_model", forbidden)
    updated = deepcopy(config)
    updated["models"]["local/b"] = {}
    runtime.update_model_catalog(updated)
    assert runtime.provider is original
    assert runtime.model_ref == "local/a"
    assert set(runtime.model_catalog.models) == {"local/a", "local/b"}


def test_editing_selected_provider_applies_only_after_explicit_update(monkeypatch):
    runtime, config = model_runtime()
    original = runtime.provider
    updated = deepcopy(config)
    updated["providers"]["local"]["base_url"] = "http://localhost:9000/v1"
    assert runtime.provider is original
    calls = []
    def create_provider(app_config, profile):
        calls.append(profile.provider.base_url)
        return SimpleNamespace(name=profile.provider_id, model=profile.model_id)
    monkeypatch.setattr("raccoon.app.session_runtime.create_provider_for_model", create_provider)
    runtime.update_model_catalog(updated)
    assert calls == ["http://localhost:9000/v1"]
    assert runtime.provider is not original
    assert runtime.provider.model == "a"


def test_model_switch_keeps_previous_model_when_history_write_fails(monkeypatch):
    import pytest

    runtime, config = model_runtime()
    original = runtime.provider
    updated = deepcopy(config)
    updated["models"]["local/b"] = {}
    runtime.update_model_catalog(updated)
    def fail_write(*args, **kwargs):
        raise OSError("disk full")
    runtime.session.store = SimpleNamespace(append_session_metadata_updated=fail_write)
    monkeypatch.setattr("raccoon.app.session_runtime.create_provider_for_model", lambda *_: SimpleNamespace(name="local", model="b"))
    with pytest.raises(OSError, match="disk full"):
        runtime.switch_model("local/b")
    assert runtime.provider is original
    assert runtime.model_ref == "local/a"
