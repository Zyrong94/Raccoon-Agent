"""Fixed-session execution, separate from navigation and display ownership."""
from __future__ import annotations


import uuid

from raccoon.app.payloads import TurnSettings
from raccoon.interfaces.web.attachments import WebAttachments
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.events import Event, EventSourceAdapter
from raccoon.application.workspaces.store import WorkspaceConflict as WorkbenchConflict
from raccoon.application.conversations.auto_routing import accepts_web_auto_event, classify_web_auto_event


def cancel_session(web, project_id, session_id):
    key = web.session_key(project_id, session_id)
    run = web.conversations.runs.get(key)
    event = Event(id=uuid.uuid4().hex, type='run.cancel', source='web', session_id=session_id,
                  payload={'run_id': run.initial_event.id if run else None})
    web.conversations.submit_event(event, key)
    web.publish_run_state(project_id, session_id)

def answer_question(display, request_id, answer):
    display.web.require_active_project(display._workspace_id(display.runtime.project_path))
    run = display.conversations.runs.get(display.key)
    event = Event(id=uuid.uuid4().hex, type='input.answer', source='web', session_id=display.key.session_id,
        payload={'run_id': run.initial_event.id if run else None, 'request_id': request_id, 'answer': answer})
    receipt = display.conversations.submit_event(event, display.key)
    if receipt.status == 'rejected':
        raise WorkbenchConflict(receipt.error)

def install_scheduling_tools(display):
    scheduling = display.web.scheduling
    if scheduling is None:
        return
    from raccoon.modules.scheduling.tools import install_scheduling
    install_scheduling(display.runtime, scheduling.store, channel='web',
                       timezone=lambda: display.runtime.session.user_timezone)

def install_canvas_tool(display):
    session = display.runtime.session
    runtime = display.runtime
    identity = display._workspace_id(runtime.project_path)
    def committed(call, result):
        loop = display.web.loop
        if result.ok and loop is not None and not loop.is_closed():
            # Capture at commit, before the next JSONL write can change the projection.
            artifacts, canvas = display.project_artifacts(runtime, session)
            loop.call_soon_threadsafe(display._publish_artifacts, identity, session.session_id, artifacts, canvas)
    session.tool_result_committed = committed
    if 'open_canvas' not in session.tool_registry.names():
        from raccoon.application.delivery.canvas import create_open_canvas_tool
        session.tool_registry.register(create_open_canvas_tool(project=runtime.project_path,
            reports=runtime.catalog.root / 'reports', session_id=session.session_id))

def create_parallel_runner(display):
    from raccoon.application.conversations.parallel_inference import ParallelBranchRunner
    session = display.runtime.session
    permission_manager = getattr(session, 'permission_manager', None)
    policy = getattr(permission_manager, 'policy', None)
    project_root = getattr(policy, 'project_root', None)
    if project_root is None:
        project_root = getattr(display.runtime, 'project_path', None) or display.runtime.catalog.root
    tool_names = {tool.name for tool in (display.runtime.tools or [])}
    tools_provider = getattr(display.runtime, 'tools_provider', None)
    if tools_provider is not None:
        try:
            tool_names |= {tool.name for tool in (tools_provider() or [])}
        except Exception:
            pass
    return ParallelBranchRunner(
        session_id=display.runtime.session.session_id,
        store=session.store,
        project_root=project_root,
        provider=display.runtime.provider,
        reasoning_effort=display.runtime.reasoning_effort,
        permission_manager=permission_manager,
        sandbox_access=getattr(session, 'sandbox_access', None),
        parent_tool_names=tool_names,
        user_timezone=getattr(session, 'user_timezone', None),
        auxiliary_models=display.runtime.auxiliary_models,
        provider_resolver=lambda: (display.runtime.provider, display.runtime.reasoning_effort),
    )

def require_idle(display):
    if display.busy:
        raise WorkbenchConflict("任务正在执行，请先停止或等待完成")
    if display.runtime.last_pending_input is not None:
        raise WorkbenchConflict("请先处理当前待确认请求")

def require_session_change(display):
    require_idle(display)
    manager = display.runtime.background_manager
    if manager is not None:
        for job in manager.list(session_id=display.runtime.session.session_id):
            if job.status == 'running':
                raise WorkbenchConflict('请先等待当前会话的后台任务完成，再修改消息版本')

def send_chat_message(web, message: str, *, project_id, session_id, attachment_ids=(),
                      mode='auto', event_id=None, model_ref=None, permission_mode=None):
    web.require_active_project(project_id)
    record = web.workspace.resolve_session(project_id, session_id)
    key = web.session_key(project_id, session_id)
    runtime = web.conversations.get_loaded_session(key)
    if runtime is not None and runtime.last_pending_input is not None:
        raise WorkbenchConflict("请先处理当前待确认请求")

    # Read configuration and staged attachments without constructing an execution runtime.
    default_model = web.default_model(record.metadata.get('model_ref'))
    if model_ref is None and default_model is None:
        raise ValueError('请先在配置管理中添加模型')
    default_mode = runtime.session.mode if runtime is not None else record.metadata.get('mode')
    default_mode = {'standard': 'ask', 'aggressive': 'auto'}.get(default_mode, default_mode)
    if default_mode not in ('ask', 'auto', 'bypass'):
        default_mode = 'ask'
    settings = TurnSettings(model_ref or default_model, permission_mode or default_mode)
    capabilities = web.message_model(settings.model_ref)
    attachments = WebAttachments(key.root, project_id, session_id)
    refs = attachments.store.staged_refs(attachment_ids)
    if any(ref.kind == 'image' for ref in refs) and not capabilities['images']:
        raise ValueError("当前模型不支持图片输入，请切换支持图片的模型后重试")

    event = EventSourceAdapter(main_session_id=session_id).user_message(
        source='web', message_id=event_id or uuid.uuid4().hex,
        conversation_id=session_id, text=message, attachments=refs, settings=settings)
    event.id = 'web-message:' + event.payload['origin']['message_id']
    event.payload['mode'] = mode

    if not web.sidebar.started(record):
        store = runtime.session.store if runtime is not None else SessionStore(key.root)
        data = runtime.session.data if runtime is not None else store.load(session_id)
        store.append_session_metadata_updated(data, web_chat_started=True)
        record = web.workspace.resolve_session(project_id, session_id)
    web.sidebar.remember(project_id, record)

    web.conversations.register_lazy(
        key, prepare=lambda first: web.prepare_session(
            project_id, session_id, model_ref=first.payload['content']['settings']['model_ref']),
        on_finish=lambda _receipt: web.publish_run_state(project_id, session_id),
    )
    web.conversations.submit_event(event, key)
    web.publish_run_state(project_id, session_id)


async def classify_message(display, event, snapshot):
    """Only the authenticated Web text entry of this owner may request auto routing."""
    if not accepts_web_auto_event(event, session_id=display.runtime.session.session_id):
        return None
    if display.runtime.last_pending_input is not None:
        # A pending answer must keep its original delivery path.
        return None
    return await classify_web_auto_event(event, snapshot, runner=display.runtime)

def set_permission_mode(display, mode):
    display.web.require_active_project(display._workspace_id(display.runtime.project_path))
    require_idle(display)
    resolved = display.runtime.session.set_permission_mode(mode)
    # A setting change must not rebuild or broadcast the conversation history.
    event = {'kind': 'permission_mode', 'permission_mode': resolved.value,
             'project_id': display._workspace_id(display.runtime.project_path),
             'session_id': display.runtime.session.session_id}
    display.publish(event)
    return {**event, 'seq': display.web._event_context(display)['event_seq'], 'epoch': display.web._event_context(display)['event_epoch']}

def set_model(display, model):
    display.web.require_active_project(display._workspace_id(display.runtime.project_path))
    require_idle(display)
    display.runtime.switch_model(model)
    # Switching a model changes capability and effort, not the conversation history.
    capabilities = getattr(display.runtime.provider, "capabilities", None)
    event = {'kind': 'model', 'model': display.runtime.model_ref,
             'effort': display.runtime.reasoning_effort,
             'input_capabilities': {'images': bool(getattr(capabilities, 'supports_vision', False)),
                                    'pdf': bool(getattr(capabilities, 'supports_pdf_input', False))},
             'project_id': display._workspace_id(display.runtime.project_path),
             'session_id': display.runtime.session.session_id}
    display.publish(event)
    return {**event, 'seq': display.web._event_context(display)['event_seq'], 'epoch': display.web._event_context(display)['event_epoch']}

def set_effort(display, effort):
    display.web.require_active_project(display._workspace_id(display.runtime.project_path))
    require_idle(display)
    display.runtime.set_effort(effort)
    display.publish({"kind": "snapshot", "state": display.snapshot()})

