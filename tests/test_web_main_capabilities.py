import time

import pytest
from fastapi.testclient import TestClient

from raccoon.bootstrap.runtime import create_runtime
from raccoon.bootstrap.web_app import create_web_app
from tests.runtime_fixtures import FakeMcpManager
from tests.test_web_attachments import AttachmentProvider, png


@pytest.fixture
def main_web(tmp_path):
    providers = []
    def factory(**kwargs):
        provider = AttachmentProvider()
        providers.append(provider)
        bundle = create_runtime(**kwargs, provider=provider, tools=[],
            mcp_manager_factory=lambda configs: FakeMcpManager())
        return bundle
    project = factory(project_root=tmp_path)
    with TestClient(create_web_app(project, runtime_factory=factory), base_url='http://127.0.0.1:8765') as client:
        client.headers['X-Raccoon-Token'] = client.get('/api/state').json()['token']
        state = client.get('/api/main-session/state').json()
        yield client, client.app.state.main_bridge.owner, providers, {key: state[key] for key in ('session_id', 'generation')}


def upload(client, guard, raw=None, name='image.png', mime='image/png'):
    return client.post('/api/main-session/attachments', params={**guard, 'name': name},
        content=png() if raw is None else raw, headers={'Content-Type': mime})


def idle(client):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = client.get('/api/main-session/state').json()
        if not state['busy']:
            return state
        time.sleep(.01)
    pytest.fail('personal main did not settle')


def test_personal_attachment_only_survives_navigation_and_uses_small_queue_reference(main_web):
    client, host, providers, guard = main_web
    item = upload(client, guard).json()
    assert item['content_url'].startswith('/api/main-session/attachments/')
    assert 'generation=' not in item['content_url']
    assert client.get(item['content_url']).content == png()
    assert client.post('/api/main-session/messages', json={**guard, 'message': '', 'message_id': 'image-only', 'attachment_ids': [item['id']]}).status_code == 202
    state = idle(client)
    assert next(m for m in state['messages'] if m['role'] == 'user')['attachments'] == [item]
    event = host.application.history(host.key)[0].event
    refs = event.payload['content']['attachments']
    assert len(refs) == 1 and refs[0]['sha256'] and 'data_base64' not in str(event.to_dict())
    assert client.get('/api/state').json()['messages'] == []
    assert client.post('/api/sidebar/new', json={'project_id': client.get('/api/state').json()['project']['id']}).status_code == 200
    assert client.get(item['content_url']).content == png()
    assert client.get('/api/main-session/state').json()['messages'] == state['messages']


def test_personal_upload_rejects_stale_identity_spoof_and_missing_token(main_web):
    client, host, providers, guard = main_web
    assert upload(client, {**guard, 'generation': 'old'}).status_code == 409
    assert upload(client, {**guard, 'session_id': 'foreign'}).status_code == 409
    assert upload(client, guard, b'broken').status_code == 400
    assert upload(client, guard, b'x', '../secret', 'text/plain').status_code == 400
    client.headers.pop('X-Raccoon-Token')
    assert upload(client, guard).status_code == 403
    assert not host.application.history(host.key) and providers[-1].requests == []


def test_personal_files_download_safely_and_cannot_cross_project(main_web):
    client, host, _, guard = main_web
    raw = b'<script>untrusted</script>'
    result = upload(client, guard, raw, 'page.html', 'text/html')
    assert result.status_code == 201
    item = result.json()
    for url in [item['content_url'], item['download_url']]:
        read = client.get(url)
        assert read.content == raw and read.headers['content-type'] == 'application/octet-stream'
        assert read.headers['content-disposition'].startswith('attachment;')
    assert client.get(item['download_url'].replace(guard['session_id'], 'foreign')).status_code == 409
    project = client.get('/api/state').json()
    project_scope = {'project_id': project['project']['id'], 'session_id': project['session']['id']}
    foreign = client.post('/api/attachments', params={**project_scope, 'name': 'p.txt'}, content=b'project').json()
    assert client.post('/api/main-session/messages', json={**guard, 'message': 'no', 'message_id': 'foreign', 'attachment_ids': [foreign['id']]}).status_code == 400
    assert client.post('/api/messages', json={**project_scope, 'submission_id': 'foreign-attachment', 'message': 'no', 'attachment_ids': [item['id']]}).status_code == 400
