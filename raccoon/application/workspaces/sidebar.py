"""Read-only project catalogs and durable logical-conversation navigation."""
from __future__ import annotations

import json

from raccoon.modules.sessions.catalog import is_safe_session_id, session_sort_key
from raccoon.application.workspaces.catalog import catalog_record, main_session_id, workspace_id
from raccoon.utils.text import optional_str


class SidebarCatalog:
    """读取项目聊天目录并保存逻辑会话的选中版本。"""

    @property
    def selected(self):
        return self.manager.sidebar_selection or {}

    @selected.setter
    def selected(self, value):
        self.manager.sidebar_selection = value

    def __init__(self, manager, *, runtime=None):
        self.manager = manager
        self._runtime = runtime
        self.path = manager.workspace.registry_path.with_name('web_sidebar.json')
        if self.manager.sidebar_selection is not None:
            return
        self.selected = {}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                self.selected = {key: value for key, value in data.items() if isinstance(value, dict)}
        except (OSError, ValueError):
            pass

    def catalog(self, project_id):
        return self.manager.workspace.catalog(project_id)

    @classmethod
    def catalog_records(cls, catalog):
        records = (catalog_record(path) for path in catalog.sessions_dir.glob('*.jsonl'))
        return sorted((record for record in records if not cls.is_child(record)),
                      key=session_sort_key, reverse=True)

    @staticmethod
    def is_child(record):
        return 'parent_session_id' in record.metadata

    @classmethod
    def archive_flags(cls, records, project_id):
        groups = {}
        for record in records:
            groups.setdefault(cls.family(record, project_id), []).append(record)
        return {record.session_id: any(bool(member.metadata.get('web_archived')) for member in members)
                for members in groups.values() for record in members}

    def records(self, project_id):
        return self.manager.workspace.records(project_id)

    @staticmethod
    def channel_sessions(root):
        identities = {path.name.removesuffix('.events.sqlite3')
                      for path in (root / 'channels').glob('*.events.sqlite3')}
        try:
            identity = main_session_id(root)
            if identity:
                identities.add(identity)
        except (OSError, ValueError):
            pass
        return identities

    @staticmethod
    def valid_revision(record, project_id):
        revision = record.metadata.get('web_message_revision')
        return not revision or (isinstance(revision, dict) and revision.get('state') == 'accepted'
            and revision.get('workspace_id') == project_id and isinstance(revision.get('family'), str)
            and is_safe_session_id(revision['family']))

    @staticmethod
    def family(record, project_id):
        revision = record.metadata.get('web_message_revision')
        if (isinstance(revision, dict) and revision.get('state') == 'accepted'
                and revision.get('workspace_id') == project_id
                and isinstance(revision.get('family'), str) and is_safe_session_id(revision['family'])):
            return revision['family']
        return record.session_id

    @staticmethod
    def started(record):
        revision = record.metadata.get('web_message_revision')
        return bool(record.user_turn_count or record.metadata.get('web_chat_started')
                    or isinstance(revision, dict) and revision.get('state') == 'accepted')

    def is_new(self):
        return not self.started(self.current_record())

    def current_identity(self):
        if self._runtime is None:
            return self.manager.selected_session
        runtime = self._runtime()
        return workspace_id(runtime.project_path), runtime.session.session_id

    def current_record(self):
        project_id, session_id = self.current_identity()
        return self.manager.workspace.resolve_session(project_id, session_id)

    def groups(self, project_id, records=None):
        if records is None:
            records = self.records(project_id)
        groups = {}
        for record in records:
            groups.setdefault(self.family(record, project_id), []).append(record)
        return groups

    def members(self, project_id, conversation_id, *, records=None):
        if not is_safe_session_id(conversation_id):
            raise ValueError('无效会话ID')
        groups = self.groups(project_id, records)
        # Legacy APIs still address a real version ID.
        family = next((key for key, records in groups.items()
                       if any(record.session_id == conversation_id for record in records)), conversation_id)
        if family not in groups:
            raise ValueError('会话不属于此项目或不可用')
        return family, groups[family]

    def chosen(self, project_id, family, records):
        current_project, current = self.current_identity()
        if (project_id == current_project
                and any(record.session_id == current for record in records)):
            return current
        saved = self.selected.get(project_id, {}).get(family)
        if any(record.session_id == saved for record in records):
            return saved
        return max(records, key=lambda record: (record.updated_at or '',
            record.metadata.get('web_message_revision', {}).get('number', 2)
            if record.metadata.get('web_message_revision') else 1, record.session_id)).session_id

    def remember_current(self):
        project_id, _ = self.current_identity()
        self.remember(project_id, self.current_record())

    def remember(self, project_id, record):
        if not self.started(record):
            return
        family = self.family(record, project_id)
        if self.selected.get(project_id, {}).get(family) == record.session_id:
            return
        selected = {**self.selected, project_id: {**self.selected.get(project_id, {}), family: record.session_id}}
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(selected, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)
        self.selected = selected

    def summary(self, project_id, family, records):
        """One row of the sidebar, shared by full snapshots and single-item updates."""
        selected = self.chosen(project_id, family, records)
        representative = next((record for record in records if record.session_id == family), records[0])
        owners = [self.manager.displays.get((project_id, record.session_id)) for record in records]
        return {'id': family, 'project_id': project_id, 'session_id': selected,
                'title': self.title(project_id, representative),
                'updated_at': max(record.updated_at or '' for record in records),
                'pinned': any(bool(record.metadata.get('web_pinned')) for record in records),
                'archived': any(bool(record.metadata.get('web_archived')) for record in records),
                'busy': any(owner is not None and owner.busy for owner in owners),
                'pending': any(owner is not None and owner.runtime.last_pending_input is not None for owner in owners)}

    def title(self, project_id, record):
        owner = self.manager.displays.get((project_id, record.session_id))
        pending = owner._pending_title if owner is not None and owner.busy else None
        return optional_str(record.metadata.get('title')) or pending or (
            '新会话' if record.title == record.session_id else record.title)

    def snapshot(self, *, project_records=None, records_project_id=None, current_record=None):
        manager = self.manager
        current_project, current_session = self.current_identity()
        projects, recent = [], []
        current_family = None
        for project_id, item in manager.workspace.projects.items():
            if item.get('archived') or item.get('removed'):
                continue
            project = {key: item[key] for key in ('id', 'name', 'path')}
            if item.get('projectless'):
                project['projectless'] = True
            project.update(available=True, conversations=[])
            try:
                groups = self.groups(project_id, project_records if project_id == records_project_id else None)
                for family, records in groups.items():
                    if not any(self.started(record) for record in records):
                        continue
                    if any(record.metadata.get('web_deleted') for record in records):
                        continue
                    active = project_id == current_project and any(record.session_id == current_session for record in records)
                    if active:
                        current_family = family
                    summary = self.summary(project_id, family, records)
                    if summary['archived'] and not summary['busy'] and not summary['pending']:
                        continue
                    project['conversations'].append(summary)
                project['conversations'].sort(key=lambda item: (item['pinned'], item['updated_at'], item['id']), reverse=True)
                recent.extend(project['conversations'])
            except (OSError, ValueError, TypeError) as error:
                project.update(available=False, error=str(error))
            projects.append(project)
        recent.sort(key=lambda item: (item['updated_at'], item['project_id'], item['id']), reverse=True)
        return {'projects': projects, 'recent': recent, 'current_conversation_id': current_family,
                'new_chat': not self.started(current_record) if current_record is not None else self.is_new()}
