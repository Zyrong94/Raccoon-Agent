"""Record edits must not prepare an Agent or change unrelated navigation."""
import asyncio
from types import SimpleNamespace

from raccoon.application.workspaces.catalog import workspace_id
from raccoon.application.workspaces.sidebar import SidebarCatalog
from raccoon.application.workspaces.store import WorkspaceStore
from raccoon.app.web import WebChatApplication
from raccoon.interfaces.web.event_stream import WebEventStream
from raccoon.app.conversations import ConversationService
from raccoon.modules.sessions.store import SessionStore


def cold_workspace(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = project / ".raccoon"
    store = SessionStore(root)
    first = store.create(session_id="selected")
    second = store.create(session_id="cold")
    manager = WebChatApplication.__new__(WebChatApplication)
    identity = workspace_id(project)
    manager.workspace = WorkspaceStore(project, root)
    manager.sidebar_selection = {}
    manager.displays = {}
    manager._project_sessions = {}
    manager.selected_session = (identity, first.session_id)
    manager.conversations = ConversationService()
    manager.events = WebEventStream()
    manager.sidebar = SidebarCatalog(manager)
    events = []
    publish = manager.events.publish
    def capture(event):
        events.append(event)
        return publish(event)
    manager.events.publish = capture
    def forbidden(*args, **kwargs):
        raise AssertionError("Record operation prepared an Agent")
    manager.runtime_factory = forbidden
    return manager, identity, store, second, events


def test_cold_metadata_update_keeps_selection_and_publishes_only_sidebar(tmp_path):
    manager, project, store, session, events = cold_workspace(tmp_path)
    selected = manager.selected_session
    before = manager.project_sessions(project, refresh=False)
    manager.sidebar_update(project, session.session_id, {"title": "Renamed", "pinned": True})
    manager.sidebar_update(project, session.session_id, {"archived": True})
    after = manager.project_sessions(project, refresh=False)
    row = next(item for item in after if item["id"] == session.session_id)
    assert after is not before and row["title"] == "Renamed" and row["archived"]
    saved = store.load(session.session_id)
    assert saved.metadata["title"] == "Renamed"
    assert saved.metadata["web_pinned"] is True
    assert saved.metadata["web_archived"] is True
    assert manager.selected_session is selected
    assert manager.displays == {}
    assert all(event["kind"] == "sidebar_item" and event["project_id"] == project for event in events)
    assert events[-1]["item"] is None


def test_deleting_cold_session_removes_only_target_and_publishes_tombstone(tmp_path):
    manager, project, store, session, events = cold_workspace(tmp_path)
    selected = manager.selected_session
    asyncio.run(manager.delete_session(project, session.session_id))
    assert not (store.root / "sessions" / "cold.jsonl").exists()
    assert (store.root / "sessions" / "selected.jsonl").exists()
    assert manager.selected_session is selected
    assert manager.displays == {}
    assert events[-1]["item"] is None
    assert events[-1]["sessions"][0]["deleted"] is True


def test_opening_cold_history_reads_project_records_once_without_loading_runtime(tmp_path, monkeypatch):
    manager, project, store, session, events = cold_workspace(tmp_path)
    from unittest.mock import Mock
    records = Mock(wraps=manager.workspace.records)
    monkeypatch.setattr(manager.workspace, "records", records)
    state = asyncio.run(manager.navigate('activate', project_id=project, session_id=session.session_id))
    records.assert_called_once_with(project)
    assert state['session']['id'] == session.session_id
    assert state['execution_ready'] is False
    assert manager.conversations.sessions == {}
    assert manager.displays == {}
