"""Own completion subscriptions and durable delivery, without session writes."""

from __future__ import annotations

from dataclasses import asdict
import sqlite3

from raccoon.runtime.background import BackgroundNotification


def record_failed_scheduled_completion(session, message, manager) -> bool:
    """Settle an exact failed timer's result without opening another model turn."""
    calls = [part.metadata for item in session.data.messages for part in item.parts
             if part.kind == 'tool_call' and (
                 part.metadata.get('tool_call_id') == message.tool_call_id if message.tool_call_id is not None else
                 part.metadata.get('background_job_id') == message.job_id
                 and part.metadata.get('background_run_id') == message.run_id)]
    tagged = [call for call in calls if 'scheduled_run_id' in call]
    if not tagged:
        return False
    if (len(calls) != 1 or not isinstance(tagged[0]['scheduled_run_id'], str) or not tagged[0]['scheduled_run_id']
            or tagged[0].get('background_run_id') != message.run_id
            or tagged[0].get('background_job_id') != message.job_id
            or tagged[0].get('tool_name') != message.tool_name):
        raise ValueError('scheduled background completion does not match its original call')
    database = session.store.root.resolve() / 'scheduling.sqlite3'
    try:
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute('SELECT status,session_id FROM schedule_runs WHERE id=?',
                             (tagged[0]['scheduled_run_id'],)).fetchone()
    except sqlite3.Error as exc:
        raise ValueError('cannot verify scheduled background completion owner') from exc
    if row is None or row[1] != session.session_id:
        raise ValueError('scheduled background completion owner is missing or mismatched')
    if row[0] not in ('failed', 'cancelled'):
        return False
    if manager is not None and manager.owns_completion(
            run_id=message.run_id, job_id=message.job_id, session_id=session.session_id):
        job = manager.get(message.job_id, session_id=session.session_id)
        if job is None or job.tool_call_id != message.tool_call_id or job.tool_name != message.tool_name:
            raise ValueError('scheduled background completion has a mismatched runtime job')
        job.on_completed = None
    if message.tool_call_id is not None:
        from raccoon.modules.agent.native_async import record_native_completion
        record_native_completion(session, message, manager)
    else:
        from raccoon.modules.agent.background_results import render_task_notification
        fields = asdict(message)
        fields.pop('event_id')
        note = BackgroundNotification(**fields, session_id=session.session_id, ok=message.status == 'completed')
        if manager is not None and manager.owns_completion(
                run_id=message.run_id, job_id=message.job_id, session_id=session.session_id):
            note = manager.finalize_completed(message.job_id, session_id=session.session_id)
            if note is None:
                raise ValueError('scheduled background completion has no finished runtime job')
        session.store.append_background_notification(session.data, content=render_task_notification(note), job_id=message.job_id,
            tool_name=message.tool_name, status=note.status, task_id=note.task_id,
            observed_revision=note.observed_revision, source_event_id=message.event_id, run_id=message.run_id)
    return True
