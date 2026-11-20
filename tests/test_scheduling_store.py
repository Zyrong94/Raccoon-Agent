from contextlib import closing
from datetime import datetime, timezone
import sqlite3

import pytest

from raccoon.modules.scheduling.store import ScheduleStore, ScheduleConflict
from raccoon.modules.scheduling.schedules import next_time, parse_time


NOW = datetime(2026, 10, 28, tzinfo=timezone.utc).timestamp()


def store_at(tmp_path):
    return ScheduleStore(tmp_path / 'scheduling.sqlite3', now=lambda: NOW)


@pytest.fixture
def schedule_store(tmp_path):
    with closing(store_at(tmp_path)) as store:
        yield store


def create(store, **changes):
    # Existing permission-flow scenarios represent explicitly saved standard tasks.
    fields = dict(title='早报', prompt='整理今天的新闻', kind='interval', spec={'seconds': 3600},
                  timezone='Asia/Tokyo', project=str(store.path.parent), origin_session='origin',
                  channel='web', target='', model='test/model', permission_mode='ask')
    fields.update(changes)
    return store.create(**fields)


@pytest.mark.parametrize('mode', [None, 'ask', 'auto'])
def test_new_task_inherits_permissions_unless_explicitly_overridden(tmp_path, mode):
    store = store_at(tmp_path)
    try:
        task = store.create(title='Task', prompt='Work', kind='interval', spec={'seconds': 3600},
            timezone='UTC', project=str(tmp_path), origin_session='source', channel='web', target='',
            model='test/model', **({} if mode is None else {'permission_mode': mode}))
        assert task['permission_mode'] == mode
        assert store.get(task['id'])['permission_mode'] == mode
    finally:
        store.close()


def test_legacy_custom_agent_approval_cannot_be_accepted(tmp_path):
    import json

    store = store_at(tmp_path)
    task = create(store)
    run = store.trigger(task['id'])
    claim = store.take_pending()
    store.begin(claim)
    store.waiting(claim, {'id': 'request', 'question': 'Allow?', 'kind': 'permission_confirmation',
                         'options': [{'id': 'allow_once'}]})
    with store.transaction() as db:
        definition = json.loads(db.execute('SELECT definition FROM schedules WHERE id=?', (task['id'],)).fetchone()[0])
        definition['agent_name'] = 'restricted'
        db.execute('UPDATE schedules SET definition=? WHERE id=?', (json.dumps(definition), task['id']))
    pending = store.get_run(run['id'])
    with pytest.raises(ScheduleConflict, match='重新创建'):
        store.answer(run['id'], token=pending['pending_token'], request_id='request', answer='allow_once')
    assert store.get_run(run['id']) == pending
    store.close()


def test_calendar_timezone_and_interval():
    assert next_time('cron', {'cron': '0 9 * * 1-5'}, 'Asia/Tokyo', NOW) == NOW + 86400
    assert next_time('interval', {'seconds': 7200}, 'Asia/Tokyo', NOW) == NOW + 7200
    assert parse_time('2026-10-29T09:00:00', 'Asia/Tokyo') == NOW + 86400


@pytest.mark.parametrize('kind,spec', [('interval', {'seconds': True}), ('interval', {'seconds': 0}),
                                       ('cron', {'cron': '* * * * * *'}), ('cron', {'cron': '0 0 31 2 *'})])
def test_invalid_schedule_is_rejected(kind, spec):
    with pytest.raises(ValueError):
        next_time(kind, spec, 'Asia/Tokyo', NOW)


def test_once_rejects_nonexistent_local_time():
    with pytest.raises(ValueError):
        parse_time('2027-03-14T02:30:00', 'America/New_York')


@pytest.mark.parametrize('kind,spec,zone', [('once', {}, 'UTC'), ('once', {'at': 123}, 'UTC'),
                                         ('cron', {'cron': '0 9 * * *'}, 'No/SuchZone')])
def test_bad_time_inputs_are_validation_errors(kind, spec, zone):
    with pytest.raises(ValueError):
        next_time(kind, spec, zone, NOW)


def test_persistent_due_occurrence_is_unique_and_atomic(tmp_path, schedule_store):
    store = schedule_store
    task = create(store)
    store.materialize(NOW + 3600)
    store.materialize(NOW + 3600)
    runs = store.runs(task['id'])
    assert len(runs) == 1
    event = store.take_pending()
    assert event.type == 'timer.trigger'
    assert event.id == runs[0]['event_id']
    assert event.payload['origin']['occurrence_id'] == runs[0]['id']
    assert store.get(task['id'])['next_at'] == NOW + 7200
    store.close()
    with closing(store_at(tmp_path)) as reopened:
        assert reopened.get(task['id'])['next_at'] == NOW + 7200
        assert len(reopened.runs(task['id'])) == 1


def test_enqueue_failure_does_not_advance_schedule(schedule_store):
    store = schedule_store
    task = create(store)
    with store.transaction() as database:
        database.execute("CREATE TRIGGER reject_timer BEFORE INSERT ON schedule_runs BEGIN SELECT RAISE(ABORT, 'test'); END")
    with pytest.raises(sqlite3.IntegrityError, match='test'):
        store.materialize(NOW + 3600)
    assert store.get(task['id'])['next_at'] == NOW + 3600
    assert store.runs(task['id']) == []


def test_periodic_backlog_coalesces_while_one_run_is_pending(schedule_store):
    store = schedule_store
    task = create(store)
    store.materialize(NOW + 10 * 3600)
    claim = store.take_pending()
    store.begin(claim)
    store.materialize(NOW + 20 * 3600)
    store.materialize(NOW + 30 * 3600)
    runs = store.runs(task['id'])
    assert len(runs) == 2
    assert {r['status'] for r in runs} == {'queued', 'running'}
    pending = store.take_pending()
    assert pending is not None
    assert store.take_pending() is None
    store.finish(claim, status='succeeded')
    assert store.begin(pending)


def test_manual_run_preserves_cadence_and_pause_cancels_queued_run(schedule_store):
    store = schedule_store
    task = create(store)
    run = store.trigger(task['id'])
    assert store.get(task['id'])['next_at'] == task['next_at']
    store.change(task['id'], 'pause', revision=task['revision'])
    assert store.get_run(run['id'])['status'] == 'cancelled'
    assert store.take_pending() is None
    store.materialize(NOW + 86400)
    assert len(store.runs(task['id'])) == 1


def test_task_revision_and_active_edit_conflicts(schedule_store):
    store = schedule_store
    task = create(store)
    with pytest.raises(ScheduleConflict):
        store.update(task['id'], {'title': 'other'}, revision=999)
    store.trigger(task['id'])
    with pytest.raises(ScheduleConflict):
        store.update(task['id'], {'prompt': 'changed'}, revision=task['revision'])


def test_resume_skips_paused_backlog(schedule_store):
    store = schedule_store
    task = create(store)
    paused = store.change(task['id'], 'pause', revision=task['revision'])
    store.now = lambda: NOW + 86400
    resumed = store.change(task['id'], 'resume', revision=paused['revision'])
    assert resumed['next_at'] == NOW + 90000


def test_once_missed_while_stopped_is_recorded_without_enqueuing(tmp_path):
    with closing(store_at(tmp_path)) as store:
        task = create(store, kind='once', spec={'at': '2026-10-28T10:00:00+09:00'})
    with closing(ScheduleStore(tmp_path / 'scheduling.sqlite3', now=lambda: NOW + 7201)) as store:
        store.materialize(NOW + 7201)
        assert store.get(task['id'])['status'] == 'completed'
        assert store.runs(task['id'])[0]['status'] == 'skipped'
        assert store.take_pending() is None


def test_origin_scope_is_host_bound(schedule_store):
    store = schedule_store
    task = create(store)
    assert store.list(origin_session='other') == []
    with pytest.raises(KeyError):
        store.require_origin(task['id'], 'other')
