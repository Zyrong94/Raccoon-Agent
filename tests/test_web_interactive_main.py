import time

import pytest
from fastapi.testclient import TestClient

from raccoon.bootstrap.runtime import create_runtime
from raccoon.bootstrap.web_app import create_web_app
from tests.runtime_fixtures import FakeMcpManager
from tests.web_fixtures import WebProvider, response


@pytest.fixture
def interactive(tmp_path):
    providers = []
    def factory(**kwargs):
        provider = WebProvider([response('main reply')])
        providers.append(provider)
        return create_runtime(**kwargs, provider=provider, tools=[], mcp_manager_factory=lambda configs: FakeMcpManager())
    project = factory(project_root=tmp_path)
    app = create_web_app(project, runtime_factory=factory)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        token = client.get('/api/state').json()['token']
        client.headers['x-raccoon-token'] = token
        yield client, project, providers


def test_interactive_main_is_independent_and_survives_project_navigation(interactive):
    client, project, providers = interactive
    project_id = project.session.session_id
    main = client.get('/api/main-session/state').json()
    assert main['available'] and main['session_id'] != project_id
    guard = {key: main[key] for key in ('session_id', 'generation')}
    result = client.post('/api/main-session/messages', json={**guard, 'message': 'hello main', 'message_id': 'web-1'})
    assert result.status_code == 202
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = client.get('/api/main-session/state').json()
        if not state['busy']:
            break
        time.sleep(.01)
    assert [message['content'] for message in state['messages']] == ['hello main', 'main reply']
    assert client.get('/api/state').json()['messages'] == []
    assert project.session.session_id == project_id
    assert client.post('/api/sidebar/new', json={'project_id': client.get('/api/state').json()['project']['id']}).status_code == 200
    after = client.get('/api/main-session/state').json()
    assert after['session_id'] == main['session_id'] and after['messages'] == state['messages']
    assert len(providers) == 2
    assert client.post('/api/main-session/messages', json={**guard, 'generation': 'stale', 'message': 'no', 'message_id': 'w2'}).status_code == 409
    assert client.post('/api/main-session/messages', json={**guard, 'session_id': project_id, 'message': 'no', 'message_id': 'w3'}).status_code == 409


@pytest.mark.parametrize('endpoint,payload', [
    ('messages', {'message': 'hello', 'message_id': 'w'}),
    ('input', {'request_id': 'permission', 'answer': 'allow_once'}), ('cancel', {}),
    ('model', {'model': 'fake/other'}), ('mode', {'mode': 'auto'}),
])
def test_main_writes_require_same_origin_and_token(interactive, endpoint, payload):
    client, project, providers = interactive
    body = {'session_id': 'main', 'generation': 'owner', **payload}
    path = '/api/main-session/' + endpoint
    assert client.post(path, json=body, headers={'origin': 'https://evil.example'}).status_code == 403
    assert client.post(path, json=body, headers={'x-raccoon-token': 'invalid'}).status_code == 403
    assert len(providers) == 1  # Rejected requests must not even acquire a host.


def test_reading_main_does_not_expose_local_ipc_capability(interactive):
    client, _, _ = interactive
    result = client.get('/api/main-session/state')
    assert result.status_code == 200
    assert 'token' not in result.json() and 'port' not in result.json()
    assert 'DO_NOT_EXPOSE' not in result.text


def test_main_model_and_permission_controls_target_only_the_personal_host(interactive, monkeypatch):
    from unittest.mock import Mock
    client, project, _ = interactive
    state = client.get('/api/main-session/state').json()
    guard = {key: state[key] for key in ('session_id', 'generation')}
    host = client.app.state.main_bridge.owner
    switch = Mock(side_effect=lambda ref: setattr(host.bundle, "model_ref", ref))
    monkeypatch.setattr(type(host.bundle), 'switch_model', switch)
    old_model = project.model_ref
    assert state['permission_mode'] == host.bundle.session.mode
    assert isinstance(state['models'], list)
    result = client.post('/api/main-session/model', json={**guard, 'model': 'fake/other'})
    assert result.status_code == 200
    assert result.json()['model'] == 'fake/other'
    switch.assert_called_once_with('fake/other')
    assert project.model_ref == old_model
    result = client.post('/api/main-session/mode', json={**guard, 'mode': 'auto'})
    assert result.status_code == 200 and result.json()['permission_mode'] == 'auto'
    assert project.session.mode == 'ask'
    assert client.post('/api/main-session/mode', json={**guard, 'generation': 'old', 'mode': 'ask'}).status_code == 409


def test_occupied_host_without_endpoint_is_unavailable_and_never_starts_a_second_runner(interactive):
    import portalocker
    client, project, providers = interactive
    root = project.catalog.root / 'channels'
    root.mkdir(exist_ok=True)
    with portalocker.Lock(str(root / 'main.lock'), timeout=0):
        assert client.get('/api/main-session/state').status_code == 503
        assert len(providers) == 1
