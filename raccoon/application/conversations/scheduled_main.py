"""Run trusted timer occurrences inside the existing personal conversation owner."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
import time
from uuid import uuid4

from raccoon.application.delivery.permissions import pending_snapshot
from raccoon.modules.permissions.grants import FilePermissionGrantStore, PermissionGrantStore
from raccoon.modules.permissions.manager import PermissionManager
from raccoon.modules.permissions.types import PermissionMode
from raccoon.modules.scheduling.store import ScheduleStore, ScheduleConflict, require_supported_execution
from raccoon.runtime import TimerInput
from raccoon.runtime import CancellationToken
from raccoon.modules.collaboration.coordination import close_subagents, has_pending_subagents, run_subagent_operation


class TaskGrantOverlay(PermissionGrantStore):
    def __init__(self, task_store, deny_grants):
        self.task_store = task_store
        super().__init__([*task_store.list(), *deny_grants])

    def add(self, grant):
        self.task_store.add(grant)
        super().add(grant)


class ScheduledTurn:
    def __init__(self, data_root, session_id, queue, runner, *, store=None, now=time.time):
        self.data_root = Path(data_root).resolve()
        self.session_id, self.application, self.runner = session_id, queue, runner
        self.store = store if store is not None else ScheduleStore(self.data_root / 'scheduling.sqlite3', now=now)
        self._own_store = store is None

    def close(self):
        if self._own_store:
            self.store.close()

    def _record(self, run_id):
        run = self.store.get_run(run_id)
        task = self.store.get(run['task_id'])
        require_supported_execution(task)
        project = self.runner.session.permission_manager.policy.project_root
        if (run['session_id'] != self.session_id or Path(task['data_root']).resolve() != self.data_root
                or self.runner.session.store.root.resolve() != self.data_root
                or Path(task['project']).resolve() != project.resolve()):
            raise ValueError('定时执行与目标会话的存储或项目不一致')
        if task['origin_session'] != self.session_id:
            raise ValueError('复用任务必须投递到来源会话')
        return task, run, self.store.event_for_run(run)

    async def dispatch(self, operation, body):
        key = self.application.session_key(self.runner)
        run_id = body['run_id']
        task, run, event = self._record(run_id)
        if operation == 'scheduled_answer':
            active = self.application.runs.get(key)
            if active is None or active.initial_event.id != event.id or run['status'] != 'waiting':
                raise ScheduleConflict('当前运行不是该定时任务')
            self.store.answer(run_id, token=body['token'], request_id=body['request_id'], answer=body['answer'])
            self.application.answer_run(key.session_id, body['request_id'], body['answer'], key=key)
            return {'accepted': True}
        if operation != 'scheduled':
            raise ValueError('不支持的定时操作')
        if run['status'] != 'queued' or task['status'] == 'deleted':
            raise ScheduleConflict('定时执行不再可提交')
        self.application.register(self.runner, timer_executor=self.execute)
        receipt = self.application.submit_event(event, key)
        return {'event_id': event.id, 'run_id': run_id, 'status': receipt.status}

    @contextmanager
    def _permissions(self, task, runner, *, run_id=None):
        session = runner.session
        background = runner.background_manager
        previous_jobs = {job.id for job in background.list(session_id=session.session_id)} if background else set()
        if task.get('permission_mode') is None:
            original_run_id = session.scheduled_run_id
            session.scheduled_run_id = run_id
            try:
                yield None, previous_jobs
            finally:
                session.scheduled_run_id = original_run_id
            return
        original = session.permission_manager
        original_mode, original_sandbox_mode = session.mode, session.sandbox_access.mode
        original_run_id = session.scheduled_run_id
        original_scope = session.subagent_scope
        if background and any(job.tool_name == 'delegate' and job.status == 'running'
                              for job in background.list(session_id=session.session_id)):
            raise ValueError('主会话仍有委派任务执行，定时任务暂不能切换授权')
        permission_root = Path(task.get('permission_root') or Path(task['data_root']) / 'schedule-permissions' / task['id'])
        if permission_root.resolve() == self.data_root:
            raise ValueError('定时授权不能写入主会话权限目录')
        task_grants = FilePermissionGrantStore(permission_root / 'permissions.json')
        grants = TaskGrantOverlay(task_grants, [grant for grant in original.grants.list() if grant.effect == 'deny'])
        mode = PermissionMode(task['permission_mode'])
        manager = PermissionManager(policy=original.policy, grants=grants, mode=mode,
                                    allow_user_approval=original.allow_user_approval)
        scope = run_id or str(uuid4())
        try:
            session.permission_manager = manager
            session.scheduled_run_id = run_id
            session.set_permission_mode(mode)
            session.subagent_scope = scope
            yield scope, previous_jobs
        finally:
            session.subagent_scope = original_scope
            session.permission_manager = original
            session.scheduled_run_id = original_run_id
            session.mode, session.sandbox_access.mode = original_mode, original_sandbox_mode

    def _delegate_jobs(self, runner, previous_jobs):
        background = runner.background_manager
        return [job for job in background.list(session_id=self.session_id)
                if job.id not in previous_jobs and job.tool_name == 'delegate' and job.status == 'running'] if background else []

    def _settle_delegates(self, runner, scope, previous_jobs):
        if scope is None:
            return
        jobs = self._delegate_jobs(runner, previous_jobs)
        for job in jobs:
            runner.background_manager.cancel(job.id, session_id=self.session_id)
        # Cancellation is cooperative; authority must stay installed until workers stop.
        while any(job.status == 'running' for job in jobs):
            time.sleep(.01)
        run_subagent_operation(runner, close_subagents(runner, scope))

    def _settle_failed_jobs(self, runner, run_id, previous_jobs):
        background = runner.background_manager
        if background is None:
            return
        calls = [part.metadata for message in runner.session.data.messages
                 for part in message.parts if part.kind == 'tool_call'
                 and part.metadata.get('scheduled_run_id') == run_id]
        jobs = [job for job in background.list(session_id=self.session_id)
                if job.id not in previous_jobs and any(
                    call.get('background_job_id') == job.id and call.get('background_run_id') == background.run_id
                    and call.get('tool_name') == job.tool_name
                    and (job.tool_call_id is None or call.get('tool_call_id') == job.tool_call_id) for call in calls)]
        for job in jobs:
            # A failed parent must not mark its TaskPlan work complete during result-only delivery.
            job.on_completed = None
            if job.status == 'running':
                background.cancel(job.id, session_id=self.session_id)
        while any(job.status == 'running' for job in jobs):
            time.sleep(.01)

    async def execute(self, turn, runner, take_pending_inputs):
        event = turn.initial_event
        run_id = event.payload.get('origin', {}).get('occurrence_id')
        task, run, canonical = self._record(run_id)
        if event.id != canonical.id or runner is not self.runner or not self.store.begin(canonical):
            raise ScheduleConflict('主会话定时执行已经开始或失效')

        previous_handler = runner.input_event_handler

        def notify(request, answer):
            if previous_handler is not None:
                previous_handler(request, answer)
            if request is None:
                return
            if answer is None:
                self.store.waiting(canonical, pending_snapshot(request))
            else:
                self.store.resume_main(run_id, request.id)

        try:
            with self._settings(task, runner), self._permissions(task, runner, run_id=run_id) as (scope, previous_jobs):
                try:
                    try:
                        runner.input_event_handler = notify
                        try:
                            result = await runner.run_turn(TimerInput(event.id, task['id'], task['prompt']),
                                                           take_pending_inputs, try_close_input=turn.try_close_input)
                        finally:
                            runner.input_event_handler = previous_handler
                        if scope is not None and (self._delegate_jobs(runner, previous_jobs) or has_pending_subagents(runner)):
                            raise RuntimeError('定时主助手结束时仍有本次委派任务未完成')
                    finally:
                        if scope is not None:
                            await runner._run_in_worker_thread(asyncio.to_thread(self._settle_delegates, runner, scope, previous_jobs),
                                                               CancellationToken())
                    succeeded = result.finish_reason in (None, 'stop')
                    status = 'succeeded' if succeeded else 'cancelled' if result.finish_reason == 'interrupted' else 'failed'
                    self.store.finish(canonical, status=status, error='' if succeeded else 'Agent未正常完成')
                    return result
                except BaseException:
                    await runner._run_in_worker_thread(asyncio.to_thread(self._settle_failed_jobs, runner, run_id, previous_jobs),
                                               CancellationToken())
                    raise
        except asyncio.CancelledError:
            self.store.cancel_main(canonical, '用户停止主会话定时执行；已记录的工具结果保留，不自动重放')
            raise
        except Exception as exc:
            self.store.finish(canonical, status='failed', error=type(exc).__name__)
            raise

    @contextmanager
    def _settings(self, task, runner):
        model = task.get('model')
        if model is None or model == runner.model_ref:
            yield
            return
        saved_model = runner.provider, runner.request_options, runner.context_window
        try:
            from raccoon.application.conversations.model_switching import model_request_options
            from raccoon.integrations.providers.factory import create_provider_for_model
            profile = runner.model_catalog.models[model]
            provider = create_provider_for_model(runner.app_config, profile)
            runner.set_model(provider, request_options=model_request_options(profile), context_window=profile.context_window)
            yield
        finally:
            runner.set_model(saved_model[0], request_options=saved_model[1], context_window=saved_model[2])
