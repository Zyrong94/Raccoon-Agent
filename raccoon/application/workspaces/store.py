"""Project registry and session records. No Agent runtimes or Web selection."""
from pathlib import Path
import json
import os

from raccoon.modules.sessions.catalog import SessionCatalog, is_safe_session_id, session_sort_key
from raccoon.modules.sessions.store import SessionStore
from raccoon.application.workspaces.catalog import catalog_record, workspace_id
from raccoon.application.workspaces.sidebar import SidebarCatalog


class WorkspaceConflict(ValueError):
    """The requested project or session operation is unavailable."""


class WorkspaceStore:
    _workspace_id = staticmethod(workspace_id)

    def __init__(self, project_path, data_root):
        self.registry_path = data_root / 'web_workspaces.json'
        self.projects = {}
        try:
            for item in json.loads(self.registry_path.read_text(encoding='utf-8')):
                path = Path(item['path']).resolve()
                identity = self._workspace_id(path)
                self.projects[identity] = {**item, 'id': identity,
                    'name': '无项目' if item.get('projectless') else path.name, 'path': str(path)}
        except (OSError, ValueError, KeyError, TypeError):
            self.projects = {}
        identity = self._workspace_id(project_path)
        self.projects[identity] = {
            'archived': False, 'removed': False, **self.projects.get(identity, {}),
            'id': identity, 'name': '无项目' if self.projects.get(identity, {}).get('projectless') else project_path.resolve().name,
            'path': str(project_path.resolve()), 'data_root': str(data_root.resolve()),
        }

    def save_projects(self, entries):
        temporary = self.registry_path.with_suffix('.tmp')
        try:
            self.registry_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(list(entries.values()), ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.registry_path)
        except OSError as exc:
            raise ValueError(f'无法保存工作区：{exc}') from exc
        self.projects = entries


    def catalog(self, project_id):
        item = self.projects.get(project_id)
        if item is None or item.get('removed'):
            raise ValueError('项目不存在或已移除')
        return SessionCatalog(item.get('data_root') or Path(item['path']) / '.raccoon')

    def resolve_session(self, project_id, session_id, *, records=None):
        if not is_safe_session_id(session_id):
            raise ValueError('无效会话ID')
        if records is None:
            records = self.records(project_id)
        record = next((record for record in records if record.session_id == session_id), None)
        if record is None or record.metadata.get('web_deleted'):
            raise WorkspaceConflict('会话不属于此项目或不可用')
        return record

    def update_records(self, project_id, records, metadata):
        if any(record.metadata.get('web_deleted') for record in records):
            raise WorkspaceConflict('会话已删除')
        store = SessionStore(self.catalog(project_id).root)
        for record in records:
            store.append_session_metadata_updated(store.load(record.session_id), **metadata)

    def delete_session_files(self, project_id, session_id):
        from raccoon.modules.sessions.deletion import delete_session_data
        delete_session_data(self.catalog(project_id).root, session_id)

    def records(self, project_id):
        """Read records directly from the project's session logs."""
        catalog = self.catalog(project_id)
        item = self.projects[project_id]
        project = Path(item['path'])
        if not project.is_dir():
            raise ValueError('项目目录不存在或无法访问')
        main_ids = SidebarCatalog.channel_sessions(catalog.root)
        project_path = os.path.normcase(str(project.resolve()))
        owners = {}
        records = []
        for path in catalog.sessions_dir.glob('*.jsonl'):
            record = catalog_record(path)
            if record.status != 'ok' or record.session_id in main_ids or SidebarCatalog.is_child(record):
                continue
            metadata = record.metadata
            thread_data = metadata.get('thread_data')
            workspace_path = thread_data.get('workspace_path') if isinstance(thread_data, dict) else None
            if workspace_path:
                if workspace_path not in owners:
                    owners[workspace_path] = os.path.normcase(str(Path(workspace_path).resolve()))
                if owners[workspace_path] != project_path:
                    continue
            revision = metadata.get('web_message_revision')
            if not SidebarCatalog.valid_revision(record, project_id):
                continue
            if revision and revision['family'] in main_ids:
                continue
            records.append(record)
        return sorted(records, key=session_sort_key, reverse=True)
