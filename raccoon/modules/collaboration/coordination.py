"""Subagent operations over the shared conversation runtime registry."""
from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from uuid import uuid4

from raccoon.app.session_runtime import SessionRuntime
from raccoon.app.conversations import SessionKey
from raccoon.modules.agent.loop_limits import AgentLoopLimits
from raccoon.modules.agent.runtime_capabilities import AgentRuntimeCapabilities
from raccoon.modules.collaboration.sessions import SubagentRequest, create_subagent_session, load_subagent_session, validate_subagent_boundary, build_subagent_result, build_subagent_prompt
from raccoon.modules.collaboration.worktree import Worktree
from raccoon.modules.events.models import Event
from raccoon.modules.sessions.catalog import is_safe_session_id
from raccoon.runtime.cancellation import current_cancellation_token
from raccoon.modules.tools.types import make_error_result, make_text_result


def _require_delegation(parent):
    session = parent.session
    if session.subagent_config is not None or not parent.runtime_capabilities.enable_delegate_tool:
        raise ValueError('当前会话不允许委派子 Agent')
    if session.allowed_tool_names is not None and 'delegate' not in session.allowed_tool_names:
        raise ValueError('当前会话未授权 delegate 工具')


def owned_subagent(parent, subagent_id):
    if not is_safe_session_id(subagent_id):
        raise ValueError('无效子会话 ID')
    data = parent.session.store.load(subagent_id)
    if data.metadata.get('parent_session_id') != parent.session.session_id:
        raise ValueError('子会话不属于此父会话')
    if data.metadata.get('subagent_execution_scope') != parent.session.subagent_scope:
        raise ValueError('子会话授权作用域不匹配')
    return data


def _scope_keys(parent, scope):
    service = parent.application
    return [key for key in service.subagent_keys(service.session_key(parent))
            if service.get_loaded_session(key).session.data.metadata.get('subagent_execution_scope') == scope]


def has_pending_subagents(parent):
    return any(parent.application.busy(key) for key in _scope_keys(parent, parent.session.subagent_scope))


def _stop_scope(parent, scope):
    for key in _scope_keys(parent, scope):
        parent.application.stop_session(key)


def run_subagent_operation(parent, coroutine):
    """Bridge a synchronous tool to the host loop and wait for real cancellation settlement."""
    service = parent.application
    if service is None or service.loop is None or service.loop.is_closed():
        coroutine.close()
        raise ValueError('子 Agent 没有活动的事件宿主')
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    if current is service.loop:
        coroutine.close()
        raise RuntimeError('同步工具必须在线程中调用；宿主代码直接 await 子 Agent 操作')
    future = asyncio.run_coroutine_threadsafe(coroutine, service.loop)
    token = current_cancellation_token()
    scope = parent.session.subagent_scope
    cancelled = False
    while True:
        try:
            return future.result(timeout=.1)
        except FutureTimeout:
            if token is not None and token.is_cancelled and not cancelled:
                cancelled = True
                service.loop.call_soon_threadsafe(_stop_scope, parent, scope)


def _runtime(parent, subagent):
    config = subagent.subagent_config
    return SessionRuntime(session=subagent, provider=parent.provider,
        tools=subagent.tool_registry.tools(supplied_only=True),
        limits=AgentLoopLimits(config.max_tool_rounds, config.max_provider_calls, config.max_turn_seconds),
        request_options=parent.request_options, routing_provider=parent.routing_provider,
        routing_request_options=parent.routing_request_options, context_window=parent.context_window,
        runtime_capabilities=AgentRuntimeCapabilities(allow_user_input=False, enable_delegate_tool=False),
        execution_root=subagent.store.root)


def _save_subagent_result(subagent, receipt):
    ok = receipt.status == 'succeeded'
    response = receipt.response
    result = build_subagent_result(subagent, ok=ok,
        summary=response.content if response is not None else receipt.error or 'Subagent stopped',
        error=None if ok else receipt.error or receipt.status)
    subagent.store.append_session_metadata_updated(subagent.data, subagent_last_result=result.to_data())
    return result


def _register_callbacks(parent, runtime):
    service = parent.application
    subagent = runtime.session
    def started(event):
        if subagent.data.metadata.get('subagent_execution_scope') != parent.session.subagent_scope:
            raise ValueError('子会话授权作用域已变化')
        validate_subagent_boundary(subagent)
        runtime.set_model(parent.provider, request_options=parent.request_options, context_window=parent.context_window)
        subagent.store.append_session_metadata_updated(subagent.data, subagent_run_id=event.id)

    def finished(receipt):
        if receipt.event.payload.get('foreground'):
            return  # The waiting operation saves and returns this receipt's result.
        result = _save_subagent_result(subagent, receipt)
        if parent.background_manager is not None:
            factory = make_text_result if result.ok else make_error_result
            parent.background_manager.publish_completed(
                job_id=f'subagent_{receipt.event.id}', session_id=parent.session.session_id,
                tool_name='delegate', completion_run_id=f'subagent:{parent.session.session_id}',
                result=factory('delegate', result.summary, **result.to_data()))

    service.register(runtime, on_start=started, on_finish=finished)


def _load_runtime(*, parent, session_id, project_root, data_root):
    return _runtime(parent, load_subagent_session(parent.session, session_id))


async def _get_runtime(parent, subagent_id):
    key = SessionKey(parent.session.store.root.resolve(), subagent_id)
    runtime = parent.application.get_loaded_session(key)
    if runtime is None:
        runtime = await parent.application.get_or_create_session(
            _load_runtime, parent=parent, project_root=parent.session.store.root,
            data_root=parent.session.store.root, session_id=subagent_id)
        _register_callbacks(parent, runtime)
    return runtime


def _message_event(parent, subagent_id, message, mode, event_id=None, *, foreground=False):
    return Event(id=event_id or uuid4().hex, type='subagent.message', source='parent', session_id=subagent_id,
        payload={'parent_session_id': parent.session.session_id, 'text': message,
                 'mode': mode, 'foreground': foreground})


async def spawn_subagent(parent, request: SubagentRequest, *, cancellation_token=None):
    _require_delegation(parent)
    subagent = await asyncio.to_thread(create_subagent_session, parent.session, request)
    scope = parent.session.subagent_scope
    if scope is not None:
        subagent.store.append_session_metadata_updated(subagent.data, subagent_execution_scope=scope)
    runtime = _runtime(parent, subagent)
    if parent.application.closing:
        runtime.close()
        raise ValueError('事件宿主正在关闭')
    _register_callbacks(parent, runtime)
    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()
    metadata = subagent.data.metadata
    worktree = None
    if metadata.get('worktree_path'):
        worktree = Worktree(subagent.session_id, Path(metadata['worktree_path']), metadata['worktree_branch'], 'HEAD')
    text = build_subagent_prompt(parent.session, request, config=subagent.subagent_config, worktree=worktree)
    receipt = parent.application.submit_event(
        _message_event(parent, subagent.session_id, text, 'follow_up', foreground=True),
        parent.application.session_key(runtime))
    await receipt.finished.wait()
    return _save_subagent_result(subagent, receipt)


async def send_subagent(parent, subagent_id, message, *, mode='steer', event_id=None,
                        wait_for_completion=True, cancellation_token=None):
    _require_delegation(parent)
    if not isinstance(message, str) or not message.strip() or len(message) > 100000 or mode not in {'steer', 'follow_up'}:
        raise ValueError('子会话消息或安排方式无效')
    data = await asyncio.to_thread(owned_subagent, parent, subagent_id)
    config = data.metadata['subagent_snapshot']['config']
    foreground = not config['allow_background'] or config['requires_worktree'] and not data.metadata.get('worktree_path')
    if foreground and not wait_for_completion:
        raise ValueError('该子 Agent 必须由父 Agent 前台调用')
    runtime = await _get_runtime(parent, subagent_id)
    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()
    if runtime.session.data.metadata.get('subagent_execution_scope') != parent.session.subagent_scope:
        raise ValueError('子会话授权作用域已变化')
    event = _message_event(parent, subagent_id, message.strip(), mode, event_id, foreground=foreground)
    receipt = parent.application.submit_event(event, parent.application.session_key(runtime))
    if foreground:
        await receipt.finished.wait()
        _save_subagent_result(runtime.session, receipt)
    return {'subagent_session_id': subagent_id, 'event_id': event.id, 'status': receipt.status,
            'ok': receipt.status not in {'failed', 'cancelled'},
            'summary': receipt.response.content if receipt.response else receipt.error or '',
            'delivery': 'processed' if receipt.finished.is_set() else 'queued'}


async def stop_subagent(parent, subagent_id):
    await asyncio.to_thread(owned_subagent, parent, subagent_id)
    key = SessionKey(parent.session.store.root.resolve(), subagent_id)
    await parent.application.stop_and_wait(key)
    return {'subagent_session_id': subagent_id, 'status': 'stopped'}


async def close_subagents(parent, scope):
    for key in _scope_keys(parent, scope):
        await parent.application.stop_and_wait(key)
        await parent.application.close_session(key)


async def list_subagents(parent):
    service = parent.application
    results = []
    for path in parent.session.store.sessions_dir.glob('*.jsonl'):
        data = await asyncio.to_thread(parent.session.store.load, path.stem)
        metadata = data.metadata
        if metadata.get('parent_session_id') != parent.session.session_id or metadata.get('subagent_execution_scope') != parent.session.subagent_scope:
            continue
        key = SessionKey(parent.session.store.root.resolve(), path.stem)
        loaded = service.get_loaded_session(key) if service is not None else None
        last = metadata.get('subagent_last_result') or {}
        state = service.get_session_status(key)['status'] if loaded is not None else ('failed' if last.get('error') else 'idle')
        results.append({'subagent_session_id': path.stem, 'agent_name': metadata.get('delegate_agent_name'),
                        'state': state, 'worktree_path': metadata.get('worktree_path'), 'last_result': last})
    return results
