"""Offline cross-project navigation and logical conversation contracts."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from raccoon.bootstrap.runtime import create_runtime
from raccoon.config.settings import AppConfig
from raccoon.modules.sessions.store import SessionStore
from raccoon.bootstrap.web_app import create_web_app
from tests.runtime_fixtures import FakeMcpManager
from tests.web_fixtures import WebProvider, response, wait_idle


def record(root, identity, project, *, text="hello", **metadata):
    store = SessionStore(root)
    data = store.create(session_id=identity, thread_data={"workspace_path": str(project)}, **metadata)
    if text:
        store.append_user_message(data, text, user_timezone="UTC")
    return data


@pytest.fixture
def sidebar_web(tmp_path, monkeypatch):
    monkeypatch.setenv('RACCOON_DISABLE_GLOBAL_SKILLS', '1')
    initial, other = tmp_path / 'first', tmp_path / 'second'
    initial.mkdir()
    other.mkdir()
    data = tmp_path / 'custom-data'
    created = []

    def factory(**kwargs):
        created.append(kwargs)
        runtime = create_runtime(**kwargs, provider=WebProvider([response('answer')] * 30),
            tools=[], app_config=AppConfig(env={}, project_config={'memory': {'enabled': False}}),
            mcp_manager_factory=lambda configs: FakeMcpManager())
        return runtime

    runtime = factory(project_root=initial, data_root=data)
    from raccoon.app.web import WebChatApplication
    other_id = WebChatApplication._workspace_id(other)
    (data / 'web_workspaces.json').write_text(json.dumps([{'path': str(other)}]))
    app = create_web_app(runtime, runtime_factory=factory)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        client.headers['X-Raccoon-Token'] = client.get('/api/state').json()['token']
        yield client, runtime, other, other_id, created, app.state.workbench


def test_sidebar_reads_all_projects_without_loading_runtime_or_writing_catalog(sidebar_web):
    client, runtime, other, other_id, created, _ = sidebar_web
    record(runtime.catalog.root, 'first-chat', runtime.project_path, title='same')
    root = other / '.raccoon'
    record(root, 'other-chat', other, title='same')
    state = client.get('/api/state').json()
    sidebar = state['sidebar']
    assert len(created) == 1
    assert {p['id'] for p in sidebar['projects']} == {state['project']['id'], other_id}
    assert {c['session_id'] for c in sidebar['recent']} == {'first-chat', 'other-chat'}
    assert not (root / 'session_index.json').exists()
    assert state['session']['id'] == runtime.session.session_id


def test_project_automatic_resume_and_archive_never_promote_children(sidebar_web):
    client, runtime, other, other_id, created, workbench = sidebar_web
    root = other / '.raccoon'
    record(root, 'child-only', other, text='subagent internal task', parent_session_id='parent', web_archived=True)
    record(root, 'child-active', other, text='another subagent internal task', parent_session_id='parent')
    assert all(conversation['session_id'] != 'child-only'
               for project in client.get('/api/archive').json()['projects'] for conversation in project['conversations'])
    state = client.post(f'/api/workspaces/{other_id}/activate').json()
    assert state['session']['id'] not in {'child-only', 'child-active'} and state['messages'] == []
    assert len(created) == 1
    assert (root / 'sessions' / 'child-only.jsonl').exists()


def test_sidebar_groups_only_explicit_accepted_family_and_filters_drafts(sidebar_web):
    client, runtime, other, other_id, _, _ = sidebar_web
    root = other / '.raccoon'
    record(root, 'original', other, title='same')
    record(root, 'independent', other, title='same')
    for identity, state in [('edited', 'accepted'), ('prepared', 'prepared')]:
        record(root, identity, other, title='same', web_message_revision={
            'workspace_id': other_id, 'family': 'original', 'state': state, 'operation': 'edit'})
    record(root, 'draft', other, text='')
    record(root, 'main', other)
    (root / 'channels').mkdir()
    (root / 'channels' / 'main_session.json').write_text(json.dumps({'session_id': 'main'}))
    (root / 'sessions' / 'corrupt.jsonl').write_text('broken')
    sidebar = client.get('/api/state').json()['sidebar']
    conversations = next(p for p in sidebar['projects'] if p['id'] == other_id)['conversations']
    assert {c['id'] for c in conversations} == {'original', 'independent'}
    assert next(c for c in conversations if c['id'] == 'original')['session_id'] == 'edited'
    assert len(sidebar['recent']) == 2


def test_new_chat_switches_to_blank_and_reuses_draft_then_locks_project(sidebar_web):
    client, runtime, other, other_id, _, _ = sidebar_web
    record(other / '.raccoon', 'old', other, text='old secret')
    result = client.post('/api/sidebar/new', json={'project_id': other_id})
    assert result.status_code == 200, result.text
    state = result.json()
    assert state['messages'] == [] and state['session']['id'] != 'old'
    assert state['sidebar']['new_chat'] is True
    draft = state['session']['id']
    assert client.post('/api/sidebar/new', json={'project_id': other_id}).json()['session']['id'] == draft
    assert client.post('/api/messages', json={
        'project_id': other_id, 'session_id': draft, 'submission_id': 'first', 'message': 'new message',
    }).status_code == 202
    accepted = client.get('/api/state').json()
    assert accepted['sidebar']['new_chat'] is False
    state = wait_idle(client)
    assert client.post('/api/sidebar/new', json={'project_id': state['project']['id'], 'draft_only': True}).status_code == 409


def test_open_history_keeps_project_selection_and_resolves_the_saved_version(sidebar_web):
    client, runtime, other, other_id, _, workbench = sidebar_web
    record(runtime.catalog.root, 'original', runtime.project_path)
    first_id = workbench._workspace_id(runtime.project_path)
    record(runtime.catalog.root, 'edited', runtime.project_path, web_message_revision={
        'workspace_id': first_id, 'family': 'original', 'state': 'accepted', 'operation': 'edit'})
    first_history = {'project_id': first_id, 'conversation_id': 'original'}
    state = client.get('/api/session-view', params=first_history).json()
    assert state['session']['id'] == 'edited'
    assert client.post('/api/sessions/original/activate').status_code == 200
    record(other / '.raccoon', 'other-chat', other)
    other_history = client.get('/api/session-view', params={'project_id': other_id, 'conversation_id': 'other-chat'})
    assert other_history.status_code == 200
    assert other_history.json()['session']['id'] == 'other-chat'
    assert client.get('/api/state').json()['session']['id'] == 'original'
    state = client.get('/api/session-view', params=first_history).json()
    assert state['session']['id'] == 'original'
    from raccoon.application.workspaces.sidebar import SidebarCatalog
    restored = SidebarCatalog(workbench)
    assert restored.snapshot()['recent'][0]['session_id'] in {'original', 'other-chat'}
    assert restored.selected[first_id]['original'] == 'original'
    assert client.get('/api/session-view', params={'project_id': other_id, 'conversation_id': 'original'}).status_code == 400


def test_logical_update_reaches_versions_and_existing_session_api(sidebar_web):
    client, runtime, _, _, _, workbench = sidebar_web
    project_id = workbench._workspace_id(runtime.project_path)
    record(runtime.catalog.root, 'original', runtime.project_path)
    record(runtime.catalog.root, 'edited', runtime.project_path, web_message_revision={
        'workspace_id': project_id, 'family': 'original', 'state': 'accepted', 'operation': 'edit'})
    result = client.patch('/api/sidebar/conversations/original', json={
        'project_id': project_id, 'title': 'renamed', 'pinned': True})
    assert result.status_code == 200, result.text
    for identity in ('original', 'edited'):
        saved = runtime.catalog.get_session(identity)
        assert saved.title == 'renamed' and saved.metadata['web_pinned'] is True
    assert client.patch('/api/sessions/edited', json={
        'project_id': project_id, 'session_id': 'edited', 'submission_id': 'rename-family', 'title': 'Updated',
    }).status_code == 200
    assert runtime.catalog.get_session('original').title == 'Updated'
    client.headers.pop('X-Raccoon-Token')
    assert client.post('/api/sidebar/new', json={}).status_code == 403


def test_missing_project_does_not_prevent_navigation(sidebar_web):
    client, runtime, other, other_id, _, _ = sidebar_web
    other.rmdir()
    sidebar = client.get('/api/state').json()['sidebar']
    assert next(p for p in sidebar['projects'] if p['id'] == other_id)['available'] is False
    assert client.post('/api/sidebar/new', json={}).status_code == 200
    assert client.post('/api/workspaces', json={'path': str(runtime.project_path), 'new_chat': True}).status_code == 200


def test_unknown_project_rejects_all_sidebar_writes(sidebar_web):
    client, *_ = sidebar_web
    assert client.post('/api/sidebar/new', json={'project_id': 'unknown'}).status_code == 409
    assert client.patch('/api/sidebar/conversations/anything', json={'project_id': 'unknown', 'pinned': True}).status_code == 409


def test_selection_save_failure_does_not_switch_project_or_session(sidebar_web, monkeypatch):
    client, runtime, _, _, _, workbench = sidebar_web
    record(runtime.catalog.root, 'target', runtime.project_path)
    initial = runtime.session.session_id
    original_write = Path.write_text
    def fail_selection(path, *args, **kwargs):
        if path == workbench.sidebar.path.with_suffix('.tmp'):
            raise OSError('selection is read only')
        return original_write(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', fail_selection)
    with pytest.raises(OSError, match='selection is read only'):
        client.post('/api/sessions/target/activate')
    assert workbench.selected_session == (workbench._workspace_id(runtime.project_path), initial)
