"""Append-only project message versions over the existing session fork and runner."""
from hashlib import sha256
from raccoon.interfaces.web import conversation_actions as actions
import json
import os
from pathlib import Path

from raccoon.modules.sessions.user_attachments import resolve_queued_attachments
from raccoon.modules.sessions.fork import _user_attachment_ref
from raccoon.application.workspaces.catalog import catalog_record, file_version
from raccoon.application.workspaces.sidebar import SidebarCatalog


class MessageRevisionConflict(ValueError):
    pass


def switch_message_version(web, project_id, session_id, history_revision, target_session_id):
    web.require_active_project(project_id)
    display = web.displays.get((project_id, session_id))
    if display is not None:
        actions.require_session_change(display)
    state = web.session_view(project_id, session_id=session_id)
    if state['busy'] or state['pending'] or state['history_revision'] != history_revision:
        raise MessageRevisionConflict('会话或历史已更新，请重新同步后操作')
    if target_session_id not in {item['session_id'] for item in state['versions']['items']}:
        raise MessageRevisionConflict('该版本不属于当前会话')
    return web.session_view(project_id, session_id=target_session_id)


class MessageRevisions:
    """协调消息编辑、重新生成与历史版本切换。"""
    def __init__(self, display, *, attachments_factory):
        self.display = display
        self._attachments_factory = attachments_factory

    @property
    def runtime(self):
        return self.display.runtime

    @property
    def workspace_id(self):
        return self.display._workspace_id(self.runtime.project_path)

    def events(self):
        session = self.runtime.session
        return session.store.list_events(session.session_id)

    def _history_summary(self):
        session = self.runtime.session
        path = session.store.sessions_dir / f'{session.session_id}.jsonl'
        version = (str(path), file_version(path))
        if getattr(self, '_events_version', None) != version:
            events = self.events()
            self._history_revision = sha256(':'.join(event.id for event in events).encode()).hexdigest()
            self._revisable_ids = [event.payload['message_id'] for event in events if self.revisable(event)]
            self._events_version = version
        return self._history_revision, self._revisable_ids

    def revision(self):
        return self._history_summary()[0]

    @staticmethod
    def revisable(event):
        source = event.payload.get('metadata', {}).get('source_event_id', '')
        return event.type == 'user_message' and (not source or source.startswith('web-message:'))

    def snapshot(self):
        current = self.runtime.session.session_id
        record = catalog_record(self.runtime.catalog.sessions_dir / f'{current}.jsonl')
        workspace_id = self.workspace_id
        details = record.metadata.get('web_message_revision', {})
        family = details.get('family', current) if details.get('workspace_id') == workspace_id else current
        items = []
        project_path = os.path.normcase(str(self.runtime.project_path.resolve()))
        owners = {}
        for item in SidebarCatalog.catalog_records(self.runtime.catalog):
            data = item.metadata.get('web_message_revision', {})
            owner = item.metadata.get('thread_data', {}).get('workspace_path')
            if owner and owner not in owners:
                owners[owner] = os.path.normcase(str(Path(owner).resolve()))
            if (item.status != 'ok' or item.metadata.get('web_deleted') or not owner
                    or owners[owner] != project_path):
                continue
            if item.session_id == family:
                items.append({'session_id': item.session_id, 'label': '原始版本 1', 'operation': 'original', 'number': 1})
            elif (data.get('workspace_id') == workspace_id and data.get('family') == family
                    and data.get('state') == 'accepted'):
                operation = data.get('operation')
                if operation in {'edit', 'regenerate'}:
                    items.append({'session_id': item.session_id,
                        'label': ('编辑版本' if operation == 'edit' else '重新生成版本') + f" {data.get('number', 2)}",
                        'operation': operation, 'number': data.get('number', 2)})
        items.sort(key=lambda item: item['number'])
        for item in items:
            item.pop('number')
        return {'history_revision': self.revision(),
                'revisable_message_ids': list(self._history_summary()[1]),
                'versions': {'current': current, 'items': items}}

    def _belongs_to_workspace(self, record):
        path = record.metadata.get('thread_data', {}).get('workspace_path')
        return bool(path) and os.path.normcase(str(Path(path).resolve())) == os.path.normcase(str(self.runtime.project_path.resolve()))

    def guard(self, body):
        self.display.web.require_active_project(self.workspace_id)
        actions.require_session_change(self.display)
        if (body['project_id'] != self.workspace_id
                or body['session_id'] != self.runtime.session.session_id
                or body['history_revision'] != self.revision()):
            raise MessageRevisionConflict('会话或历史已更新，请重新同步后操作')
        record = self.runtime.catalog.get_session(body['session_id'])
        if not self._belongs_to_workspace(record):
            raise MessageRevisionConflict('此会话不属于当前工作区')

    async def submit(self, operation, body):
        self.display.web.require_active_project(self.workspace_id)
        canonical = {'operation': operation, **body}
        fingerprint = sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        # Consult the durable receipt before active-session/revision checks: accepting
        # this very request already changed the active session.
        for record in self.runtime.catalog.list_sessions():
            receipt = record.metadata.get('web_message_revision', {})
            if receipt.get('workspace_id') == self.workspace_id and receipt.get('request_id') == body['request_id']:
                if receipt.get('fingerprint') != fingerprint:
                    raise MessageRevisionConflict('该请求标识已用于另一项操作')
                if receipt.get('state') != 'accepted':
                    raise MessageRevisionConflict('该请求的准备结果尚未确认，请检查版本记录；不会自动重复执行')
                return self.display.web.session_view(self.workspace_id, session_id=record.session_id)
        actions.require_session_change(self.display)
        with self.display.runtime.execution_guard():
            return await self._submit_new(operation, body, fingerprint)

    async def _submit_new(self, operation, body, fingerprint):
        self.guard(body)
        events = self.events()
        target = next((event for event in events if self.revisable(event)
                       and event.payload.get('message_id') == body['message_id']), None)
        if target is None:
            raise MessageRevisionConflict('该消息不是当前会话中可修改的用户消息')
        text = body.get('message') if operation == 'edit' else '\n\n'.join(
            part['content'] for part in target.payload.get('parts', []) if part.get('kind') == 'text')
        attachments = self._attachments(target)
        if not isinstance(text, str) or len(text) > 100000 or not text.strip() and not attachments:
            raise ValueError('消息不能为空且不能超过 100000 字符')
        model_ref = body.get('model_ref') or self.runtime.model_ref
        capabilities = self.display.web.message_model(model_ref)
        if any(item[1]['kind'] == 'image' for item in attachments) and not capabilities['images']:
            raise ValueError('当前模型不支持图片输入，请切换模型后重试')
        source = self.runtime.session
        record = self.runtime.catalog.get_session(source.session_id)
        inherited = record.metadata.get('web_message_revision', {})
        family = inherited.get('family', source.session_id) if inherited.get('workspace_id') == self.workspace_id else source.session_id
        number = max([1] + [item.metadata['web_message_revision'].get('number', 1)
            for item in self.runtime.catalog.list_sessions()
            if item.metadata.get('web_message_revision', {}).get('family') == family
            and item.metadata['web_message_revision'].get('workspace_id') == self.workspace_id]) + 1
        receipt = {'workspace_id': self.workspace_id, 'family': family, 'operation': operation,
                   'request_id': body['request_id'], 'fingerprint': fingerprint, 'state': 'prepared',
                   'source_session_id': source.session_id, 'message_id': body['message_id'], 'number': number}
        from raccoon.modules.sessions.fork import fork_session
        session_id = fork_session(source.store, self.runtime.project_path, source.session_id,
            title=record.title, before_message_id=body['message_id'], metadata={'web_message_revision': receipt})
        data = source.store.load(session_id)
        owner = self._attachments_factory(self.runtime.catalog.root, self.workspace_id, session_id)
        self._index_copied_files(source.store, session_id, owner)
        descriptors = [owner.stage(raw, meta['filename'], meta['media_type']) for raw, meta in attachments]
        source.store.append_session_metadata_updated(data, web_message_revision={**receipt, 'state': 'accepted'})
        actions.send_chat_message(self.display.web, text, attachment_ids=[item['id'] for item in descriptors],
                                  project_id=self.workspace_id, session_id=session_id, event_id=body['request_id'],
                                  model_ref=model_ref, permission_mode=body.get('permission_mode'))
        return self.display.web.session_view(self.workspace_id, session_id=session_id)

    def _attachments(self, event):
        parts = [part for part in event.payload.get('parts', []) if part.get('kind') in {'image', 'file'}]
        refs = tuple(_user_attachment_ref(part) for part in parts)
        files = resolve_queued_attachments(refs, store_root=self.runtime.catalog.root,
                                          session_id=event.session_id)
        result = []
        for file, ref, part in zip(files, refs, parts):
            with file.path.open('rb') as stream:
                raw = stream.read(ref.size_bytes + 1)
            if len(raw) != ref.size_bytes or sha256(raw).hexdigest() != ref.sha256:
                raise ValueError('附件内容已变化，请检查原消息')
            result.append((raw, {'kind': part['kind'], **part['metadata']}))
        return result

    @staticmethod
    def _index_copied_files(store, session_id, owner):
        refs = []
        for event in store.list_events(session_id):
            if event.type != 'user_message':
                continue
            for part in event.payload.get('parts', []):
                if part.get('kind') not in {'image', 'file'}:
                    continue
                refs.append(_user_attachment_ref(part))
        owner.store.index_copied_refs(refs)
