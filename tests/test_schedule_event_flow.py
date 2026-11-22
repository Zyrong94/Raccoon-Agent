"""Timers enqueue ordinary instructions; only execution status belongs to scheduling."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from raccoon.modules.scheduling.store import ScheduleStore
from raccoon.modules.sessions.store import SessionStore
from raccoon.application.conversations.scheduled_main import ScheduledTurn
from raccoon.runtime.model_types import ChatResponse
from raccoon.runtime.user_input import UserInputRequest

NOW = 1800000000.0


def store_at(tmp_path):
    return ScheduleStore(tmp_path / 'scheduling.sqlite3', now=lambda: NOW)


def create(store, **changes):
    options = dict(title='检查项目', prompt='使用指定 Skill 检查项目', kind='interval', spec={'seconds': 3600},
                   timezone='UTC', project=str(store.path.parent), data_root=str(store.path.parent),
                   origin_session='origin', channel='web', target='')
    options.update(changes)
    return store.create(**options)


def test_timer_waits_until_due_and_targets_original_session(tmp_path):
    store = store_at(tmp_path)
    try:
        task = create(store)
        store.materialize(task['next_at'] - 1)
        assert store.take_pending() is None
        store.materialize(task['next_at'])
        event = store.take_pending()
        assert event.session_id == 'origin'
        assert event.payload['content']['prompt'] == task['prompt']
        assert store.take_pending() is None
        assert task['model'] is None and task['permission_mode'] is None
    finally:
        store.close()


def test_legacy_report_options_are_ignored_without_deleting_history(tmp_path):
    store = store_at(tmp_path)
    try:
        task = create(store)
        with store.transaction() as db:
            raw = json.loads(db.execute('SELECT definition FROM schedules WHERE id=?', (task['id'],)).fetchone()[0])
            raw.update(report_file='reports/old.html', prepare_seconds=600, previous_summary='old text', context_mode='fresh')
            db.execute('UPDATE schedules SET definition=? WHERE id=?', (json.dumps(raw), task['id']))
        current = store.get(task['id'])
        assert not {'report_file', 'prepare_seconds', 'previous_summary', 'context_mode'} & current.keys()
        store.materialize(task['next_at'] - 600)
        assert store.take_pending() is None
        run = store.trigger(task['id'])
        assert run['session_id'] == task['origin_session']
        saved = json.loads(store._connection.execute('SELECT definition FROM schedules WHERE id=?', (task['id'],)).fetchone()[0])
        assert saved['previous_summary'] == 'old text'
    finally:
        store.close()


def test_scheduled_turn_uses_exact_prompt_and_records_only_status(tmp_path):
    store = store_at(tmp_path)
    try:
        task = create(store)
        run = store.trigger(task['id'])
        event = store.take_pending()
        session = SimpleNamespace(store=SessionStore(tmp_path), session_id='origin', tool_registry=object(),
            scheduled_run_id=None, permission_manager=SimpleNamespace(policy=SimpleNamespace(project_root=tmp_path)))
        notifications = []
        def previous_handler(request, answer):
            notifications.append((request.id, answer))
        runtime = SimpleNamespace(session=session, background_manager=None, input_event_handler=previous_handler)
        executor = ScheduledTurn(tmp_path, 'origin', None, runtime, store=store)
        received = []
        async def execute(message, take_pending_inputs, *, try_close_input):
            received.append(message.text)
            question = UserInputRequest("choice", "ask_user", "Continue?")
            runtime.input_event_handler(question, None)
            assert store.get_run(run["id"])["status"] == "waiting"
            runtime.input_event_handler(question, "yes")
            assert store.get_run(run["id"])["status"] == "running"
            assert session.scheduled_run_id == run['id']
            return ChatResponse(provider='fake', model='fake', content='ordinary chat output', finish_reason='stop')
        runtime.run_turn = execute
        turn = SimpleNamespace(initial_event=event, try_close_input=lambda: True)
        result = asyncio.run(executor.execute(turn, runtime, lambda: ()))
        assert runtime.input_event_handler is previous_handler
        assert notifications == [("choice", None), ("choice", "yes")]
        assert result.content == 'ordinary chat output'
        assert received == [task['prompt']]
        assert session.scheduled_run_id is None
        history = store.runs(task['id'])
        assert history[0]['status'] == 'succeeded'
        assert 'output' not in history[0] and 'notices' not in history[0]
    finally:
        store.close()


def test_model_override_is_restored_when_execution_fails(monkeypatch):
    runtime = SimpleNamespace(provider='original', request_options='original-options', context_window=100,
                              model_ref='original', model_catalog=SimpleNamespace(models={'override': 'profile'}))
    def set_model(provider, *, request_options, context_window):
        runtime.provider, runtime.request_options, runtime.context_window = provider, request_options, context_window
    runtime.app_config = object()
    monkeypatch.setattr('raccoon.integrations.providers.factory.create_provider_for_model', lambda *_: 'override')
    monkeypatch.setattr('raccoon.application.conversations.model_switching.model_request_options', lambda _: 'options')
    runtime.set_model = set_model
    runtime.model_catalog.models['override'] = SimpleNamespace(context_window=200)
    executor = ScheduledTurn.__new__(ScheduledTurn)
    with pytest.raises(RuntimeError):
        with executor._settings({'model':'override'}, runtime):
            assert runtime.provider == 'override'
            raise RuntimeError('failed')
    assert (runtime.provider, runtime.request_options, runtime.context_window) == ('original','original-options',100)
