"""Project conversation projections that never construct an execution runtime."""
from hashlib import sha256
from pathlib import Path

from raccoon.application.conversations.parallel_inference import parallel_replies_from_metadata
from raccoon.application.conversations.model_switching import model_options
from raccoon.application.delivery.artifacts import session_artifacts
from raccoon.modules.sessions.store import SessionStore, _store_lock
from raccoon.application.delivery.messages import display_messages
from raccoon.application.workspaces.sidebar import SidebarCatalog


class ReadOnlySessionStore(SessionStore):
    """为历史展示提供只读会话事实和单次读取缓存。"""
    def __init__(self, root):
        self.root = Path(root)
        self.sessions_dir = self.root / 'sessions'
        self._lock = _store_lock(self.root)
        self._events = {}

    def list_events(self, session_id):
        if session_id not in self._events:
            self._events[session_id] = super().list_events(session_id)
        return self._events[session_id]

    def append_event(self, event):
        raise TypeError('History projections cannot write session events')


def read_store(root):
    return ReadOnlySessionStore(root)


def session_snapshot(workbench, project_id, current, records, *,
                     scoped_artifacts, session_canvas, attachments_factory, event_context):
    catalog = workbench.sidebar.catalog(project_id)
    session_id = current.session_id
    store = read_store(catalog.root)
    view = store.load(session_id)
    events = store.list_events(session_id)
    family = SidebarCatalog.family(current, project_id)
    versions = []
    for record in records:
        if SidebarCatalog.family(record, project_id) != family or record.metadata.get('web_deleted'):
            continue
        revision = record.metadata.get('web_message_revision') or {}
        operation = revision.get('operation', 'original')
        number = revision.get('number', 1)
        versions.append({'session_id': record.session_id, 'operation': operation, 'number': number,
            'label': ('原始版本' if operation == 'original' else '编辑版本' if operation == 'edit' else '重新生成版本') + f' {number}'})
    versions.sort(key=lambda item: item['number'])
    for item in versions:
        item.pop('number')
    project = workbench.workspace.projects[project_id]
    artifacts = session_artifacts(events, session_id=session_id, project=Path(project['path']), reports=catalog.root / 'reports')
    pending = None
    sidebar = workbench.sidebar.snapshot(project_records=records, records_project_id=project_id, current_record=current)
    sidebar.update(current_conversation_id=family if SidebarCatalog.started(current) else None,
                   new_chat=not SidebarCatalog.started(current))
    for item in [*sidebar['recent'], *(item for project in sidebar['projects'] for item in project['conversations'])]:
        if item['project_id'] == project_id and item['id'] == family:
            item['session_id'] = session_id
    permission_mode = view.metadata.get('mode', 'ask')
    if permission_mode == 'standard':
        permission_mode = 'ask'
    elif permission_mode == 'aggressive':
        permission_mode = 'auto'
    elif permission_mode not in ('ask', 'auto', 'bypass'):
        permission_mode = 'ask'
    model = workbench.default_model(view.metadata.get('model_ref'))
    status = workbench.conversations.get_session_status(workbench.session_key(project_id, session_id))
    archived = SidebarCatalog.archive_flags(records, project_id)
    rows = [{'id': record.session_id, 'title': record.title, 'updated_at': record.updated_at,
             'conversation_id': SidebarCatalog.family(record, project_id), 'message_count': record.message_count,
             'status': record.status, 'pinned': bool(record.metadata.get('web_pinned')),
             'archived': archived[record.session_id], 'deleted': bool(record.metadata.get('web_deleted'))} for record in records]
    return {
        'navigation_id': None, 'project': project,
        'workspaces': [item for item in workbench.workspace.projects.values() if not item.get('removed')],
        'sidebar': sidebar,
        'session': {'id': session_id, 'title': workbench.sidebar.title(project_id, current)},
        'sessions': rows, 'messages': display_messages(
            view, attachments=attachments_factory(catalog.root, project_id, session_id), include_reasoning=True),
        'permission_mode': permission_mode,
        'model': model, 'models': model_options(workbench.model_catalog), 'effort': view.metadata.get('reasoning_effort'),
        'input_capabilities': workbench.message_model(model) if model is not None else {'images': False, 'pdf': False},
        **status,
        'parallel_replies': parallel_replies_from_metadata(view.metadata),
        'pending': pending,
        'live': [], 'activity': [],
        'artifacts': scoped_artifacts(artifacts, project_id, session_id),
        'canvas': session_canvas(events, artifacts),
        'history_revision': sha256(':'.join(event.id for event in events).encode()).hexdigest(),
        'revisable_message_ids': [event.payload['message_id'] for event in events
            if event.type == 'user_message' and not event.payload.get('metadata', {}).get('source_event_id')],
        'versions': {'current': session_id, 'items': versions},
        'execution_ready': False, 'event_seq': event_context['event_seq'],
        'event_epoch': event_context['event_epoch'],
    }
