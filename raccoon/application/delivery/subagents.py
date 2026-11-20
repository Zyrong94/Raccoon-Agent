"""Read-only live and stored Subagent projections."""
from raccoon.modules.sessions.catalog import is_safe_session_id
from raccoon.application.delivery.messages import display_messages
from raccoon.app.conversations import SessionKey

def subagent_view(store, parent_id, subagent_id):
    if not is_safe_session_id(subagent_id):
        raise ValueError('无效子代理会话 ID')
    view = store.load(subagent_id)
    if view.metadata.get('parent_session_id') != parent_id:
        raise ValueError('子代理不属于当前父会话')
    return view


def _summary(store, view, conversations):
    metadata = view.metadata
    key = SessionKey(store.root.resolve(), view.session_id)
    runtime = conversations.get_loaded_session(key) if conversations is not None else None
    result = metadata.get('subagent_last_result') or {}
    state = conversations.get_session_status(key)['status'] if runtime is not None else ('failed' if result.get('error') else 'idle')
    queued = conversations.history(key) if conversations is not None else []
    delivered = {message.metadata.get('source_event_id') for message in view.messages}
    messages = [{'event_id': receipt.event.id, 'text': receipt.event.payload.get('text', ''),
                 'mode': receipt.event.payload.get('mode', 'follow_up'), 'delivery': 'queued'}
                for receipt in queued if receipt.status == 'queued' and receipt.event.id not in delivered]
    latest_activity = ''
    restricted = False
    for message in view.messages:
        for part in message.parts:
            name = part.metadata.get('tool_name', 'tool')
            if part.kind == 'tool_call':
                latest_activity = f'运行工具：{name}'
            elif part.kind == 'tool_result':
                denied = (part.metadata.get('data') or {}).get('request_type') == 'permission_denied'
                restricted = restricted or denied
                latest_activity = f'权限受限：{part.content[:300]}' if denied else f"{'完成工具' if part.metadata.get('ok') else '工具失败'}：{name}"
    events = store.list_events(view.session_id)
    completed = next((event.created_at for event in reversed(events)
                      if event.type == 'session_metadata_updated' and 'subagent_last_result' in event.payload), None)
    result = metadata.get('subagent_last_result') or {}
    return {
        'subagent_session_id': view.session_id, 'agent_name': metadata.get('delegate_agent_name') or metadata.get('delegate_role'),
        'task': metadata.get('delegate_task', ''), 'state': state, 'summary': result.get('summary', ''),
        'latest_activity': latest_activity, 'queued_count': len(messages), 'queued_messages': messages,
        'created_at': events[0].created_at if events else None,
        'updated_at': events[-1].created_at if events else None,
        'completed_at': completed if state not in {'running', 'waiting', 'stopping'} else None,
        'restricted': restricted,
    }

def subagent_snapshot(runner, *, subagent_id=None):
    session = runner.session
    store, parent_id = session.store, session.session_id
    return stored_subagent_snapshot(store, parent_id, conversations=runner.application, subagent_id=subagent_id)

def stored_subagent_snapshot(store, parent_id, *, conversations=None, subagent_id=None):
    if subagent_id is not None:
        view = subagent_view(store, parent_id, subagent_id)
        return {**_summary(store, view, conversations), 'parent_session_id': parent_id,
                'messages': display_messages(view)}
    subagents = []
    for path in store.sessions_dir.glob('*.jsonl'):
        if not is_safe_session_id(path.stem):
            continue
        view = store.load(path.stem)
        if view.metadata.get('parent_session_id') == parent_id:
            subagents.append(_summary(store, view, conversations))
    return {'parent_session_id': parent_id, 'subagents': subagents}
