"""Validate authenticated event payloads at the execution boundary."""
from raccoon.modules.events.models import Event, EventError
from raccoon.app.payloads import EventInput, MessageInput, TimerInput, CompletionInput, AttachmentRef, SubagentInput, TurnSettings


def user_input(event: Event) -> MessageInput:
    origin = event.payload.get("origin")
    content = event.payload.get("content")
    if event.type != "user.message" or not isinstance(origin, dict) or origin.get("kind") != "user":
        raise EventError("scheduled chat currently accepts authenticated user.message only")
    if not isinstance(content, dict) or 'text' not in content or set(content) - {'text', 'attachments', 'settings'}:
        raise EventError('invalid user.message content')
    try:
        refs = content.get('attachments', [])
        if not isinstance(refs, list) or len(refs) > 16 or any(not isinstance(item, dict) for item in refs):
            raise ValueError('invalid queued attachments (max 16)')
        settings = None
        if 'settings' in content:
            if event.source != 'web':
                raise ValueError('消息执行配置仅支持已认证的 Web 消息')
            settings = TurnSettings(**content['settings'])
        return MessageInput(event.id, content["text"], tuple(AttachmentRef(**item) for item in refs), settings)
    except (TypeError, ValueError) as exc:
        raise EventError(str(exc)) from exc


def event_input(event: Event) -> EventInput:
    if event.type == 'subagent.message':
        if event.source != 'parent':
            raise EventError('子 Agent 消息来源无效')
        return SubagentInput(event.id, event.payload['parent_session_id'], event.payload['text'])
    if event.type == 'timer.trigger':
        return timer_input(event)
    if event.source != "background":
        return user_input(event)
    origin, content = event.payload.get("origin"), event.payload.get("content")
    if not isinstance(origin, dict) or origin.get("kind") != "system" or not isinstance(content, dict):
        raise EventError("invalid internal background completion envelope")
    result = content.get("result")
    if not isinstance(result, dict) or not isinstance(content.get("ok"), bool):
        raise EventError("invalid internal background completion result")
    if (result.get("session_id") != event.session_id or result.get("run_id") != origin.get("run_id")
            or result.get("job_id") != origin.get("job_id") or result.get("ok") is not content["ok"]
            or event.type != ("task.completed" if content["ok"] else "task.failed")
            or content["ok"] != (result.get("status") == "completed")):
        raise EventError("background completion identity or outcome mismatch")
    fields = {key: value for key, value in result.items() if key not in ("session_id", "ok")}
    try:
        return CompletionInput(event_id=event.id, **fields)
    except (TypeError, ValueError) as exc:
        raise EventError("invalid background completion fields") from exc


def timer_input(event: Event) -> TimerInput:
    origin, content = event.payload.get('origin'), event.payload.get('content')
    if (event.type != 'timer.trigger' or event.source != 'timer'
            or not isinstance(origin, dict) or origin.get('kind') != 'system'
            or not isinstance(origin.get('occurrence_id'), str) or not origin['occurrence_id']
            or not event.task_id or origin.get('timer_id') != event.task_id
            or not isinstance(content, dict) or not isinstance(content.get('prompt'), str)):
        raise EventError('invalid host-owned timer envelope')
    return TimerInput(event.id, event.task_id, content['prompt'])


def settle_cancelled_completion(event: Event, runner) -> bool:
    """A failed/cancelled background job records its result without a new answer."""
    if event.source != "background" or event.type not in ("task.completed", "task.failed"):
        return False
    session = runner.session
    message = event_input(event)
    if session is None or not isinstance(message, CompletionInput):
        return False
    manager = getattr(runner, "background_manager", None)
    from raccoon.modules.agent.native_async import native_completion_answered
    if message.tool_call_id is not None and native_completion_answered(session, message):
        return True
    from raccoon.application.conversations.background_events import record_failed_scheduled_completion
    if record_failed_scheduled_completion(session, message, manager):
        return True
    if message.tool_call_id is None:
        return False
    if message.status != "cancelled":
        return False
    # 只有 manager 确实拥有这个事件的运行身份时才查内存作业；否则短编号可能被
    # 新运行的同名作业占用，必须按持久化取消事件处理。
    if manager is not None and manager.owns_completion(
            run_id=message.run_id, job_id=message.job_id, session_id=event.session_id):
        job = manager.get(message.job_id, session_id=event.session_id)
        if job is not None and (job.status != "cancelled" or job.tool_call_id != message.tool_call_id):
            return False
    from raccoon.modules.agent.native_async import record_native_completion
    try:
        record_native_completion(session, message, manager)
    except ValueError:
        return False
    return True


def record_completion_only(event, context):
    """A stopped task records late tool results without starting another answer."""
    from dataclasses import asdict
    from raccoon.modules.agent.native_async import record_native_completion
    from raccoon.modules.agent.background_results import render_task_notification
    from raccoon.runtime.background import BackgroundNotification

    message = event_input(event)
    with context.execution_guard():
        if message.tool_call_id is not None:
            record_native_completion(context.session, message, context.background_manager)
            return
        fields = asdict(message)
        fields.pop('event_id')
        notification = BackgroundNotification(**fields, session_id=context.session_id,
                                              ok=message.status == 'completed')
        context.session.store.append_background_notification(context.session.data,
            content=render_task_notification(notification), job_id=message.job_id,
            tool_name=message.tool_name, status=message.status, task_id=message.task_id,
            observed_revision=message.observed_revision, source_event_id=message.event_id,
            run_id=message.run_id)
