"""One personal runner shared by local Web and IM processes.

The lock owns execution; the loopback capability only bridges local connectors.
Browser clients never receive the capability or write a session log themselves.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4
from raccoon.runtime.model_provider import AuxiliaryModels


from raccoon.application.conversations.parallel_inference import ParallelBranchRunner
from raccoon.application.conversations.auto_routing import accepts_web_auto_event, classify_web_auto_event
from raccoon.modules.events import Event, EventSourceAdapter
from raccoon.application.delivery.attachments import ChatAttachments


from raccoon.application.delivery.main_host import main_host_snapshot

class MainHostUnavailable(RuntimeError):
    pass

class MainHostConflict(ValueError):
    pass

class PersonalChatHost:
    """管理网页和 QQ 共用的个人聊天执行宿主。"""

    def __init__(self, data_root, session_id, queue, bundle, *, chat_attachments: ChatAttachments, lock=None, owns_runtime=True, endpoint=None, deliver_qq_response=None, scheduled_executor=None):
        self.data_root = Path(data_root)
        if (chat_attachments.store.root != self.data_root.absolute()
                or chat_attachments.store.session_id != session_id):
            raise ValueError('附件存储不属于当前主会话')
        self.chat_attachments = chat_attachments
        self.session_id, self.application, self.bundle = session_id, queue, bundle
        self.generation = uuid4().hex
        self.lock, self.owns_runtime = lock, owns_runtime
        self.scheduled_executor = scheduled_executor
        parent_session = bundle.session
        parent_permission = getattr(parent_session, 'permission_manager', None)
        parent_policy = getattr(parent_permission, 'policy', None)
        parent_tool_names = {tool.name for tool in (getattr(bundle, 'tools', None) or [])}
        tools_provider = getattr(bundle, 'tools_provider', None)
        if tools_provider is not None:
            try:
                parent_tool_names |= {tool.name for tool in (tools_provider() or [])}
            except Exception:
                pass
        self.parallel = ParallelBranchRunner(
            session_id=session_id,
            store=parent_session.store,
            project_root=getattr(parent_policy, 'project_root', None) or self.data_root,
            provider=bundle.provider,
            reasoning_effort=getattr(bundle, 'reasoning_effort', None),
            permission_manager=parent_permission,
            sandbox_access=getattr(parent_session, 'sandbox_access', None),
            parent_tool_names=parent_tool_names,
            user_timezone=getattr(parent_session, 'user_timezone', None),
            auxiliary_models=getattr(self.bundle, "auxiliary_models", AuxiliaryModels()),
            provider_resolver=lambda: (self.bundle.provider,
                                       getattr(self.bundle, 'reasoning_effort', None)),
        )
        self.key = self.application.register(bundle, on_response=self._response,
            on_user_event=self._user_event, on_start=self._turn_start, on_finish=self._finished,
            classifier=self._classify_auto, parallel_executor=self.parallel.execute,
            timer_executor=scheduled_executor.execute if scheduled_executor is not None else None)
        self.on_qq_response = None
        self.route = None
        self.background_routes = {}
        self.live = []
        self.activity = {}
        self.run_phase = None
        self.status = 'idle'
        self.endpoint = endpoint
        self.deliver_qq_response = deliver_qq_response
        self.closed = False
        self.handoff_task = None
        self.loop = asyncio.get_running_loop()

    async def start(self):
        runner = self.bundle
        self.previous_tool = getattr(runner, 'tool_event_handler', None)
        self.previous_run_phase = getattr(runner, 'run_phase_handler', None)
        runner.input_event_handler = self._input
        runner.tool_event_handler = self._tool
        runner.run_phase_handler = self._run_phase
        try:
            self.application.start()
            if self.endpoint is not None:
                await self.endpoint.start(self)
        except BaseException:
            await self.close()
            raise

    def _accepts_auto(self, event) -> bool:
        """Only the authenticated personal-web text entry may request auto routing."""
        return accepts_web_auto_event(event, session_id=self.session_id)

    async def _classify_auto(self, event, snapshot):
        if not self._accepts_auto(event):
            return None
        if self.application.pending_inputs.get(self.session_id) is not None:
            # A pending answer must keep its original delivery path, never become an independent task.
            return None
        mode = await classify_web_auto_event(event, snapshot, runner=self.bundle)
        return mode if mode in ("steer", "interrupt", "parallel") else "queue"

    def _user_event(self, event):
        origin = event.payload['origin']
        self.route = (origin['conversation_id'], origin['message_id']) if event.source == 'qq' else None
        self.live = []
        self.activity = {}
        self.status = 'running'

    def _turn_start(self, event):
        if event.type == 'timer.trigger' and event.payload.get('delivery', {}).get('channel') == 'qq':
            self.route = (event.payload['delivery']['target'], '')
        elif event.source == 'background':
            origin = event.payload.get('origin', {})
            self.route = self.background_routes.get((origin.get('run_id'), origin.get('job_id')))
        else:
            self.route = None
        self.live = []
        self.activity = {}
        self.status = 'running'

    def _response(self, session_id, response):
        self.live = []
        self.activity = {}
        self.run_phase = None
        self.status = 'waiting' if response.finish_reason == 'waiting_for_user_input' else 'idle'
        if self.route is None:
            return
        if self.on_qq_response is not None:
            self.on_qq_response(session_id, response, self.route)
            return
        if self.deliver_qq_response is not None:
            self.deliver_qq_response(session_id, response, self.route, self.application)

    def _input(self, request, answer):
        if request is not None and answer is None:
            response = self.bundle._waiting_for_input_response(request)
            self.loop.call_soon_threadsafe(self._response, self.session_id, response)

    def _run_phase(self, phase):
        self.loop.call_soon_threadsafe(setattr, self, 'run_phase', phase)

    def _tool(self, event):
        if self.previous_tool is not None:
            self.previous_tool(event)
        data = getattr(event.result, 'data', {}) if event.result else {}
        job_id = data.get('background_job_id')
        manager = getattr(self.bundle, 'background_manager', None)
        if job_id and manager is not None:
            self.background_routes[(manager.run_id, job_id)] = self.route
        if event.kind == 'background_started' and manager is not None:
            for job in manager.list(session_id=self.session_id):
                if job.tool_call_id == event.tool_call.id:
                    self.background_routes[(manager.run_id, job.id)] = self.route
        payload = {'id': event.tool_call.id, 'name': event.tool_call.name, 'kind': event.kind,
                   'arguments': event.tool_call.arguments, 'ok': event.result.ok if event.result else None,
                   'content': event.result.content[:12000] if event.result else ''}
        self.loop.call_soon_threadsafe(self.activity.__setitem__, payload['id'], payload)

    def snapshot(self):
        return main_host_snapshot(self, self.application.history(self.key))

    def guard(self, body):
        if body.get('session_id') != self.session_id or body.get('generation') != self.generation:
            raise MainHostConflict('主会话宿主已更换，请重新同步后再提交。')

    def attachment_store(self):
        return self.chat_attachments

    def require_idle(self):
        if self.application.busy(self.key) or self.bundle.last_pending_input is not None:
            raise MainHostConflict('请先完成当前任务或处理待回答请求。')


    async def dispatch(self, operation, body=None):
        if self.closed:
            raise MainHostUnavailable('主会话正在转交给 QQ，请重新连接。')
        body = body or {}
        if operation == 'snapshot':
            return self.snapshot()
        self.guard(body)
        if operation in {'scheduled', 'scheduled_answer'}:
            if self.scheduled_executor is None:
                raise MainHostUnavailable('主会话定时执行尚未启用。')
            run_id = body.get('run_id')
            if not isinstance(run_id, str) or not run_id or len(run_id) > 200:
                raise ValueError('无效定时执行标识')
            executor = self.scheduled_executor
            run = executor.store.get_run(run_id)
            task = executor.store.get(run['task_id'])
            if task['origin_session'] != self.session_id:
                raise MainHostConflict('任务来源不是当前会话')
            if task.get('execution_owner') != 'personal-main' and task['channel'] != 'qq':
                raise MainHostConflict('任务未绑定当前宿主')
            return await executor.dispatch(operation, body)
        if operation in {'subagents_list', 'subagents_detail', 'subagents_send', 'subagents_stop'}:
            from raccoon.application.conversations.subagents import control_subagent
            from raccoon.application.delivery.subagents import subagent_snapshot
            runner = self.bundle
            if operation in {'subagents_list', 'subagents_detail'}:
                return subagent_snapshot(runner, subagent_id=body.get('subagent_id') if operation == 'subagents_detail' else None)
            kwargs = {key: body.get(key) for key in ('message', 'mode', 'event_id')} if operation == 'subagents_send' else {'stop': True}
            return await control_subagent(runner, body.get('subagent_id'), **kwargs)
        if operation == 'attachment_guard':
            self.require_idle()
        elif operation in {'model', 'mode', 'effort'}:
            self.require_idle()
            # Use the same session lease as scheduled work; never switch the project runner.
            with self.bundle.execution_guard():
                if operation == 'model':
                    model = body.get('model')
                    if not isinstance(model, str) or not model.strip() or len(model) > 200:
                        raise ValueError('请选择有效模型')
                    self.bundle.switch_model(model.strip())
                elif operation == 'effort':
                    self.bundle.set_effort(body.get('effort'))
                else:
                    self.bundle.session.set_permission_mode(body.get('mode'))
        elif operation == 'handoff':
            if self.owns_runtime and self.handoff_task is None:
                self.handoff_task = asyncio.create_task(self._handoff())
        elif operation == 'message':
            text = body.get('message')
            message_id = body.get('message_id')
            mode = body.get('mode', 'steer')
            if mode not in {'steer', 'follow_up', 'auto'}:
                raise ValueError('消息模式无效')
            identities = body.get('attachment_ids', [])
            if not isinstance(identities, list) or any(not isinstance(item, str) for item in identities):
                raise ValueError('附件 ID 无效')
            if not isinstance(text, str) or (not text.strip() and not identities) or len(text) > 100000:
                raise ValueError('消息不能为空且不能超过 100000 字符')
            if not isinstance(message_id, str) or not message_id or len(message_id) > 200:
                raise ValueError('消息缺少有效 ID')
            pending = self.application.pending_inputs.get(self.session_id)
            if pending and pending.kind == 'permission_confirmation' and not self.application.timer_active(self.session_id):
                raise MainHostConflict('请先处理当前权限请求。')
            refs = self.attachment_store().store.staged_refs(identities)
            if any(ref.kind == 'image' for ref in refs) and not self.application.accepts_images(self.session_id):
                raise ValueError('当前模型不支持图片输入，请切换支持图片的模型后重试')
            event = EventSourceAdapter(main_session_id=self.session_id).user_message(
                source='web', message_id=message_id, conversation_id='personal-web', text=text, attachments=refs)
            event.payload['mode'] = mode
            self.application.submit_event(event, self.key)
        elif operation == 'event':
            event = Event.from_dict(body['event'])
            if event.source != 'qq' or event.session_id != self.session_id:
                raise ValueError('主会话仅接收绑定 QQ 的本地消息')
            self.application.submit_event(event, self.key)
        elif operation == 'input':
            pending = self.application.pending_inputs.get(self.session_id)
            if pending is None or pending.id != body.get('request_id'):
                raise MainHostConflict('待回答请求已经失效。')
            answer = body.get('answer')
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 100000:
                raise ValueError('回答不能为空')
            if pending.kind == 'permission_confirmation' and answer not in {option.id for option in pending.options}:
                raise ValueError('请选择有效权限选项')
            self.application.answer_run(self.session_id, pending.id, answer)
        elif operation == 'cancel':
            active = self.application.runs.get(self.key)
            event = Event(id=uuid4().hex, type='run.cancel', source='local', session_id=self.session_id,
                          payload={'run_id': active.initial_event.id if active else None})
            self.application.submit_event(event, self.key)
            self.status = 'stopping' if self.application.busy(self.key) else 'interrupted'

        else:
            raise ValueError('未知主会话操作')
        return self.snapshot()


    def _finished(self, receipt):
        if receipt.event.type == 'timer.trigger' and self.scheduled_executor is not None:
            self.scheduled_executor.store.finish_unstarted(receipt)
        self.status = 'interrupted' if receipt.status == 'cancelled' else 'error' if receipt.status == 'failed' else 'idle'
        self.live = []
        self.activity = {}
        self.run_phase = None

    async def _handoff(self):
        # Finish the existing turn, including human input, before changing owners.
        while not self.closed:
            await asyncio.sleep(.1)
            manager = getattr(self.bundle, 'background_manager', None)
            if manager is not None and not manager.wait(timeout=0):
                continue
            if self.application.busy(self.key):
                continue
            if self.endpoint is not None:
                self.endpoint.reserve_handoff()
            await self.close()
            return

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.handoff_task is not None and self.handoff_task is not asyncio.current_task():
            self.handoff_task.cancel()
            await asyncio.gather(self.handoff_task, return_exceptions=True)
        if self.endpoint is not None:
            await self.endpoint.stop()
        try:
            if not self.owns_runtime and self.scheduled_executor is not None:
                await self.application.close()
            if self.owns_runtime:
                try:
                    await self.application.close()
                finally:
                    await asyncio.to_thread(self.bundle.close)
        finally:
            try:
                if self.scheduled_executor is not None:
                    self.scheduled_executor.close()
            finally:
                runner = self.bundle
                if getattr(runner, 'run_phase_handler', None) == self._run_phase:
                    runner.run_phase_handler = self.previous_run_phase
                if self.endpoint is not None:
                    self.endpoint.cleanup(generation=self.generation, handoff=self.handoff_task is asyncio.current_task())
                if self.lock is not None:
                    self.lock.release()
