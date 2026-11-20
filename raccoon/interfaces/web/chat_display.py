"""Read-only chat projections and Web progress notifications."""
from __future__ import annotations


import asyncio

from raccoon.application.delivery.permissions import pending_snapshot
from raccoon.application.delivery.messages import display_message, display_messages, running_tool_call_ids
from raccoon.application.workspaces.catalog import file_version
from raccoon.interfaces.web.attachments import WebAttachments
from raccoon.application.delivery.artifacts import session_artifacts
from raccoon.interfaces.web.artifacts import scoped_artifacts
from raccoon.application.delivery.canvas import session_canvas
from raccoon.app.session_runtime import SessionRuntime
from raccoon.modules.sessions.models import AgentMessage
from raccoon.modules.sessions.catalog import _preview
from raccoon.application.conversations.message_revisions import MessageRevisions
from raccoon.application.conversations.model_switching import model_options
from raccoon.application.workspaces.catalog import main_id, workspace_id
from raccoon.application.workspaces.sidebar import SidebarCatalog
from raccoon.application.workspaces.store import WorkspaceConflict as WorkbenchConflict


class ChatDisplay:
    """保存聊天展示状态，将执行通知转换为网页数据。"""

    _workspace_id = staticmethod(workspace_id)
    _main_id = staticmethod(main_id)

    def __init__(self, runtime: SessionRuntime, web):
        if runtime.session.session_id == self._main_id(runtime.catalog.root):
            raise WorkbenchConflict("主会话由渠道宿主管理，请通过侧边栏查看")
        if 'parent_session_id' in runtime.session.data.metadata:
            raise WorkbenchConflict('子代理由原父会话管理，请从协作面板查看')
        self.conversations = web.conversations
        self.key = self.conversations.session_key(runtime)
        self.web = web
        self.runtime = runtime
        self._pending_title = None
        self.run_phase = None
        self.messages = []
        self.artifacts = {}
        self.canvas = None
        self.activity = {}
        self._projection_version = None
        self._projected_artifacts = None
        self.sidebar = SidebarCatalog(web, runtime=lambda: self.runtime)
        self.message_revisions = MessageRevisions(self, attachments_factory=WebAttachments)
        self.runtime.session.store.on_message_committed = self._message_committed
        self.runtime.tool_event_handler = self._tool_callback
        self.runtime.run_phase_handler = self._run_phase_callback
        self.runtime.input_event_handler = self._input_callback
        self.refresh_history()


    @property
    def task(self):
        run = self.conversations.runs.get(self.key)
        return run.task if run is not None else None

    @property
    def busy(self):
        return self.conversations.busy(self.key)

    def _run_started(self, event):
        self.web.loop = asyncio.get_running_loop()
        if event.type == 'user.message':
            self._pending_title = _preview(event.payload['content']['text'])
        self.publish_run_state()

    def _run_finished(self, receipt):
        if receipt.event.type == 'timer.trigger':
            self.web.scheduling.store.finish_unstarted(receipt)
        settled = self.task is None
        if settled:
            self.run_phase = None
            self._pending_title = None
            self.activity.clear()
        previous_artifacts = self.artifact_state()
        self.refresh_artifacts()
        if self.artifact_state() != previous_artifacts:
            self.publish({'kind': 'artifacts', **self.artifact_state()})
        self.publish_run_state(settled=settled, revisions=self.message_revisions.snapshot())

    def execution_state(self):
        state = self.conversations.get_session_status(self.key)
        state['pending'] = pending_snapshot(state['pending'])
        return {
            **state,
            'run_phase': self.run_phase,
            'running_tool_call_ids': running_tool_call_ids(self.runtime, self.key.session_id),
            'parallel_replies': self.parallel.replies(),
        }

    def publish_run_state(self, *, settled=False, revisions=None):
        self.publish({'kind': 'run_state', 'state': {**self.execution_state(), **(revisions or {})},
                      'settled': settled})
        record = self.sidebar.current_record()
        if self.sidebar.started(record):
            project_id = self._workspace_id(self.runtime.project_path)
            self.web.sidebar_item_update(project_id, self.sidebar.family(record, project_id))

    def _publish_artifacts(self, project_id, session_id, artifacts, canvas):
        if project_id != self._workspace_id(self.runtime.project_path) or session_id != self.runtime.session.session_id:
            return
        self.artifacts, self.canvas = artifacts, canvas
        self.publish({'kind': 'artifacts', 'project_id': project_id, 'session_id': session_id,
                      **self.artifact_state()})

    def snapshot(self):
        refresh = not self.busy
        if refresh:
            self.refresh_history()
        navigation = {'execution_ready': True, **self.web.navigation_snapshot(self, refresh=refresh),
                      **self.web._event_context(self)}
        return self.build_snapshot(execution=self.execution_state(), navigation=navigation,
                                   revisions=self.message_revisions.snapshot())


    def publish(self, event):
        return self.web.publish_from(self, event)

    def _message_committed(self, session_id: str, message: AgentMessage) -> None:
        loop = self.web.loop
        if session_id == self.key.session_id and loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._publish_message, message)

    def _publish_message(self, message: AgentMessage) -> None:
        rows = display_message(message, attachments=self.attachment_store(), include_reasoning=True)
        if rows:
            known_ids = {row['id'] for row in self.messages}
            self.messages = [*self.messages, *(row for row in rows if row['id'] not in known_ids)]
            self.publish({'kind': 'message_committed', 'messages': rows})
            if message.role == 'user':
                self.publish_run_state()

    def _on_tool(self, payload):
        self.publish({'kind': 'tool', 'event': self.tool_event(payload)})


    def _ui_session(self, project_id, session_id):
        if project_id != self._workspace_id(self.runtime.project_path) or session_id != self.runtime.session.session_id:
            raise WorkbenchConflict('会话已切换，请重新载入界面')
        return self.runtime.session


    def attachment_store(self, project_id=None, session_id=None):
        active_project = self._workspace_id(self.runtime.project_path)
        active_session = self.runtime.session.session_id
        if project_id is not None or session_id is not None:
            self._ui_session(project_id, session_id)
        return WebAttachments(self.runtime.catalog.root, active_project, active_session)


    def _run_phase_callback(self, phase):
        self.web.loop.call_soon_threadsafe(self._on_run_phase, phase)


    def _on_run_phase(self, phase):
        if self.task is not None and self.run_phase != phase:
            self.run_phase = phase
            self.publish({"kind": "run_phase", "event": {"phase": phase}})


    def _input_callback(self, request, answer):
        self.web.loop.call_soon_threadsafe(self.publish_run_state)

    def _tool_callback(self, event):
        payload = {"id": event.tool_call.id, "name": event.tool_call.name, "kind": event.kind,
                   "arguments": event.tool_call.arguments,
                   "ok": event.result.ok if event.result else None,
                   "content": event.result.content[:12000] if event.result else "",
                   "review": event.prewrite_review}
        self.web.loop.call_soon_threadsafe(self._on_tool, payload)


    def refresh_history(self) -> None:
        version = self.log_version()
        changed = version != self._projection_version
        if changed:
            view = self.runtime.session.data
            self.messages = display_messages(view, attachments=self.attachment_store(), include_reasoning=True)
        if changed or self.artifact_version() != self._projected_artifacts:
            self.refresh_artifacts()
            self._projected_artifacts = self.artifact_version()
        self._projection_version = version


    def log_version(self):
        session = self.runtime.session
        path = session.store.sessions_dir / f'{session.session_id}.jsonl'
        return str(path), file_version(path)


    def artifact_version(self):
        return tuple((identity, file_version(entry['path']))
                     for identity, entry in self.artifacts.items())


    def refresh_artifacts(self) -> None:
        session = self.runtime.session
        self.artifacts, self.canvas = self.project_artifacts(self.runtime, session)


    def project_artifacts(self, runtime, session):
        events = session.store.list_events(session.session_id)
        artifacts = session_artifacts(events, session_id=session.session_id,
            project=runtime.project_path, reports=runtime.catalog.root / 'reports')
        return artifacts, session_canvas(events, artifacts)


    def artifact_state(self):
        return {
            'artifacts': scoped_artifacts(
                self.artifacts, workspace_id(self.runtime.project_path), self.runtime.session.session_id),
            'canvas': self.canvas,
        }


    def tool_event(self, payload):
        previous = self.activity.get(payload['id'])
        event = {**payload, 'order': previous['order'] if previous
                 else len(self.activity)}
        self.activity[payload['id']] = event
        return event


    def build_snapshot(self, *, execution: dict, navigation: dict, revisions: dict) -> dict:
        runner = self.runtime
        model = self.web.default_model(runner.session.data.metadata.get('model_ref'))
        capabilities = self.web.message_model(model) if model is not None else {'images': False, 'pdf': False}
        return {
            **navigation,
            'messages': self.messages,
            'permission_mode': self.runtime.session.mode,
            'model': model,
            'input_capabilities': capabilities,
            'effort': runner.request_options.extra_body.get('reasoning_effort')
                      or runner.request_options.extra_body.get('reasoning', {}).get('effort'),
            'models': model_options(runner.model_catalog),
            **execution,
            'live': [],
            'activity': list(self.activity.values()),
            **self.artifact_state(),
            **revisions,
        }


