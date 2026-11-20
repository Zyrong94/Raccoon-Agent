"""Explicit Web facade over conversation execution and workspace navigation."""
from __future__ import annotations


import asyncio

from dataclasses import replace

from raccoon.app.session_runtime import SessionRuntime
from raccoon.bootstrap.runtime import create_runtime
from raccoon.interfaces.web.chat_display import ChatDisplay
from raccoon.interfaces.web import conversation_actions as actions
from raccoon.application.workspaces.catalog import workspace_id
from raccoon.application.workspaces.store import WorkspaceStore, WorkspaceConflict
from raccoon.application.workspaces.sidebar import SidebarCatalog
from raccoon.application.workspaces.project_archives import ProjectArchives
from pathlib import Path

from raccoon.interfaces.web.history import read_store, session_snapshot
from raccoon.interfaces.web.event_stream import WebEventStream

WorkbenchConflict = WorkspaceConflict

class WebChatApplication:
    """适配网页请求和事件流，显式连接聊天执行与工作台导航。"""

    _workspace_id = staticmethod(workspace_id)

    def __init__(self, *, project_path, data_root, session_id, app_config, model_ref=None,
                 runtime: SessionRuntime | None = None, runtime_factory=create_runtime):
        self.loop = None
        self.scheduling = None
        from raccoon.app.conversations import ConversationService
        self.conversations = ConversationService()
        self.events = WebEventStream()
        self.runtime_factory = runtime_factory
        self.configuration_runtime = runtime
        self.app_config = app_config
        self._model_catalog = app_config.model_catalog()
        self.startup_model_ref = model_ref
        self.workspace = WorkspaceStore(project_path, data_root)
        identity = workspace_id(project_path)
        self.selected_session = (identity, session_id)
        self.drafts = {}
        self.displays = {}
        self._project_sessions = {}
        self.sidebar_selection = None
        self.navigation_id = None
        self.sidebar = SidebarCatalog(self)
        self.project_archives = ProjectArchives(self, sidebar=self.sidebar)
        if runtime is not None:
            self.displays[self.selected_session] = self._create_display(runtime)
        if self.sidebar.is_new():
            self.drafts[identity] = session_id


    def _create_display(self, runtime):
        display = ChatDisplay(runtime, self)
        display.parallel = actions.create_parallel_runner(display)
        actions.install_scheduling_tools(display)
        actions.install_canvas_tool(display)
        self.conversations.register(runtime, on_start=display._run_started, on_finish=display._run_finished,
            classifier=lambda event, snapshot: actions.classify_message(display, event, snapshot),
            parallel_executor=display.parallel.execute)
        return display

    def _event_context(self, owner):
        return {'event_seq': self.events.event_seq, 'event_epoch': self.events.event_epoch,
                'navigation_id': self.navigation_id if self.selected_session == (workspace_id(owner.runtime.project_path), owner.runtime.session.session_id) else None}

    def publish_from(self, owner, event):
        identity = self._workspace_id(owner.runtime.project_path)
        self.publish_session_event(identity, owner.runtime.session.session_id, event)

    def publish_session_event(self, project_id, session_id, event):
        self.events.publish({**event, 'project_id': project_id, 'session_id': session_id})

    def session_key(self, project_id, session_id):
        from raccoon.app.conversations import SessionKey
        return SessionKey(self.workspace.catalog(project_id).root.resolve(), session_id)

    def publish_run_state(self, project_id, session_id):
        display = self.displays.get((project_id, session_id))
        if display is not None:
            display.publish_run_state()
            return
        state = self.conversations.get_session_status(self.session_key(project_id, session_id))
        self.publish_session_event(project_id, session_id, {
            'kind': 'run_state', 'state': state, 'settled': not state['busy'],
        })
        record = self.workspace.resolve_session(project_id, session_id)
        if self.sidebar.started(record):
            self.sidebar_item_update(project_id, self.sidebar.family(record, project_id))

    @property
    def model_catalog(self):
        runtime = self.configuration_runtime
        return runtime.model_catalog if runtime is not None else self._model_catalog

    def update_model_catalog(self, model_config):
        self.app_config = replace(self.app_config, model_config=model_config)
        self._model_catalog = self.app_config.model_catalog()

    def default_model(self, saved_model=None):
        catalog = self.model_catalog
        for ref in (saved_model, self.startup_model_ref, catalog.default_ref):
            if ref in catalog.models:
                return ref
        runtime = self.configuration_runtime
        if runtime is not None:
            return runtime.model_ref
        return next(iter(catalog.models), None)

    def message_model(self, model_ref):
        runtime = self.configuration_runtime
        profile = self.model_catalog.models.get(model_ref)
        if profile is not None:
            return {'images': profile.vision, 'pdf': profile.pdf_input}
        if runtime is not None and model_ref == runtime.model_ref:
            capabilities = runtime.provider.capabilities
            return {'images': capabilities.supports_vision, 'pdf': capabilities.supports_pdf_input}
        raise ValueError(f'未配置模型：{model_ref}')


    async def close(self):
        await self.conversations.close()
        self.displays.clear()
        self._project_sessions.clear()
        self.events.close()

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.conversations.start()

    def require_unoccupied(self, project_id, session_ids=None):
        root = self.workspace.catalog(project_id).root.resolve()
        keys = set(self.conversations.sessions) | set(self.conversations.preparing_sessions)
        for key in keys:
            if key.root == root and (session_ids is None or key.session_id in session_ids):
                self.conversations.require_session_idle(key)


    def require_active_project(self, project_id):
        item = self.workspace.projects.get(project_id)
        if item is None or item.get('removed'):
            raise WorkspaceConflict('项目不存在或已移除')
        if item.get('archived'):
            raise WorkspaceConflict('此项目已归档，请先恢复项目')


    def update_workspace(self, identity, *, archived):
        if self.workspace.projects.get(identity, {}).get('projectless'):
            raise WorkspaceConflict('无项目聊天的存储空间不能归档')
        if archived:
            self.require_unoccupied(identity)
        if identity not in self.workspace.projects or self.workspace.projects[identity].get('removed'):
            raise ValueError('项目不存在或已移除，请重新打开项目')
        self.workspace.save_projects({**self.workspace.projects, identity: {**self.workspace.projects[identity], 'archived': archived}})
        self.events.publish({'kind': 'snapshot', 'state': self.selected_snapshot()})


    async def remove_workspace(self, identity):
        if self.workspace.projects.get(identity, {}).get('projectless'):
            raise WorkspaceConflict('无项目聊天的存储空间不能移除')
        self.require_unoccupied(identity)
        if identity not in self.workspace.projects:
            raise ValueError('项目不存在')
        if self.selected_session[0] == identity:
            await self.navigate('sidebar_new')
        # Keep a tombstone so startup cannot silently register the default directory again.
        self.workspace.save_projects({**self.workspace.projects, identity: {**self.workspace.projects[identity], 'removed': True}})
        self._project_sessions.pop(identity, None)
        self.events.publish({'kind': 'snapshot', 'state': self.selected_snapshot()})


    def restore_archived_chat(self, storage_project_id, conversation_id):
        self.project_archives.restore(storage_project_id, conversation_id)
        self._project_sessions.pop(storage_project_id, None)
        return self.events.publish({'kind': 'snapshot', 'state': self.selected_snapshot()})


    def _projectless_id(self):
        root = self.workspace.registry_path.parent / 'web-personal'
        path = (root / 'workspace').resolve()
        identity = self._workspace_id(path)
        path.mkdir(parents=True, exist_ok=True)
        item = {'id': identity, 'name': '无项目', 'path': str(path),
                'data_root': str((root / 'data').resolve()), 'projectless': True,
                'archived': False, 'removed': False}
        if self.workspace.projects.get(identity) != item:
            self.workspace.save_projects({**self.workspace.projects, identity: item})
        return identity


    def _runtime_options(self, identity, path, session_id):
        root = self.workspace.catalog(identity).root
        options = {'project_root': path, 'data_root': root, 'session_id': session_id, 'resume_session': True}
        configured = self.configuration_runtime
        if self.workspace.projects[identity].get('projectless') and self.runtime_factory is create_runtime:
            options['app_config'] = self.app_config
        if (configured is not None and self.runtime_factory is create_runtime
                and configured.model_ref not in configured.model_catalog.models):
            options.update(provider=configured.provider, app_config=configured.app_config,
                           tools=list(configured.tools or []), runtime_capabilities=configured.runtime_capabilities)
        return options


    def sidebar_update(self, project_id, conversation_id, changes):
        self.require_active_project(project_id)
        family, records = self.sidebar.members(project_id, conversation_id)
        if changes.get('archived') or changes.get('deleted'):
            self.require_unoccupied(project_id, {record.session_id for record in records})
        metadata = {}
        if 'title' in changes:
            title = changes['title'].strip()
            if not title:
                raise ValueError('会话名称不能为空')
            metadata['title'] = title
        for flag in ('pinned', 'archived', 'deleted'):
            if flag in changes:
                metadata['web_' + flag] = changes[flag]
        self.workspace.update_records(project_id, records, metadata)
        return self.sidebar_item_update(project_id, family)


    def sidebar_item_update(self, project_id, family, *, deleted_record=None):
        """Publish a project-owned row without loading a chat runtime or its history."""
        records = self.sidebar.groups(project_id).get(family, [])
        visible_records = [record for record in records if not record.metadata.get("web_deleted")]
        summary = self.sidebar.summary(project_id, family, visible_records) if visible_records else None
        if summary and summary['archived'] and not summary['busy'] and not summary['pending']:
            summary = None
        archived = SidebarCatalog.archive_flags(records, project_id)
        rows = []
        if deleted_record is not None:
            records.append(deleted_record)
        for record in records:
            rows.append({"id": record.session_id, "title": record.title, "updated_at": record.updated_at,
                         "conversation_id": SidebarCatalog.family(record, project_id),
                         "message_count": record.message_count, "status": record.status,
                         "pinned": bool(record.metadata.get("web_pinned")),
                         "archived": archived.get(record.session_id, False),
                         "deleted": record is deleted_record or bool(record.metadata.get("web_deleted"))})
        self._project_sessions.pop(project_id, None)
        return self.events.publish({'kind': 'sidebar_item', 'project_id': project_id,
                                    'conversation_id': family, 'item': summary, 'sessions': rows})


    async def delete_session(self, project_id, session_id):
        import portalocker
        from raccoon.runtime.session_locks import session_lock
        self.require_active_project(project_id)
        record = self.workspace.resolve_session(project_id, session_id)
        self.require_unoccupied(project_id, {session_id})
        lease = session_lock(self.workspace.catalog(project_id).root, session_id)
        try:
            lease.acquire()
        except portalocker.exceptions.LockException as error:
            raise WorkspaceConflict('会话仍被其他执行器占用') from error
        try:
            await self.discard(project_id, session_id)
            self.workspace.delete_session_files(project_id, session_id)
            event = self.sidebar_item_update(project_id, SidebarCatalog.family(record, project_id), deleted_record=record)
        finally:
            lease.release()
        if self.selected_session == (project_id, session_id):
            await self.navigate('activate_workspace', project_id=project_id)
        return event


    async def navigate(self, operation, **options):
        from raccoon.modules.sessions.store import SessionStore
        identity = options.get('project_id') or options.get('identity')
        if identity is None:
            identity = self._projectless_id() if operation == 'sidebar_new' else self.selected_session[0]
        if operation == 'sidebar_new' and options.get('draft_only') and not self.sidebar.is_new():
            raise WorkspaceConflict('已开始的会话不能更换项目，请新建聊天')
        if operation == 'add_workspace':
            path = Path(options['directory']).expanduser()
            if not path.is_absolute() or not path.is_dir():
                raise ValueError('请输入存在的项目绝对目录')
            path = path.resolve()
            identity = workspace_id(path)
            item = {'id': identity, 'name': path.name, 'path': str(path), 'archived': False, 'removed': False}
            self.workspace.save_projects({**self.workspace.projects, identity: {**self.workspace.projects.get(identity, {}), **item}})
        self.require_active_project(identity)
        session_id = options.get('session_id')
        new_chat = operation == 'sidebar_new' or options.get('new_chat', False)
        records = self.workspace.records(identity)
        if new_chat:
            session_id = self.drafts.get(identity)
            if not any(record.session_id == session_id and not SidebarCatalog.started(record) for record in records):
                session_id = None
        elif session_id is None:
            archived = SidebarCatalog.archive_flags(records, identity)
            session_id = next((record.session_id for record in records
                               if not archived[record.session_id] and not record.metadata.get('web_deleted')), None)
        if session_id is None:
            store = SessionStore(self.workspace.catalog(identity).root)
            from raccoon.modules.sessions.identity import new_session_id
            from raccoon.modules.sessions.thread_data import default_thread_data
            session_id = new_session_id()
            data = store.create(session_id=session_id, thread_data=default_thread_data(
                store_root=store.root, session_id=session_id, project_root=Path(self.workspace.projects[identity]['path'])))
            session_id = data.session_id
            self.drafts[identity] = session_id
            self._project_sessions.pop(identity, None)
            records = self.workspace.records(identity)
        record = self.workspace.resolve_session(identity, session_id, records=records)
        self.sidebar.remember(identity, record)
        self.selected_session = (identity, session_id)
        self.navigation_id = options.get('navigation_id')
        state = self._snapshot_for_record(identity, record, records)
        state['navigation_id'] = self.navigation_id
        self.events.publish({'kind': 'snapshot', 'state': state, 'project_id': identity, 'session_id': session_id})
        return state


    async def discard(self, project_id, session_id):
        from raccoon.app.conversations import SessionKey
        await self.conversations.close_session(SessionKey(self.workspace.catalog(project_id).root.resolve(), session_id))
        display = self.displays.pop((project_id, session_id), None)
        if display is not None:
            display.runtime.session.store.on_message_committed = None


    def resolve_session(self, project_id, *, session_id=None, conversation_id=None, records=None):
        if session_id is not None:
            self.workspace.resolve_session(project_id, session_id, records=records)
            return session_id
        family, records = self.sidebar.members(project_id, conversation_id, records=records)
        records = [record for record in records if not record.metadata.get('web_deleted')]
        if not records:
            raise ValueError('会话不可用')
        return self.sidebar.chosen(project_id, family, records)


    def read_store(self, project_id, session_id):
        self.workspace.resolve_session(project_id, session_id)
        return read_store(self.workspace.catalog(project_id).root)


    def session_view(self, project_id, *, session_id=None, conversation_id=None):
        records = self.workspace.records(project_id)
        if session_id is None:
            session_id = self.resolve_session(project_id, conversation_id=conversation_id, records=records)
        record = self.workspace.resolve_session(project_id, session_id, records=records)
        return self._snapshot_for_record(project_id, record, records)

    def _snapshot_for_record(self, project_id, record, records):
        display = self.displays.get((project_id, record.session_id))
        if display is not None:
            return display.snapshot()
        return session_snapshot(self, project_id, record, records,
                                event_context={'event_seq': self.events.event_seq, 'event_epoch': self.events.event_epoch})

    def selected_snapshot(self):
        project_id, session_id = self.selected_session
        return self.session_view(project_id, session_id=session_id)


    async def prepare_session(self, project_id, session_id, *, model_ref=None):
        record = self.workspace.resolve_session(project_id, session_id)
        self.require_active_project(project_id)
        options = self._runtime_options(project_id, Path(self.workspace.projects[project_id]['path']), session_id)
        selected_model = model_ref or self.default_model(record.metadata.get('model_ref'))
        if selected_model in self.model_catalog.models:
            options['model_spec'] = selected_model
        runtime = await self.conversations.get_or_create_session(
            self.runtime_factory, **options)
        key = (project_id, session_id)
        if key not in self.displays:
            self._project_sessions.pop(project_id, None)
            display = self.displays[key] = self._create_display(runtime)
            display.publish({'kind': 'snapshot', 'state': display.snapshot()})
        return self.displays[key]


    def project_sessions(self, identity, *, refresh):
        if not refresh and identity in self._project_sessions:
            return self._project_sessions[identity]
        catalog = self.workspace.catalog(identity)
        records = SidebarCatalog.catalog_records(catalog)
        archived = SidebarCatalog.archive_flags(records, identity)
        main_ids = SidebarCatalog.channel_sessions(catalog.root)
        rows = [
            {"id": record.session_id, "title": record.title, "updated_at": record.updated_at,
             "conversation_id": SidebarCatalog.family(record, identity),
             "message_count": record.message_count, "status": record.status,
             "pinned": bool(record.metadata.get("web_pinned")),
             "archived": archived[record.session_id],
             "deleted": bool(record.metadata.get("web_deleted"))}
            for record in records if record.session_id not in main_ids
            and SidebarCatalog.valid_revision(record, identity)]
        self._project_sessions[identity] = rows
        return rows


    def navigation_snapshot(self, source, *, refresh):
        runtime = source.runtime
        identity = self._workspace_id(runtime.project_path)
        current = source.sidebar.current_record()
        title = source.sidebar.title(identity, current)
        sessions = [{**item, "title": title} if item["id"] == current.session_id else item
                    for item in self.project_sessions(identity, refresh=refresh)]
        return {"project": self.workspace.projects[identity],
                "workspaces": [item for item in self.workspace.projects.values() if not item.get('removed')],
                "sidebar": source.sidebar.snapshot(),
                "session": {"id": runtime.session.session_id, "title": title},
                "sessions": sessions}
