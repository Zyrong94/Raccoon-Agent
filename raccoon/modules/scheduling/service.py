"""One local host drives durable occurrences; SessionRuntime owns inference."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import time

import portalocker

from raccoon.modules.scheduling.store import ScheduleStore, require_supported_execution

log = logging.getLogger(__name__)


class SchedulingService:
    def __init__(self, data_root, *, project_call, store=None, now=time.time, main_call=None):
        self.root = Path(data_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = store or ScheduleStore(self.root / 'scheduling.sqlite3', now=now)
        self._own_store = store is None
        self.project_call, self.now = project_call, now
        self.main_call = main_call
        self._lock = portalocker.Lock(str(self.root / 'scheduling.lock'), timeout=0)
        self.is_owner = False
        self._loop_task = None
        self._closed = False
        self.error = ''

    async def start(self, *, background=True):
        self._acquire()
        if background:
            self._loop_task = asyncio.create_task(self._loop())

    def _acquire(self):
        if self.is_owner:
            return
        try:
            self._lock.acquire()
        except portalocker.exceptions.LockException:
            return
        self.is_owner = True

    async def tick(self):
        self._acquire()
        if self._closed:
            return
        if self.is_owner:
            self.store.materialize(self.now())
        # Manual triggers belong to the submitting process, even when another host scans the clock.
        event = self.store.take_pending()
        while event is not None:
            run = self.store.run_for_event(event.id)
            task = self.store.get(run['task_id'])
            try:
                await self._route(task, 'scheduled', {'run_id': run['id']})
            except Exception as error:
                if self.store.get_run(run['id'])['status'] == 'queued':
                    self.store.finish(event, status='failed', error=str(error))
            event = self.store.take_pending()

    def is_main_task(self, task):
        return task.get('execution_owner') == 'personal-main' or task['channel'] == 'qq'

    async def _call_main(self, task, operation, body):
        require_supported_execution(task)
        if self.main_call is None or Path(task['data_root']).resolve() != self.root:
            raise ValueError('主会话执行桥尚未配置，禁止回退到独立runner')
        state = await self.main_call(task, 'snapshot', {})
        if state.get('session_id') != task['origin_session']:
            raise ValueError('任务绑定的原主会话已更换，禁止切换执行来源')
        return await self.main_call(task, operation, {**body, 'session_id': state['session_id'], 'generation': state['generation']})

    async def _route(self, task, operation, body):
        require_supported_execution(task)
        if self.is_main_task(task) or task['channel'] == 'qq':
            return await self._call_main(task, operation, body)
        return await self.project_call(operation, body)

    async def answer(self, run_id, *, token, request_id, answer):
        run = self.store.get_run(run_id)
        task = self.store.get(run['task_id'])
        return await self._route(task, 'scheduled_answer', {
            'run_id': run_id, 'token': token, 'request_id': request_id, 'answer': answer,
        })


    async def _loop(self):
        while not self._closed:
            try:
                await self.tick()
                self.error = ''
            except Exception as exc:
                self.error = '定时调度暂时失败，将自动重试'
                log.warning('Scheduling tick failed (%s)', type(exc).__name__)
            await asyncio.sleep(1)

    async def stop_intake(self):
        self._closed = True
        if self._loop_task:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
            self._loop_task = None

    async def close(self):
        await self.stop_intake()
        event = self.store.take_pending()
        while event is not None:
            self.store.finish(event, status='cancelled', error='宿主已停止')
            event = self.store.take_pending()
        if self.is_owner:
            self._lock.release()
            self.is_owner = False
        if self._own_store:
            self.store.close()
