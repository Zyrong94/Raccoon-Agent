"""Read-only archive projection and explicit, project-scoped restore routes."""
from pathlib import Path

from raccoon.modules.sessions.catalog import record_from_path, is_safe_session_id
from raccoon.application.workspaces.catalog import workspace_id


class ProjectArchives:
    """投影已归档项目与聊天，并执行显式恢复。"""

    def __init__(self, manager, *, sidebar):
        self.manager = manager
        self.sidebar = sidebar

    def groups(self, storage_id):
        manager = self.manager
        item = manager.workspace.projects.get(storage_id)
        if not item or item.get('removed'):
            raise ValueError('存储项目不存在或已移除')
        catalog = self.sidebar.catalog(storage_id)
        main_ids = self.sidebar.channel_sessions(catalog.root)
        groups = {}
        for path in catalog.sessions_dir.glob('*.jsonl'):
            record = record_from_path(path)
            if record.status != 'ok' or record.session_id in main_ids or self.sidebar.is_child(record):
                continue
            thread_data = record.metadata.get('thread_data')
            owner = thread_data.get('workspace_path') if isinstance(thread_data, dict) else None
            project_id = workspace_id(Path(owner)) if owner else None
            if item.get('projectless'):
                project_id = None
            # Explicit ownership never falls through to the legacy, unassigned bucket.
            if project_id is not None and (project_id not in manager.workspace.projects
                    or manager.workspace.projects[project_id].get('removed')):
                continue
            revision_owner = project_id or storage_id
            if not self.sidebar.valid_revision(record, revision_owner):
                continue
            family = self.sidebar.family(record, revision_owner)
            if family not in main_ids:
                groups.setdefault((project_id, family), []).append(record)
        return groups

    def snapshot(self):
        manager = self.manager
        projects = {}
        for identity, item in manager.workspace.projects.items():
            if item.get('removed') or item.get('projectless'):
                continue
            available = Path(item['path']).is_dir()
            projects[identity] = {key: item[key] for key in ('id', 'name', 'path')}
            projects[identity].update(archived=bool(item.get('archived')), available=available, conversations=[])
            if not available:
                projects[identity]['error'] = '项目目录不存在或无法访问'
        unassigned, seen_roots = [], set()
        for storage_id, item in manager.workspace.projects.items():
            if item.get('removed'):
                continue
            root = self.sidebar.catalog(storage_id).root.resolve()
            if root in seen_roots:
                continue
            seen_roots.add(root)
            try:
                for (project_id, family), records in self.groups(storage_id).items():
                    if (not any(self.sidebar.started(record) for record in records)
                            or any(record.metadata.get('web_deleted') for record in records)):
                        continue
                    whole_project = project_id is not None and projects[project_id]['archived']
                    if not whole_project and not any(record.metadata.get('web_archived') for record in records):
                        continue
                    representative = next((record for record in records if record.session_id == family), records[0])
                    summary = {'id': family, 'session_id': self.sidebar.chosen(project_id or storage_id, family, records),
                        'title': representative.title, 'updated_at': max(record.updated_at or '' for record in records),
                        'storage_project_id': storage_id, 'project_id': project_id}
                    if project_id is None:
                        unassigned.append(summary)
                    else:
                        projects[project_id]['conversations'].append(summary)
            except (OSError, ValueError, TypeError) as error:
                if storage_id in projects:
                    projects[storage_id].update(available=False, error=str(error))
        for project in projects.values():
            project['conversations'].sort(key=lambda item: (item['updated_at'], item['id']), reverse=True)
        unassigned.sort(key=lambda item: (item['updated_at'], item['id']), reverse=True)
        return {'projects': [item for item in projects.values() if item['archived'] or item['conversations']],
                'unassigned': unassigned}

    def restore(self, storage_id, conversation_id):
        if not is_safe_session_id(conversation_id):
            raise ValueError('无效会话ID')
        matches = [(project_id, records) for (project_id, family), records in self.groups(storage_id).items()
                   if family == conversation_id]
        if len(matches) != 1:
            raise ValueError('会话不属于此存储项目或不可用')
        project_id, records = matches[0]
        if project_id and self.manager.workspace.projects[project_id].get('archived'):
            raise ValueError('请先恢复整个项目，再恢复其中的聊天')
        if any(record.metadata.get('web_deleted') for record in records):
            raise ValueError('已删除的聊天不能从归档恢复')
        if (not any(self.sidebar.started(record) for record in records)
                or not any(record.metadata.get('web_archived') for record in records)):
            raise ValueError('此聊天尚未归档')
        from raccoon.modules.sessions.store import SessionStore
        store = SessionStore(self.sidebar.catalog(storage_id).root)
        def save(record, archived):
            try:
                store.append_session_metadata_updated(store.load(record.session_id), web_archived=archived)
            except Exception as exc:
                # A write can fail on close; check whether the requested flag reached the log.
                saved = record_from_path(store.sessions_dir / (record.session_id + '.jsonl'))
                if saved.status != 'ok' or saved.metadata.get('web_archived') is not archived:
                    raise ValueError(f'无法恢复聊天，请同步后重试：{exc}') from exc
        authority = next((record for record in records if record.session_id == conversation_id),
                         min(records, key=lambda record: record.session_id))
        # Keep the logical family archived until its final log append commits, including after interrupted retries.
        if authority.metadata.get('web_archived') is not True:
            save(authority, True)
        for record in records:
            if record is not authority:
                save(record, False)
        save(authority, False)
