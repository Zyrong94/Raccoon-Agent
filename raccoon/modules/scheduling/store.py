"""Timer definitions and execution status; message content stays in session history."""

from __future__ import annotations

import json
from pathlib import Path
import time
from uuid import uuid4

from raccoon.modules.events.sources import EventSourceAdapter
from collections import deque
from contextlib import contextmanager
import sqlite3
import threading
from raccoon.modules.scheduling.schedules import advance, next_time

ACTIVE = ('queued', 'running', 'waiting')


class ScheduleConflict(ValueError):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def require_supported_execution(task):
    # Keep legacy bindings intact so removal cannot silently expand their tool access.
    if task.get('agent_name') is not None:
        raise ScheduleConflict('此任务绑定的自定义主 Agent 已移除，请检查指令和权限后重新创建任务')


class ScheduleStore:
    def __init__(self, path, *, now=time.time):
        self.path, self.now = Path(path), now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._pending = deque()
        self.started_at = now()
        with self.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY, definition TEXT NOT NULL, status TEXT NOT NULL,
                next_at REAL, revision INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS schedule_runs (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, event_id TEXT UNIQUE NOT NULL,
                session_id TEXT NOT NULL, scheduled_at REAL NOT NULL, created_at REAL NOT NULL,
                status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                pending TEXT, answer TEXT, pending_token TEXT, finished_at REAL,
                manual INTEGER NOT NULL DEFAULT 0)''')
            db.execute('CREATE INDEX IF NOT EXISTS schedule_run_status ON schedule_runs(task_id,status)')
            run_columns = {row['name'] for row in db.execute('PRAGMA table_info(schedule_runs)')}
            if 'execution_data_root' not in run_columns:
                db.execute('ALTER TABLE schedule_runs ADD COLUMN execution_data_root TEXT')
                db.execute("""UPDATE schedule_runs SET execution_data_root=(SELECT json_extract(definition,'$.data_root')
                    FROM schedules WHERE schedules.id=schedule_runs.task_id)""")

    @contextmanager
    def transaction(self):
        with self._lock:
            nested = self._connection.in_transaction
            if not nested:
                self._connection.execute('BEGIN IMMEDIATE')
            try:
                yield self._connection
                if not nested:
                    self._connection.execute('COMMIT')
            except BaseException:
                if not nested:
                    self._connection.execute('ROLLBACK')
                raise

    def close(self):
        self._connection.close()

    def event_for_run(self, run):
        task = self.get(run['task_id'])
        event = EventSourceAdapter(main_session_id=run['session_id']).timer_triggered(
            timer_id=task['id'], occurrence_id=run['id'], payload={'prompt': task['prompt']})
        event.id = run['event_id']
        if task['channel'] == 'qq':
            event.payload['delivery'] = {'channel': 'qq', 'target': task['target']}
        return event

    def take_pending(self):
        while self._pending:
            run = self.get_run(self._pending.popleft())
            if run['status'] == 'queued':
                return self.event_for_run(run)
        return None

    def begin(self, event):
        with self.transaction() as db:
            row = db.execute("SELECT r.*,s.status AS task_status FROM schedule_runs r JOIN schedules s ON s.id=r.task_id WHERE r.event_id=? AND r.status='queued'", (event.id,)).fetchone()
            if row is None or row['task_status'] == 'deleted' or row['task_status'] == 'paused' and not row['manual']:
                return False
            db.execute("UPDATE schedule_runs SET status='running' WHERE event_id=?", (event.id,))
            return True

    def _task(self, row):
        if row is None:
            raise KeyError('定时任务不存在')
        definition = json.loads(row['definition'])
        # Old definitions and archived output remain on disk, but no longer affect execution.
        fields = ('title', 'prompt', 'kind', 'spec', 'timezone', 'project', 'origin_session',
                  'channel', 'target', 'model', 'permission_mode', 'data_root', 'permission_root',
                  'execution_owner', 'agent_name')
        task = {field: definition.get(field) for field in fields}
        task.update({key: row[key] for key in ('id', 'status', 'next_at', 'revision', 'created_at')})
        return task

    def get(self, identity):
        with self._lock:
            return self._task(self._connection.execute('SELECT * FROM schedules WHERE id=?', (identity,)).fetchone())

    def list(self, *, origin_session=None, project=None):
        with self._lock:
            rows = self._connection.execute("SELECT * FROM schedules WHERE status!='deleted' ORDER BY created_at DESC").fetchall()
        tasks = [self._task(row) for row in rows]
        return [t for t in tasks if (origin_session is None or t['origin_session'] == origin_session)
                and (project is None or t['project'] == str(Path(project).resolve()))]

    def require_origin(self, identity, origin_session):
        task = self.get(identity)
        if task['origin_session'] != origin_session or task['status'] == 'deleted':
            raise KeyError('此对话不能管理该任务')
        return task

    def create(self, *, title, prompt, kind, spec, timezone, project, origin_session, channel, target, model=None,
               permission_mode=None, request_id=None, data_root=None, permission_root=None, execution_owner=None):
        if channel not in ('web', 'qq'):
            raise ValueError('无效任务来源')
        for value, limit in ((title, 160), (prompt, 20000), (origin_session, 200)):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError('任务名称、指令和会话必须为有效文本')
        from raccoon.modules.permissions.types import PermissionMode
        if permission_mode is not None:
            permission_mode = PermissionMode(permission_mode).value
        if model is not None and (not isinstance(model, str) or not model.strip() or len(model) > 200):
            raise ValueError('模型标识无效')
        if channel == 'qq' and (not isinstance(target, str) or not target or len(target) > 256):
            raise ValueError('QQ 任务需要已验证的私聊目标')
        from raccoon.modules.sessions.catalog import is_safe_session_id
        if not is_safe_session_id(origin_session):
            raise ValueError('无效来源会话')
        path = Path(project).resolve()
        if not path.is_dir():
            raise ValueError('项目目录不存在')
        instant = next_time(kind, spec, timezone, self.now())
        definition = dict(title=title.strip(), prompt=prompt.strip(), kind=kind, spec=spec, timezone=timezone,
                          project=str(path), origin_session=origin_session, channel=channel, target=target,
                          model=model, permission_mode=permission_mode,
                          data_root=str(Path(data_root or self.path.parent).resolve()))
        if execution_owner not in (None, 'personal-main'):
            raise ValueError('无效宿主执行归属')
        definition['execution_owner'] = execution_owner
        definition['permission_root'] = str(Path(permission_root).resolve()) if permission_root is not None else None
        identity = str(uuid4())
        if request_id:
            from uuid import NAMESPACE_URL, uuid5
            identity = str(uuid5(NAMESPACE_URL, f'raccoon:schedule:{origin_session}:{request_id}'))
        if execution_owner == 'personal-main':
            if permission_root is None:
                definition['permission_root'] = str(Path(definition['data_root']) / 'schedule-permissions' / identity)
            elif Path(definition['permission_root']).resolve() == Path(definition['data_root']).resolve():
                raise ValueError('定时授权目录必须独立于主会话权限目录')
        with self.transaction() as db:
            old = db.execute('SELECT * FROM schedules WHERE id=?', (identity,)).fetchone()
            if old:
                task = self._task(old)
                if any(task[k] != v for k, v in definition.items()):
                    raise ScheduleConflict('创建标识已经用于其他任务')
                return task
            db.execute('INSERT INTO schedules(id,definition,status,next_at,created_at) VALUES (?,?,?,?,?)',
                       (identity, encoded(definition), 'enabled', instant, self.now()))
        return self.get(identity)

    def _checked(self, db, identity, revision):
        task = self._task(db.execute('SELECT * FROM schedules WHERE id=?', (identity,)).fetchone())
        if task['status'] == 'deleted' or revision != task['revision']:
            raise ScheduleConflict('任务已被修改，请刷新后重试')
        return task

    def _busy(self, db, identity, *, queued=True):
        statuses = ACTIVE if queued else ACTIVE[1:]
        return db.execute(f"SELECT 1 FROM schedule_runs WHERE task_id=? AND status IN ({','.join('?' for _ in statuses)}) LIMIT 1",
                          (identity, *statuses)).fetchone() is not None

    def update(self, identity, changes, *, revision):
        allowed = {'title', 'prompt', 'kind', 'spec', 'timezone', 'model', 'permission_mode'}
        if not changes or set(changes) - allowed:
            raise ValueError('只允许修改名称、指令、时间规则、时区、模型和权限')
        with self.transaction() as db:
            task = self._checked(db, identity, revision)
            if self._busy(db, identity):
                raise ScheduleConflict('请等待本次执行结束，或先暂停取消排队')
            definition = {k: v for k, v in task.items() if k not in {'id', 'status', 'next_at', 'revision', 'created_at'}}
            definition.update(changes)
            from raccoon.modules.permissions.types import PermissionMode
            if definition.get('permission_mode') is not None:
                definition['permission_mode'] = PermissionMode(definition['permission_mode']).value
            model = definition.get('model')
            if model is not None and (not isinstance(model, str) or not model.strip() or len(model) > 200):
                raise ValueError('模型标识无效')
            for key, limit in (('title', 160), ('prompt', 20000)):
                if not isinstance(definition[key], str) or not definition[key].strip() or len(definition[key]) > limit:
                    raise ValueError('名称或指令为空或过长')
            computed = next_time(definition['kind'], definition['spec'], definition['timezone'], self.now()) if (
                {'kind', 'spec', 'timezone'} & changes.keys()) else task['next_at']
            status = 'enabled' if task['status'] == 'completed' and {'kind', 'spec', 'timezone'} & changes.keys() else task['status']
            db.execute('UPDATE schedules SET definition=?,next_at=?,status=?,revision=revision+1 WHERE id=?',
                       (encoded(definition), computed, status, identity))
        return self.get(identity)

    def change(self, identity, action, *, revision):
        if action not in ('pause', 'resume', 'delete'):
            raise ValueError('无效操作')
        with self.transaction() as db:
            task = self._checked(db, identity, revision)
            if self._busy(db, identity, queued=False):
                raise ScheduleConflict('任务执行中、待确认或结果不确定，暂不能修改状态')
            status = {'pause': 'paused', 'resume': 'enabled', 'delete': 'deleted'}[action]
            instant = task['next_at']
            if action == 'resume' and task['status'] != 'enabled':
                instant = next_time(task['kind'], task['spec'], task['timezone'], self.now())
            if action != 'resume':
                rows = db.execute("SELECT event_id FROM schedule_runs WHERE task_id=? AND status='queued'", (identity,)).fetchall()
                db.execute("UPDATE schedule_runs SET status='cancelled',error='任务已暂停或删除',finished_at=? WHERE task_id=? AND status='queued'", (self.now(), identity))
            db.execute('UPDATE schedules SET status=?,next_at=?,revision=revision+1 WHERE id=?', (status, instant, identity))
        return self.get(identity)

    def runs(self, identity=None, *, limit=50):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError('历史条数必须在 1 到 200 之间')
        with self._lock:
            where, args = ('WHERE task_id=?', (identity,)) if identity else ('', ())
            rows = self._connection.execute(f'SELECT * FROM schedule_runs {where} ORDER BY rowid DESC LIMIT ?', (*args, limit)).fetchall()
            output = []
            for row in rows:
                run = dict(row)
                run['pending'] = json.loads(run['pending']) if run['pending'] else None
                run.pop('answer', None)
                run.pop('output', None)
                output.append(run)
            return output

    def _enqueue(self, db, task, instant, *, manual=False, skipped=False):
        identity = str(uuid4())
        session_id = task['origin_session']
        event = EventSourceAdapter(main_session_id=session_id).timer_triggered(
            timer_id=task['id'], occurrence_id=identity, payload={'prompt': task['prompt']})
        db.execute('''INSERT INTO schedule_runs(id,task_id,event_id,session_id,scheduled_at,created_at,status,manual)
                      VALUES (?,?,?,?,?,?,?,?)''', (identity, task['id'], event.id, session_id, instant, self.now(), 'skipped' if skipped else 'queued', int(manual)))
        db.execute('UPDATE schedule_runs SET execution_data_root=? WHERE id=?', (task['data_root'], identity))
        if skipped:
            db.execute("UPDATE schedule_runs SET error='一次性任务错过超过一小时',finished_at=? WHERE id=?", (self.now(), identity))
        if not skipped:
            self._pending.append(identity)
        return dict(db.execute('SELECT * FROM schedule_runs WHERE id=?', (identity,)).fetchone())

    def trigger(self, identity):
        with self.transaction() as db:
            task = self._task(db.execute('SELECT * FROM schedules WHERE id=?', (identity,)).fetchone())
            if task['status'] == 'deleted' or self._busy(db, identity):
                raise ScheduleConflict('任务已经排队、运行或需要检查结果')
            return self._enqueue(db, task, self.now(), manual=True)

    def materialize(self, now):
        with self.transaction() as db:
            for row in db.execute("SELECT * FROM schedules WHERE status='enabled' AND next_at<=?", (now,)).fetchall():
                task = self._task(row)
                queued = db.execute("SELECT 1 FROM schedule_runs WHERE task_id=? AND status='queued'", (task['id'],)).fetchone()
                waiting = db.execute("SELECT 1 FROM schedule_runs WHERE task_id=? AND status='waiting'", (task['id'],)).fetchone()
                if not queued and not waiting:
                    self._enqueue(db, task, task['next_at'], skipped=task['next_at'] < self.started_at)
                instant = advance(task['kind'], task['spec'], task['timezone'], task['next_at'], max(now, task['next_at']))
                db.execute('UPDATE schedules SET next_at=?,status=? WHERE id=?',
                           (instant, 'completed' if instant is None else 'enabled', task['id']))


    def finish_unstarted(self, receipt):
        if receipt.status in {'failed', 'cancelled'}:
            with self.transaction():
                if self.run_for_event(receipt.event.id)['status'] == 'queued':
                    self.finish(receipt.event, status=receipt.status, error=receipt.error or '任务已停止')

    def finish(self, event, *, status, error=''):
        if status not in ('succeeded', 'failed', 'cancelled'):
            raise ValueError('无效执行结果')
        with self.transaction() as db:
            updated = db.execute("""UPDATE schedule_runs SET status=?,error=?,pending=NULL,answer=NULL,
                pending_token=NULL,finished_at=? WHERE event_id=?""",
                (status, error, self.now(), event.id))
            if updated.rowcount != 1:
                raise KeyError('执行不存在')

    def waiting(self, event, pending):
        token = uuid4().hex[:16]
        with self.transaction() as db:
            run = db.execute('SELECT * FROM schedule_runs WHERE event_id=?', (event.id,)).fetchone()
            db.execute("UPDATE schedule_runs SET status='waiting',pending=?,answer=NULL,pending_token=? WHERE id=?",
                       (encoded(pending), token, run['id']))

    def answer(self, run_id, *, token, request_id, answer):
        with self.transaction() as db:
            run = db.execute('SELECT * FROM schedule_runs WHERE id=?', (run_id,)).fetchone()
            if run is None or run['status'] != 'waiting' or not token or token != run['pending_token'] or run['answer']:
                raise ScheduleConflict('待确认请求已失效或已经回答')
            require_supported_execution(self.get(run['task_id']))
            pending = json.loads(run['pending'])
            if request_id != pending['id'] or not isinstance(answer, str) or not answer.strip() or len(answer) > 20000:
                raise ValueError('回答与本次请求不匹配')
            if pending['kind'] == 'permission_confirmation' and answer not in {o['id'] for o in pending.get('options', [])}:
                raise ValueError('无效的权限选择')
            db.execute('UPDATE schedule_runs SET answer=? WHERE id=?', (answer, run_id))


    def run_for_event(self, event_id):
        with self._lock:
            row = self._connection.execute('SELECT * FROM schedule_runs WHERE event_id=?', (event_id,)).fetchone()
            if row is None:
                raise KeyError('执行不存在')
            result = dict(row)
            result.pop('output', None)
            result['pending'] = json.loads(result['pending']) if result['pending'] else None
            return result

    def get_run(self, run_id):
        with self._lock:
            row = self._connection.execute('SELECT event_id FROM schedule_runs WHERE id=?', (run_id,)).fetchone()
            if row is None:
                raise KeyError('执行不存在')
            return self.run_for_event(row['event_id'])

    def resume_main(self, run_id, request_id):
        with self.transaction() as db:
            row = db.execute("SELECT pending FROM schedule_runs WHERE id=? AND status='waiting'", (run_id,)).fetchone()
            if row is None or json.loads(row['pending'])['id'] != request_id:
                raise ScheduleConflict('主会话待回答请求已变化')
            db.execute("UPDATE schedule_runs SET status='running',pending=NULL,answer=NULL,pending_token=NULL WHERE id=?", (run_id,))

    def cancel_main(self, event, error):
        """Keep a stopped occurrence's pending snapshot for audit; invalidate its answer token."""
        with self.transaction() as db:
            db.execute("UPDATE schedule_runs SET status='cancelled',answer=NULL,pending_token=NULL,error=?,finished_at=? WHERE event_id=?",
                       (error, self.now(), event.id))
