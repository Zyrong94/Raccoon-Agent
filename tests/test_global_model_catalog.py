"""全局唯一模型文件、会话选择和推理强度的离线验证。"""
from copy import deepcopy
from pathlib import Path

import pytest

from raccoon.config import AppConfig, load_config
from raccoon.config.models import ModelCatalogError, build_model_catalog
from raccoon.config.model_store import ModelConfigStore
from raccoon.config.management import ConfigurationConflict, ManagedConfigStore
from raccoon.integrations.providers.factory import create_provider_for_model, ProviderConfigError
from tests.web_fixtures import web


@pytest.fixture
def model_config():
    return {
        "default_model": "local/a",
        "providers": {"local": {"api_key_env": "TEST_MODEL_KEY", "base_url": "https://example.test/v1"}},
        "models": {
            "local/a": {"context_window": 128000, "supported_reasoning_efforts": ["low", "high"],
                        "request": {"max_tokens": 1000, "reasoning_effort": "high", "temperature": 0}},
            "local/b": {"context_window": 64000, "supported_reasoning_efforts": []},
        },
    }


@pytest.fixture
def model_store(tmp_path, monkeypatch, model_config):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "global"))
    store = ModelConfigStore()
    store._write(model_config)
    return store


def test_global_models_ignore_project_and_legacy_registry(tmp_path, model_store):
    project = tmp_path / "project"
    project.mkdir()
    (project / "raccoon.toml").write_text('default_model = "legacy/wrong"\n[models."legacy/wrong"]\n', encoding="utf-8")
    registry = model_store.path.parent / "managed" / "registry.json"
    registry.parent.mkdir()
    registry.write_text('{"models":{"legacy/wrong":{"id":"legacy/wrong"}}}', encoding="utf-8")
    config = load_config(project_root=project, env={"TEST_MODEL_KEY": "offline"})
    assert set(config.model_catalog().models) == {"local/a", "local/b"}
    assert config.model_catalog().default_ref == "local/a"
    assert {record["id"] for record in ManagedConfigStore().records("models")} == {"local/a", "local/b"}


def test_store_round_trip_updates_siblings_and_rejects_stale_revision(model_store):
    first, second = model_store.records()
    fields = deepcopy(first["config"])
    fields["base_url"] = "https://changed.test/v1"
    fields["parallel_tool_calls"] = False
    model_store.save(fields, identifier=first["id"], revision=first["revision"])
    catalog = build_model_catalog(model_store.read())
    assert catalog.models["local/b"].provider.base_url == "https://changed.test/v1"
    assert catalog.models["local/a"].parallel_tool_calls is False
    assert catalog.models["local/b"].parallel_tool_calls is True
    with pytest.raises(ConfigurationConflict):
        model_store.save(second["config"], identifier=second["id"], revision=second["revision"])


def test_failed_save_does_not_change_file(model_store):
    record = model_store.records()[0]
    before = model_store.path.read_bytes()
    fields = deepcopy(record["config"])
    fields["request"]["reasoning_effort"] = "unsupported"
    with pytest.raises(ModelCatalogError, match="推理强度"):
        model_store.save(fields, identifier=record["id"], revision=record["revision"])
    assert model_store.path.read_bytes() == before


def test_delete_default_does_not_silently_choose_another_model(model_store):
    first = model_store.records()[0]
    model_store.delete(first["id"], first["revision"])
    catalog = build_model_catalog(model_store.read())
    assert catalog.default_ref is None
    assert list(catalog.models) == ["local/b"]
    from raccoon.bootstrap.runtime import _initial_model_profile
    with pytest.raises(ModelCatalogError, match="默认模型"):
        _initial_model_profile(catalog, model_spec=None)
    with pytest.raises(ModelCatalogError, match="重新选择"):
        _initial_model_profile(catalog, model_spec="local/a")


@pytest.mark.parametrize("field,value", [("credential_ref", "old-id"), ("api_key", "never-store"), ("type", "openai-responses")])
def test_provider_rejects_credentials_and_protocol_fields(model_config, field, value):
    model_config["providers"]["local"][field] = value
    with pytest.raises(ModelCatalogError, match="未知字段"):
        build_model_catalog(model_config)


@pytest.mark.parametrize("efforts,default", [([], "high"), (["low"], "high"), (["low", "low"], "low"), ("low", "low")])
def test_invalid_reasoning_options_are_rejected(model_config, efforts, default):
    model_config["models"]["local/a"]["supported_reasoning_efforts"] = efforts
    model_config["models"]["local/a"]["request"]["reasoning_effort"] = default
    with pytest.raises(ModelCatalogError, match="推理强度"):
        build_model_catalog(model_config)


def test_factory_reads_only_configured_environment_and_keeps_effort_separate(model_config):
    catalog = build_model_catalog(model_config)
    profile = catalog.models["local/a"]
    assert "reasoning_effort" not in profile.request.extra_body
    with pytest.raises(ProviderConfigError, match="TEST_MODEL_KEY"):
        create_provider_for_model(AppConfig(env={"OPENAI_API_KEY": "wrong"}), profile)
    provider = create_provider_for_model(AppConfig(env={"TEST_MODEL_KEY": "offline"}), profile)
    assert provider.model == "a"
    from raccoon.application.conversations.model_switching import model_request_options
    assert model_request_options(profile).extra_body == {"reasoning_effort": "high"}


def test_import_is_atomic_when_second_model_invalid(model_store, model_config):
    before = model_store.path.read_bytes()
    incoming = deepcopy(model_config)
    incoming["models"]["local/b"]["context_window"] = -1
    with pytest.raises(ModelCatalogError):
        model_store.import_config(incoming)
    assert model_store.path.read_bytes() == before


def test_independent_sessions_restore_model_and_effort(tmp_path, model_store, model_config):
    from raccoon.bootstrap.runtime import create_runtime
    from tests.runtime_fixtures import FakeMcpManager
    config = AppConfig(env={"TEST_MODEL_KEY": "offline"}, model_config=model_config,
                       global_config={"memory": {"enabled": False}})
    def runtime(session_id, resume=False):
        return create_runtime(project_root=tmp_path / "project", data_root=tmp_path / "sessions",
                              app_config=config, session_id=session_id, resume_session=resume,
                              tools=[], mcp_manager_factory=lambda _: FakeMcpManager())
    first = runtime("first")
    second = runtime("second")
    try:
        first.set_effort("low")
        second.switch_model("local/b")
        assert first.provider.model == "a"
        assert second.provider.model == "b"
        assert first.request_options.extra_body["reasoning_effort"] == "low"
        assert "reasoning_effort" not in second.request_options.extra_body
        with pytest.raises(ValueError, match="不支持"):
            second.set_effort("high")
    finally:
        first.close()
        second.close()
    first = runtime("first", True)
    second = runtime("second", True)
    try:
        assert first.provider.model == "a"
        assert first.request_options.extra_body["reasoning_effort"] == "low"
        assert second.provider.model == "b"
        first.set_effort(None)
        assert first.request_options.extra_body["reasoning_effort"] == "high"
    finally:
        first.close()
        second.close()

def test_model_api_uses_global_file_and_rejects_plaintext_keys(model_store, web):
    client, _, _ = web
    state = client.get('/api/state').json()
    scope = {'project_id': state['project']['id'], 'session_id': state['session']['id']}
    registry = model_store.path.parent / 'managed' / 'registry.json'
    previous_registry = registry.read_bytes() if registry.exists() else None
    payload = {
        **scope, 'submission_id': 'save',
        'config': {'provider_id': 'other', 'model_id': 'model', 'api_key_env': 'OTHER_KEY',
                   'supported_reasoning_efforts': ['custom'], 'request': {'reasoning_effort': 'custom'}},
    }
    response = client.post('/api/configuration/models/save', json=payload)
    assert response.status_code == 200, response.text
    assert 'other/model' in model_store.read()['models']
    current_registry = registry.read_bytes() if registry.exists() else None
    assert current_registry == previous_registry
    saved = response.json()
    response = client.post('/api/configuration/models/save', json={
        **payload, 'submission_id': 'plaintext', 'api_key': 'do-not-save',
    })
    assert response.status_code == 422
    response = client.request('DELETE', '/api/configuration/models/other/model', json={
        **scope, 'submission_id': 'delete', 'revision': saved['revision'],
    })
    assert response.status_code == 200, response.text
    assert 'other/model' not in model_store.read()['models']


def test_session_buttons_restore_model_and_new_session_uses_default(tmp_path, model_store, model_config):
    from fastapi.testclient import TestClient
    from raccoon.bootstrap.runtime import create_runtime
    from raccoon.bootstrap.web_app import create_web_app
    from tests.runtime_fixtures import FakeMcpManager
    config = AppConfig(env={"TEST_MODEL_KEY": "offline"}, model_config=model_config,
                       global_config={"memory": {"enabled": False}})
    def factory(**kwargs):
        return create_runtime(**kwargs, app_config=config, tools=[],
                              mcp_manager_factory=lambda _: FakeMcpManager())
    runtime = factory(project_root=tmp_path, session_id="original")
    runtime.session.store.append_user_message(runtime.session.data, "existing conversation",
                                              user_timezone=runtime.session.user_timezone)
    with TestClient(create_web_app(runtime, runtime_factory=factory), base_url="http://127.0.0.1:8765") as client:
        initial = client.get('/api/state').json()
        client.headers["X-Raccoon-Token"] = initial['token']
        project_id = initial['project']['id']
        assert client.post('/api/model', json={
            'project_id': project_id, 'session_id': 'original', 'submission_id': 'switch', 'model': 'local/b',
        }).status_code == 200
        created = client.post('/api/sidebar/new', json={'project_id': project_id})
        assert created.status_code == 200
        prepared = client.post('/api/execution/prepare', json={
            'project_id': project_id, 'session_id': created.json()['session']['id'], 'submission_id': 'prepare',
        })
        assert prepared.status_code == 200
        assert prepared.json()['model'] == 'local/a'
        restored = client.post('/api/sessions/original/activate')
        assert restored.status_code == 200
        assert restored.json()['model'] == 'local/b'


def test_explicit_catalog_update_keeps_session_choice_and_effort(tmp_path, model_store, model_config):
    from raccoon.bootstrap.runtime import create_runtime
    from tests.runtime_fixtures import FakeMcpManager
    runtime = create_runtime(
        project_root=tmp_path, tools=[], session_id="refresh",
        app_config=AppConfig(env={"TEST_MODEL_KEY": "offline"}, model_config=model_config,
                             global_config={"memory": {"enabled": False}}),
        mcp_manager_factory=lambda _: FakeMcpManager(),
    )
    try:
        runtime.set_effort("low")
        changed = model_store.read()
        changed["default_model"] = "local/b"
        changed["models"]["local/a"]["label"] = "Updated"
        model_store._write(changed)
        runtime.update_model_catalog(model_store.read())
        assert runtime.provider.model == "a"
        assert runtime.request_options.extra_body["reasoning_effort"] == "low"
        record = next(record for record in model_store.records() if record["id"] == "local/a")
        model_store.delete(record["id"], record["revision"])
        runtime.update_model_catalog(model_store.read())
        assert runtime.provider.model == "a"
        runtime.switch_model("local/b")
        runtime.update_model_catalog(model_store.read())
        assert runtime.provider.model == "b"
    finally:
        runtime.close()


def test_empty_catalog_can_open_configuration_page_without_network(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from raccoon.bootstrap.web_app import create_web_app
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'global'))

    def factory(**kwargs):
        pytest.fail('Opening the Web app or inspecting models must not create a runtime')

    app = create_web_app(project_path=tmp_path, app_config=AppConfig(env={}), runtime_factory=factory)
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        state = client.get('/api/state').json()
        assert state['execution_ready'] is False
        assert state['model'] is None and state['models'] == []
        response = client.post('/api/configuration', headers={'X-Raccoon-Token': state['token']}, json={
            'project_id': state['project']['id'], 'session_id': state['session']['id'], 'domains': ['models'],
        })
        assert response.status_code == 200
        assert response.json()['models'] == []
        assert not app.state.workbench.displays
