"""One in-memory event intake for conversation hosts.

Only this object schedules runs. Channels supply presentation callbacks; they
do not maintain another queue or restart an Agent to answer a question.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from copy import deepcopy
from functools import partial
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from raccoon.app.inputs import parse_event_input, settle_cancelled_completion
from raccoon.modules.events.models import Event, EventError
from raccoon.modules.events.sources import EventSourceAdapter


@dataclass(frozen=True, slots=True)
class SessionKey:
    root: Path
    session_id: str


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    session_id: str
    events: tuple[Event, ...]


@dataclass(slots=True)
class EventRecord:
    event: Event
    key: SessionKey
    status: str = "queued"
    error: str | None = None
    response: Any = None
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    queue_only: bool = False


@dataclass(slots=True)
class SessionBinding:
    context: Any
    prepare: Callable | None = None
    on_start: Callable | None = None
    on_response: Callable | None = None
    on_finish: Callable | None = None
    on_user_event: Callable | None = None
    classifier: Callable | None = None
    parallel_executor: Callable | None = None
    timer_executor: Callable | None = None
    unsubscribe: Callable | None = None
    dispatch_requested: bool = False


@dataclass(slots=True)
class ActiveRun:
    initial_event: Event
    key: SessionKey
    binding: SessionBinding
    task: asyncio.Task | None = None
    cancelled: bool = False
    started: bool = False
    pending_events: list[Event] = field(default_factory=list)
    consumed_event_ids: list[str] = field(default_factory=list)
    accepting_inputs: bool = True
    lock: Lock = field(default_factory=Lock)

    @property
    def session_id(self):
        return self.key.session_id

    def take_pending_inputs(self, loop):
        with self.lock:
            if self.cancelled:
                return ()
            events = self.pending_events
            self.pending_events = []
            self.consumed_event_ids.extend(event.id for event in events)
        for event in events:
            if event.type == "user.message" and self.binding.on_user_event is not None:
                loop.call_soon_threadsafe(self.binding.on_user_event, event)
        return tuple(event.input for event in events)

    def try_close_input(self) -> bool:
        with self.lock:
            if self.pending_events:
                return False
            self.accepting_inputs = False
            return True


class ConversationService:
    def __init__(self):
        self.sessions: dict[SessionKey, SessionBinding] = {}
        self.queues: dict[SessionKey, deque[Event]] = {}
        self.runs: dict[SessionKey, ActiveRun] = {}
        self.dispatch_tasks: dict[SessionKey, asyncio.Task] = {}
        self.parallel_tasks: dict[SessionKey, set[asyncio.Task]] = {}
        self.event_records: OrderedDict[tuple[SessionKey, str], EventRecord] = OrderedDict()
        self.silent_jobs: set[tuple[SessionKey, str, str]] = set()
        self.loop = None
        self.closing = False
        self.preparing_sessions = {}

    @staticmethod
    def session_key(context):
        return SessionKey(context.session.store.root.resolve(), context.session.session_id)

    def get_loaded_session(self, key):
        binding = self.sessions.get(key)
        return binding.context if binding is not None else None

    async def get_or_create_session(self, factory, **options):
        root = Path(options.get('data_root') or Path(options['project_root']) / '.raccoon').resolve()
        key = SessionKey(root, options['session_id'])
        runtime = self.get_loaded_session(key)
        if runtime is not None:
            return runtime
        if self.closing:
            raise ValueError('服务正在关闭')
        if key not in self.preparing_sessions:
            async def create():
                runtime = await asyncio.to_thread(factory, **options)
                if self.closing:
                    runtime.close()
                    raise ValueError('服务正在关闭')
                try:
                    self.register(runtime)
                    return runtime
                except BaseException:
                    runtime.close()
                    raise
            task = asyncio.create_task(create())
            self.preparing_sessions[key] = task
            def finished(task):
                self.preparing_sessions.pop(key, None)
                if not task.cancelled():
                    task.exception()
            task.add_done_callback(finished)
        return await asyncio.shield(self.preparing_sessions[key])

    def get_session_status(self, key):
        runtime = self.get_loaded_session(key)
        pending = runtime.last_pending_input if runtime is not None else None
        active = self.runs.get(key)
        busy = self.busy(key)
        status = 'idle'
        error = None
        if active is not None and active.cancelled:
            status = 'stopping'
        elif pending is not None:
            status = 'waiting'
        elif busy:
            status = 'running'
        else:
            turns = [record for record in self.history(key) if record.event.type not in {'run.cancel', 'input.answer'}]
            if turns:
                last = turns[-1]
                status = 'interrupted' if last.status == 'cancelled' else 'error' if last.status == 'failed' else 'completed'
                error = last.error
        return {'busy': busy, 'status': status, 'pending': pending, 'error': error,
                'cancellable': busy and status != 'stopping', 'pending_messages': self.pending_messages(key)}

    def subagent_keys(self, parent_key):
        return [key for key, binding in self.sessions.items()
                if key.root == parent_key.root
                and binding.context is not None
                and binding.context.session.data.metadata.get('parent_session_id') == parent_key.session_id]

    async def stop_and_wait(self, key):
        keys = [key, *self.subagent_keys(key)]
        tasks = []
        for target in keys:
            active = self.runs.get(target)
            if active is not None and active.task is not None:
                tasks.append(active.task)
            dispatch = self.dispatch_tasks.get(target)
            if dispatch is not None:
                tasks.append(dispatch)
            tasks.extend(self.parallel_tasks.get(target, ()))
        self.stop_session(key)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def require_session_idle(self, key):
        for subagent_key in self.subagent_keys(key):
            self.require_session_idle(subagent_key)
        if key in self.preparing_sessions or self.busy(key):
            raise ValueError('会话正在运行或准备执行，请等待结束')
        runtime = self.get_loaded_session(key)
        if runtime is None:
            return
        if runtime.last_pending_input is not None:
            raise ValueError('会话仍在等待回答')
        manager = runtime.background_manager
        if manager is not None and any(job.status == 'running' for job in manager.list(session_id=key.session_id)):
            raise ValueError('会话仍有后台任务')

    async def close_session(self, key):
        self.require_session_idle(key)
        for subagent_key in self.subagent_keys(key):
            await self.close_session(subagent_key)
        runtime = self.get_loaded_session(key)
        self.unregister(key)
        if runtime is not None:
            runtime.close()

    def register_lazy(self, key, *, prepare, on_finish):
        if key not in self.sessions:
            self.sessions[key] = SessionBinding(None, prepare=prepare, on_finish=on_finish)
            self.queues[key] = deque()

    def register(self, context, **callbacks):
        context.application = self
        key = self.session_key(context)
        binding = self.sessions.get(key)
        if binding is None:
            binding = SessionBinding(context, **callbacks)
            self.sessions[key] = binding
            self.queues[key] = deque()
        else:
            if binding.context is None:
                binding.context = context
            elif binding.context is not context:
                raise ValueError("会话已有执行上下文")
            for name, callback in callbacks.items():
                setattr(binding, name, callback)
        if self.loop is not None:
            self._subscribe_background(key, binding)
        return key

    def start(self):
        loop = asyncio.get_running_loop()
        if self.loop is not None and self.loop is not loop:
            raise RuntimeError("事件入口必须留在所属事件循环")
        self.loop = loop
        for key, binding in self.sessions.items():
            self._subscribe_background(key, binding)

    def unregister(self, key):
        if self.busy(key):
            raise ValueError("会话仍有运行或排队消息")
        binding = self.sessions.pop(key, None)
        if binding is not None and binding.unsubscribe is not None:
            binding.unsubscribe()
        dispatch = self.dispatch_tasks.pop(key, None)
        if dispatch is not None:
            dispatch.cancel()
        self.queues.pop(key, None)

    def _subscribe_background(self, key, binding):
        manager = binding.context.background_manager if binding.context is not None else None
        if manager is None or binding.unsubscribe is not None:
            return
        source = EventSourceAdapter(main_session_id=key.session_id)

        def completed(notification):
            if notification.session_id != key.session_id or not manager.owns_completion(
                run_id=notification.run_id, job_id=notification.job_id, session_id=key.session_id,
            ):
                raise ValueError("后台结果不属于此会话")
            event = source.background_completed(
                run_id=notification.run_id, job_id=notification.job_id,
                session_id=key.session_id, ok=notification.ok, result=asdict(notification),
            )
            self.loop.call_soon_threadsafe(self._receive_background, event, key)

        binding.unsubscribe = manager.bind_completion_sink(key.session_id, completed)

    def _receive_background(self, event, key):
        if not self.closing and key in self.sessions:
            self.submit_event(event, key)

    def key_for_session(self, session_id):
        matches = [key for key in self.sessions if key.session_id == session_id]
        if len(matches) != 1:
            raise ValueError("事件必须指向唯一已登记会话")
        return matches[0]

    @property
    def pending_inputs(self):
        return {key.session_id: binding.context.last_pending_input for key, binding in self.sessions.items()
                if binding.context is not None and binding.context.last_pending_input is not None}

    def accepts_images(self, session_id):
        binding = self.sessions[self.key_for_session(session_id)]
        return bool(getattr(binding.context.provider.capabilities, "supports_vision", False))

    def timer_active(self, session_id):
        run = self.runs.get(self.key_for_session(session_id))
        return run is not None and run.initial_event.type == "timer.trigger"

    def answer_run(self, session_id, request_id, answer, *, key=None):
        from uuid import uuid4
        if key is None:
            key = self.key_for_session(session_id)
        run = self.runs.get(key)
        event = Event(id=uuid4().hex, type="input.answer", source="local", session_id=session_id,
                      payload={"run_id": run.initial_event.id if run else None,
                               "request_id": request_id, "answer": answer})
        record = self.submit_event(event, key)
        if record.status == "rejected":
            raise EventError(record.error)
        return record

    def history(self, key):
        return [item for (session, _), item in self.event_records.items() if session == key]

    def submit_event(self, event: Event, key: SessionKey | None = None):
        if self.closing:
            raise ValueError("服务正在关闭")
        if self.loop is None:
            self.start()
        if key is None:
            key = self.key_for_session(event.session_id)
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("请通过所属事件循环提交事件")
        if key not in self.sessions or event.session_id != key.session_id:
            raise ValueError("事件与目标会话不匹配")
        event.validate()
        identity = (key, event.id)
        previous = self.event_records.get(identity)
        if previous is not None:
            if (previous.event.type, previous.event.source, previous.event.payload) != (event.type, event.source, event.payload):
                raise ValueError("同一事件 ID 不能用于不同内容")
            return previous
        event = deepcopy(event)
        if event.type not in {"input.answer", "run.cancel"}:
            event.input = parse_event_input(event)
        record = EventRecord(event, key)
        self.event_records[identity] = record
        for old_key, old in list(self.event_records.items()):
            if len(self.event_records) <= 2048:
                break
            if old.finished.is_set():
                del self.event_records[old_key]
        if event.type == "input.answer":
            self._answer(record)
        elif event.type == "run.cancel":
            expected = event.payload.get("run_id")
            active = self.runs.get(key)
            if (expected is None and active is not None) or (expected is not None and (active is None or active.initial_event.id != expected)):
                self._finish_event(record, "rejected", "运行已变化")
            else:
                self.stop_session(key)
                self._finish_event(record, "applied")
        else:
            self.queues[key].append(event)
            self._schedule_dispatch(key)
        return record

    def _answer(self, record):
        active = self.runs.get(record.key)
        data = record.event.payload
        if active is None or active.cancelled or (data.get("run_id") is not None and data["run_id"] != active.initial_event.id):
            self._finish_event(record, "rejected", "当前没有对应的待答运行")
            return
        try:
            active.binding.context.answer_input(data["request_id"], data["answer"])
        except (ValueError, KeyError) as error:
            self._finish_event(record, "rejected", str(error))
        else:
            self._finish_event(record, "applied")

    def _schedule_dispatch(self, key):
        if self.closing or key not in self.sessions:
            return
        if key in self.dispatch_tasks:
            # A new message can arrive while an earlier routing decision is awaiting its model.
            self.sessions[key].dispatch_requested = True
            return
        task = asyncio.create_task(self._dispatch_pending_events(key))
        self.dispatch_tasks[key] = task
        task.add_done_callback(lambda done: self._dispatch_done(key, done))

    def _dispatch_done(self, key, task):
        if self.dispatch_tasks.get(key) is task:
            del self.dispatch_tasks[key]
        if not task.cancelled() and task.exception() is not None:
            pending = self.queues.get(key)
            if pending:
                event = pending.popleft()
                self._finish_event(self.event_records[key, event.id], "failed", str(task.exception()))
        if self.queues.get(key) and (key not in self.runs or self.sessions[key].dispatch_requested):
            self._schedule_dispatch(key)

    async def _dispatch_pending_events(self, key):
        pending = self.queues[key]
        binding = self.sessions[key]
        binding.dispatch_requested = False
        for event in tuple(pending):
            record = self.event_records[key, event.id]
            if self.closing or record.status != "queued":
                continue
            active = self.runs.get(key)
            if active is not None and active.cancelled:
                return
            if binding.prepare is not None:
                try:
                    await binding.prepare(record.event)
                except Exception as error:
                    pending.remove(event)
                    self._finish_event(record, "failed", str(error))
                    binding.on_finish(record)
                    continue
                binding.prepare = None
            if event.source == "background":
                origin = event.payload['origin']
                identity = (key, origin['run_id'], origin['job_id'])
                if identity in self.silent_jobs:
                    if active is not None:
                        return  # The cancelled worker still owns the session log.
                    from raccoon.app.inputs import record_completion_only
                    await asyncio.to_thread(record_completion_only, event, binding.context)
                    pending.remove(event)
                    self.silent_jobs.discard(identity)
                    self._finish_event(record, "consumed")
                    continue
            if active is None and event.source == "background" and await asyncio.to_thread(settle_cancelled_completion, event, binding.context):
                pending.remove(event)
                self._finish_event(record, "consumed")
                continue
            if record.status != "queued" or self.closing:
                continue
            active = self.runs.get(key)
            if active is None:
                pending.remove(event)
                self._start_run(record, binding)
                continue
            if active.cancelled or not active.accepting_inputs:
                return
            if active.initial_event.type == "timer.trigger":
                continue
            mode = event.payload.get("mode", "steer" if event.type == "user.message" else "follow_up")
            if event.source == "background":
                mode = "steer"
            if record.queue_only or event.type == "timer.trigger":
                mode = "queue"
            if event.type == "user.message":
                settings = event.payload['content'].get('settings')
                active_settings = active.initial_event.payload.get('content', {}).get('settings')
                if settings is not None and settings != active_settings:
                    record.queue_only = True
                    mode = "queue"
            if mode == "auto" and binding.classifier is not None:
                with active.lock:
                    events = [active.initial_event]
                    events.extend(self.event_records[key, event_id].event for event_id in active.consumed_event_ids)
                snapshot = SessionSnapshot(key.session_id, tuple(events))
                try:
                    async with asyncio.timeout(binding.context.routing_timeout_seconds or 5):
                        mode = await binding.classifier(event, snapshot)
                except Exception:
                    mode = "queue"
                if self.runs.get(key) is not active or active.cancelled:
                    # The classification was made for a run that no longer owns this session.
                    return
            if record.status != "queued" or self.closing:
                continue
            if not active.accepting_inputs:
                return
            if mode == "steer":
                with active.lock:
                    if not active.accepting_inputs:
                        return
                    pending.remove(event)
                    active.pending_events.append(event)
                    record.status = "delivered"
            elif mode == "interrupt":
                pending.remove(event)
                pending.appendleft(event)
                self._cancel_run(active)
                return
            elif mode == "parallel" and binding.parallel_executor is not None:
                pending.remove(event)
                record.status = "running"
                task = asyncio.create_task(self._execute_parallel(record, binding))
                tasks = self.parallel_tasks.setdefault(key, set())
                tasks.add(task)
                task.add_done_callback(lambda done, key=key, record=record, binding=binding:
                                       self._parallel_done(key, record, binding, done))

    def _start_run(self, record, binding):
        run = ActiveRun(record.event, record.key, binding)
        self.runs[record.key] = run
        record.status = "running"
        run.task = asyncio.create_task(self.execute_run(run))

    async def execute_run(self, run):
        record = self.event_records[run.key, run.initial_event.id]
        binding = run.binding
        context = binding.context
        run.started = True
        try:
            if run.cancelled:
                raise asyncio.CancelledError
            if self.session_key(context) != record.key:
                raise ValueError("执行会话已变化")
            if binding.on_start is not None:
                binding.on_start(record.event)
            if binding.on_user_event is not None and record.event.type == "user.message":
                binding.on_user_event(record.event)
            take_pending_inputs = partial(run.take_pending_inputs, self.loop)
            with context.execution_guard():
                if run.initial_event.type == "timer.trigger":
                    if binding.timer_executor is None:
                        raise ValueError("目标会话没有定时执行入口")
                    response = await binding.timer_executor(run, context, take_pending_inputs)
                else:
                    response = await context.run_turn(run.initial_event.input, take_pending_inputs,
                                                      try_close_input=run.try_close_input)
            record.response = response
            status = "succeeded"
            if run.cancelled or response is not None and response.finish_reason == "interrupted":
                status = "cancelled"
            elif response is not None and response.finish_reason not in (None, "stop"):
                status = "failed"
            self._finish_event(record, status)
            if response is not None and binding.on_response is not None:
                binding.on_response(run.session_id, response)
        except asyncio.CancelledError:
            context.cancel_current_turn()
            self._finish_event(record, "cancelled")
        except Exception as error:
            self._finish_event(record, "failed", str(error))
        finally:
            with run.lock:
                run.accepting_inputs = False
                settled_ids = list(run.consumed_event_ids)
                settled_ids.extend(event.id for event in run.pending_events)
                run.pending_events.clear()
            for event_id in settled_ids:
                item = self.event_records[run.key, event_id]
                item.response = record.response
                self._finish_event(item, "consumed" if record.status == "succeeded" else record.status, record.error)
            if self.runs.get(record.key) is run:
                del self.runs[record.key]
            if binding.on_finish is not None:
                binding.on_finish(record)
            self._schedule_dispatch(record.key)

    async def _execute_parallel(self, record, binding):
        try:
            await binding.parallel_executor(ActiveRun(record.event, record.key, binding))
            self._finish_event(record, "succeeded")
        except asyncio.CancelledError:
            self._finish_event(record, "cancelled")
        except Exception as error:
            self._finish_event(record, "failed", str(error))

    def _parallel_done(self, key, record, binding, task):
        self.parallel_tasks.get(key, set()).discard(task)
        if task.cancelled() and not record.finished.is_set():
            self._finish_event(record, "cancelled")
        if binding.on_finish is not None:
            binding.on_finish(record)

    def _cancel_run(self, run):
        run.cancelled = True
        run.binding.context.cancel_current_turn()
        if run.started and run.task is not None and not run.task.cancelling():
            run.task.cancel()

    def stop_session(self, key):
        for subagent_key in self.subagent_keys(key):
            self.stop_session(subagent_key)
        router = self.dispatch_tasks.pop(key, None)
        if router is not None:
            router.cancel()
        binding = self.sessions.get(key)
        runtime = self.get_loaded_session(key)
        manager = runtime.background_manager if runtime is not None else None
        pending_job_ids = {event.payload['origin']['job_id'] for event in self.queues.get(key, ())
                           if event.source == 'background'}
        active = self.runs.get(key)
        if active is not None:
            with active.lock:
                pending_job_ids.update(event.payload['origin']['job_id'] for event in active.pending_events
                                       if event.source == 'background')
        if manager is not None:
            for job in manager.list(session_id=key.session_id):
                if job.status != 'running' and job.id not in pending_job_ids:
                    continue
                self.silent_jobs.add((key, job.completion_run_id or manager.run_id, job.id))
                job.on_completed = None
                if job.status == 'running':
                    manager.cancel(job.id, session_id=key.session_id)
        pending = self.queues.get(key, ())
        completions = []
        cancelled = []
        while pending:
            event = pending.popleft()
            item = self.event_records[key, event.id]
            if event.source == 'background':
                completions.append(event)
            else:
                self._finish_event(item, "cancelled")
                cancelled.append(item)
        if completions:
            pending.extend(completions)
        run = self.runs.get(key)
        if run is not None:
            self._cancel_run(run)
            with run.lock:
                for event in run.pending_events:
                    item = self.event_records[key, event.id]
                    if item.event.source == 'background':
                        self.queues[key].append(event)
                        item.status = 'queued'
                    else:
                        self._finish_event(item, "cancelled")
                run.pending_events.clear()
        for task in tuple(self.parallel_tasks.get(key, ())):
            task.cancel()
        if run is None and completions:
            self._schedule_dispatch(key)
        if binding is not None and binding.on_finish is not None:
            for item in cancelled:
                binding.on_finish(item)

    @staticmethod
    def _finish_event(record, status, error=None):
        record.status, record.error = status, error
        record.finished.set()

    def pending_messages(self, key):
        messages = []
        for event in self.queues.get(key, ()):
            item = self.event_records[key, event.id]
            if event.type == 'user.message':
                mode = 'follow_up' if item.queue_only else event.payload.get('mode', 'auto')
                messages.append({'id': event.payload['origin']['message_id'], 'text': event.payload['content']['text'], 'mode': mode})
            elif event.type == 'timer.trigger':
                messages.append({'id': event.id, 'text': '定时任务：' + event.payload['content']['prompt'], 'mode': 'follow_up'})
        return messages

    def busy(self, key):
        return key in self.runs or bool(self.queues.get(key)) or bool(self.parallel_tasks.get(key))

    async def wait_for_event(self, record):
        await record.finished.wait()
        if record.status == "failed":
            raise RuntimeError(record.error or "任务执行失败")
        return record.response

    async def close(self):
        self.closing = True
        if self.preparing_sessions:
            await asyncio.gather(*self.preparing_sessions.values(), return_exceptions=True)
        for binding in self.sessions.values():
            if binding.unsubscribe is not None:
                binding.unsubscribe()
                binding.unsubscribe = None
        tasks = list(self.dispatch_tasks.values())
        for key in tuple(self.sessions):
            self.stop_session(key)
        tasks.extend(run.task for run in self.runs.values() if run.task is not None)
        for parallel in self.parallel_tasks.values():
            tasks.extend(parallel)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        for binding in self.sessions.values():
            if binding.context is not None:
                binding.context.close()
        self.sessions.clear()
