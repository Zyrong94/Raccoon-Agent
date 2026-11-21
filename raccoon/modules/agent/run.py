"""Agent 主循环最小闭环。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, replace
from typing import Literal

import anyio

from raccoon.runtime.cancellation import AgentCancelledError, CancellationToken
from raccoon.runtime.user_input import UserInputRequest
from raccoon.runtime import CompletionInput, MessageInput, TimerInput, SubagentInput
from raccoon.modules.agent.loop_limits import AgentLoopLimits, AgentLoopStopReason
from raccoon.modules.agent.runtime_capabilities import AgentRuntimeCapabilities, PLANNING_TOOL_NAMES
from raccoon.modules.agent.session import AgentSession
from raccoon.modules.agent.telemetry import AgentTurnTelemetry
from raccoon.modules.agent.task_plan_policy import final_reconciliation_instruction
from raccoon.modules.agent.status_bar import render_agent_status
from raccoon.modules.context.compaction import effective_messages
from raccoon.modules.agent import tool_execution
from raccoon.modules.agent.tool_execution import ToolExecutionEvent
from raccoon.modules.agent.tool_settlement import append_interrupted_tail
from raccoon.modules.tools.background_controls import DEFAULT_BACKGROUND_TOOL_NAMES, with_background_controls, with_task_handle_control
from raccoon.runtime.background import BackgroundJobManager, BackgroundNotification
from raccoon.modules.agent.background_results import render_task_notification
from raccoon.modules.context.context_builder import ContextBuilder
from raccoon.modules.context.compaction import CompactionSettings, prepare_compaction, generate_compaction
from raccoon.modules.context.summary import SummaryError
from raccoon.modules.context.token_budget import ContextBudget, build_context_budget
from raccoon.modules.sessions.user_attachments import UserAttachment, resolve_queued_attachments
from raccoon.modules.permissions.types import PermissionRequest
from raccoon.runtime.model_provider import AuxiliaryModels, ChatProvider
from raccoon.runtime.model_errors import ProviderError
from raccoon.runtime.model_types import ChatRequest, ChatResponse, MainRequestOptions, ToolCall
from raccoon.modules.tools.types import Tool, ToolResult, make_error_result, make_text_result


AgentRunPhase = Literal["thinking", "responding", "waiting_tools"]


@dataclass(frozen=True, slots=True)
class PreparedMainRequest:
    request: ChatRequest
    tool_result_part_ids: tuple[str, ...]
    background_result_part_ids: tuple[str, ...] = ()


class AgentRun:
    """把用户输入、上下文投影、provider 调用和工具执行串成一轮会话。

    可以把这一层理解成 Raccoon 的“单轮事务”：

    1. 先把用户输入写入 append-only session log。
    2. 从 session log 重建当前视图，投影成 provider messages。
    3. 调用模型。如果模型返回普通文本，就写入 assistant 消息并结束。
    4. 如果模型返回 tool_calls，就先写入 assistant tool_call，再执行工具。
    5. 工具结果写成 role=tool 消息后，再次调用模型，让模型基于工具结果继续回答。

    这里故意不把具体工具、OpenAI SDK chunk、Textual widget 混进来。AgentRun 只协调
    “模型想做什么”和“会话事实应该怎样落库”，具体协议转换交给 provider/context 层。
    """

    def __init__(
        self,
        *,
        session: AgentSession,
        application=None,
        provider: ChatProvider,
        tools: list[Tool] | None = None,
        tools_provider: Callable[[], list[Tool]] | None = None,
        tool_catalog_status: Callable[[], str] | None = None,
        context_builder: ContextBuilder | None = None,
        compaction_settings: CompactionSettings | None = None,
        limits: AgentLoopLimits | None = None,
        clock=time.monotonic,
        response_handler: Callable[[ChatResponse], None] | None = None,
        request_input: Callable[[UserInputRequest], Awaitable[str]] | None = None,
        tool_event_handler: Callable[[ToolExecutionEvent], None] | None = None,
        run_phase_handler: Callable[[AgentRunPhase | None], None] | None = None,
        cancellation_token: CancellationToken | None = None,
        request_options: MainRequestOptions | None = None,
        auxiliary_models: AuxiliaryModels = AuxiliaryModels(),
        routing_timeout_seconds: float | None = None,
        context_window: int | None = None,
        background_manager: BackgroundJobManager | None = None,
        background_tool_names: frozenset[str] | None = None,
        enable_delegate_tool: bool | None = None,
        runtime_capabilities: AgentRuntimeCapabilities | None = None,
        memory_service=None,
        initial_input=None,
        take_inputs=None,
        native_async_tools: bool = False,
    ) -> None:
        self.application = application
        self.pending_input = None
        self._input_future = None
        from threading import Lock
        self._input_lock = Lock()
        self.session = session
        self.tools_provider = tools_provider
        self.tool_catalog_status = tool_catalog_status
        self.initial_input = initial_input
        self.take_inputs = take_inputs
        self.memory_service = memory_service if session.subagent_config is None else None
        self._memory_user_text = ""
        self._memory_message_id = None
        self._memory_submitted = False
        self.provider = provider
        if native_async_tools and (background_manager is None or not getattr(getattr(provider, "capabilities", None), "supports_async_tools", False)):
            raise ValueError("native async tools require a background manager and an async-capable provider")
        self.native_async_tools = native_async_tools
        self._projected_native_completions: dict[str, dict[str, str]] = {}
        self._native_calls_started: set[str] = set()
        self.request_options = request_options or MainRequestOptions()
        self.auxiliary_models = auxiliary_models
        self.routing_timeout_seconds = routing_timeout_seconds
        self.context_window = context_window
        self.context_builder = context_builder or ContextBuilder()
        self.compaction_settings = compaction_settings or CompactionSettings()
        configured_limits = AgentLoopLimits.default()
        capability_config = session.subagent_config
        if capability_config is not None:
            configured_limits = replace(configured_limits, **{
                key: getattr(capability_config, key)
                for key in ("max_tool_rounds", "max_provider_calls", "max_turn_seconds")
                if getattr(capability_config, key) is not None
            })
        self.limits = limits or configured_limits
        self.clock = clock
        self.turn_started_at: float | None = None
        self.response_handler = response_handler
        self.request_input = request_input
        self.tool_event_handler = tool_event_handler
        self.run_phase_handler = run_phase_handler
        self.cancellation_token = cancellation_token
        self.background_manager = background_manager
        self.runtime_capabilities = runtime_capabilities or AgentRuntimeCapabilities.interactive()
        configured_background_names = self.runtime_capabilities.background_tool_names
        self.background_tool_names = (
            background_tool_names
            if background_tool_names is not None
            else configured_background_names or DEFAULT_BACKGROUND_TOOL_NAMES
        )
        self.enable_delegate_tool = (
            self.runtime_capabilities.enable_delegate_tool
            if enable_delegate_tool is None
            else enable_delegate_tool
        )
        self._task_plan_reconciliation_attempted = False
        self.turn_telemetry = AgentTurnTelemetry()
        # session 创建时通常已经注册了 session-scoped 工具。这里允许调用方再传入一批
        # 测试或临时工具，但避免重复注册同名工具导致模型 schema 不稳定。
        if tools is not None:
            self.session.tool_registry.replace_supplied_tools(tools)
        self._background_alias_scope = None
        from raccoon.bootstrap.tools import register_runtime_tools
        register_runtime_tools(self)

    async def wait_for_input(
        self,
        request: UserInputRequest,
        token: CancellationToken | None,
        notify: Callable[[UserInputRequest | None, str | None], None],
    ) -> str:
        """只保存当前进程的交互等待。原工具参数和 review 留在调用栈。"""
        future = asyncio.get_running_loop().create_future()
        with self._input_lock:
            if self.pending_input is not None:
                raise ValueError("当前会话已有待回答请求")
            self.pending_input = request
            self._input_future = future
        try:
            await asyncio.to_thread(notify, request, None)
            while not future.done():
                if token is not None:
                    token.raise_if_cancelled()
                await asyncio.wait({future}, timeout=0.1)
            if token is not None:
                token.raise_if_cancelled()
            answer = future.result()
            await asyncio.to_thread(notify, request, answer)
            return answer
        finally:
            with self._input_lock:
                self.pending_input = None
                self._input_future = None
                if not future.done():
                    future.cancel()
            await asyncio.to_thread(notify, None, None)


    def answer_input(self, request_id: str, answer: str) -> None:
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("回答不能为空")
        with self._input_lock:
            request, future = self.pending_input, self._input_future
            if request is None or request.id != request_id or future is None or future.done():
                raise ValueError("该请求已失效，请刷新后处理当前请求")
            self._input_future = None  # 接受一次；重复点击不能再次批准。

            def complete():
                if not future.done():
                    future.set_result(answer)

            future.get_loop().call_soon_threadsafe(complete)


    def _validate_attachments(self, attachments: list[UserAttachment] | None) -> None:
        """在消息落库前确认 provider 能消费本轮附件。

        文件附件可以继续以内联文本或路径引用进入上下文；图片则必须由当前模型显式声明
        视觉能力，避免 provider 静默忽略图片后让 agent 在错误前提下执行任务。
        """

        if not attachments or not any(item.kind == "image" for item in attachments):
            return
        capabilities = getattr(self.provider, "capabilities", None)
        if bool(getattr(capabilities, "supports_vision", False)):
            return
        raise ValueError(
            f"模型 {self.provider.name}/{self.provider.model} 未声明视觉能力，"
            "无法发送图片附件；请在对应 [models] 配置中设置 vision = true。"
        )


    def _main_chat_request(self, messages, definitions, tool_choice) -> ChatRequest:
        return ChatRequest(
            messages=messages,
            tools=definitions,
            tool_choice=tool_choice,
            **self.request_options.as_chat_request_kwargs(),
        )

    async def request_model_response(
        self,
        *,
        tool_choice="auto",
        runtime_instruction: str | None = None,
    ) -> ChatResponse:
        """准备请求并取得模型响应；必要时压缩后重试，不保存助手响应。"""
        prepared = await self._prepare_main_provider_request(
            tool_choice=tool_choice,
            runtime_instruction=runtime_instruction,
        )
        self._reserve_provider_call()
        self._check_turn_timeout()
        self._check_cancelled()
        with self._report_thinking():
            try:
                response = await self._await_model_response(prepared.request)
            except ProviderError as error:
                if not error.requires_compaction or not self.compaction_settings.enabled:
                    raise
                record = await self.compact_context(reason="overflow")
                if record is None:
                    raise
                self._append_status_if_changed(runtime_instruction)
                view = self.session.data
                messages = self._request_messages(view=view)
                request = self._main_chat_request(messages, self._provider_tool_definitions(), tool_choice)
                self._reserve_provider_call()
                response = await self._await_model_response(request)
                prepared = replace(prepared, request=request,
                    tool_result_part_ids=self.context_builder.projected_tool_result_part_ids(view),
                    background_result_part_ids=self._pending_background_part_ids(view))
        self._check_cancelled()
        if response.finish_reason in (None, "stop", "tool_calls"):
            self._record_background_receipt(prepared)
        self._record_native_completion_delivery(prepared)
        return response

    async def _await_model_response(self, request: ChatRequest) -> ChatResponse:
        """等待网络请求时仍检查跨线程的停止信号和本轮时间上限。"""
        request_task = asyncio.create_task(self.provider.acomplete(request))
        try:
            while not request_task.done():
                await asyncio.wait({request_task}, timeout=0.1)
                self._check_cancelled()
                self._check_turn_timeout()
            return await request_task
        finally:
            if not request_task.done():
                request_task.cancel()
                with suppress(asyncio.CancelledError):
                    await request_task

    @contextmanager
    def _report_thinking(self):
        callback = self.run_phase_handler
        if callback is not None:
            callback("thinking")
        try:
            yield
        finally:
            if callback is not None:
                callback(None)

    def _publish_response(self, response: ChatResponse) -> None:
        if self.response_handler is not None:
            self.response_handler(response)


    def finish_turn(self, response: ChatResponse) -> ChatResponse:
        """保存最终响应、发布展示通知并记录本轮结束状态。"""
        self.session.store.append_assistant_response(self.session.data,
            response, native_completions=list(self._projected_native_completions.values())
            if response.finish_reason in (None, "stop") and not response.tool_calls else None,
         scheduled_run_id=self.session.scheduled_run_id)
        self._publish_response(response)
        status = "completed"
        if response.finish_reason == "interrupted":
            status = "interrupted"
        elif response.finish_reason == "length" or response.finish_reason in {
            reason.value for reason in AgentLoopStopReason
        }:
            status = "limited"
        elif response.finish_reason in {"error", "content_filter"}:
            status = "errored"
        self._persist_turn_telemetry(
            status=status,
            stop_reason=response.finish_reason or "completed",
            finalize=True,
        )
        if status == "completed" and self.memory_service is not None and self._memory_message_id and not self._memory_submitted:
            self._memory_submitted = True
            try:
                self.memory_service.submit_turn(
                    self._memory_user_text, response.content,
                    session_id=self.session.session_id, message_id=self._memory_message_id,
                    project_root=str(self.session.thread_data.get("workspace_path") or ""),
                )
            except Exception:
                pass  # Optional memory must not invalidate an already persisted answer.
        return response

    def _retrieve_memory(self, content, message_id):
        if self._memory_user_text:
            self._memory_user_text += "\n" + content
        else:
            self._memory_user_text = content
            self._memory_message_id = message_id
        self._memory_submitted = False
        if self.memory_service is None:
            return
        self.session.store.append_session_metadata_updated(self.session.data,
            memory_turn_message_id=self._memory_message_id, memory_turn_eligible=True,
        )
        message = next(item for item in self.session.data.messages if item.id == message_id)
        part = next(item for item in message.parts if item.kind == "text")
        # A replayed source event must keep exactly the reference originally sent.
        if part.metadata.get("memory_retrieval_done"):
            return
        memory = ""
        try:
            memory = self.memory_service.search(content)
        except Exception:
            pass  # Optional memory must not prevent the user input from running.
        self.session.store.append_message_part_metadata_updated(self.session.data,
            message_id=message_id, part_id=part.id,
            metadata={"memory_retrieval_done": True, "recalled_memory": memory},
        )


    def _final_reconciliation_instruction(self) -> str | None:
        if self._task_plan_reconciliation_attempted:
            return None
        instruction = final_reconciliation_instruction(self.session.data.task_plan)
        if instruction is None:
            return None
        self._task_plan_reconciliation_attempted = True
        return instruction


    def _append_interrupted_tool_results(self) -> None:
        for settlement in append_interrupted_tail(self.session):
            self._emit_tool_event("interrupted", settlement.tool_call, result=settlement.result)

    def _repair_interrupted_tool_calls_before_provider_request(self) -> None:
        if self.pending_input is None:
            self._append_interrupted_tool_results()

    def _emit_tool_event(
        self,
        kind: Literal[
            "prewrite_review",
            "started",
            "finished",
            "permission_requested",
            "denied",
            "skipped",
            "interrupted",
            "background_started",
        ],
        tool_call: ToolCall,
        *,
        result: ToolResult | None = None,
        permission_request: PermissionRequest | None = None,
        prewrite_review: dict[str, object] | None = None,
    ) -> None:
        if (self.native_async_tools and kind == "background_started"
                and tool_call.async_execution and tool_call.name != "delegate"):
            self._native_calls_started.add(tool_call.id)
        if self.tool_event_handler is None:
            return
        self.tool_event_handler(
            ToolExecutionEvent(
                kind=kind,
                tool_call=tool_call,
                result=result,
                permission_request=permission_request,
                prewrite_review=prewrite_review,
            )
        )

    async def _prepare_main_provider_request(
        self,
        *,
        tool_choice="auto",
        runtime_instruction: str | None = None,
    ) -> PreparedMainRequest:
        self._repair_interrupted_tool_calls_before_provider_request()
        self._check_cancelled()
        self._append_incoming_messages()
        self._append_background_notifications()
        definitions = self._provider_tool_definitions()
        self._append_status_if_changed(runtime_instruction)
        view = self.session.data
        budget = self._context_budget_for_view(view, definitions=definitions)
        if self.compaction_settings.enabled and budget.input_tokens > budget.context_window - self.compaction_settings.reserve_tokens:
            await self.compact_context(reason="threshold")
            self._append_status_if_changed(runtime_instruction)
            view = self.session.data

        messages = self._request_messages(view=view)
        request = self._main_chat_request(messages, definitions, tool_choice)
        return PreparedMainRequest(
            request=request,
            tool_result_part_ids=self.context_builder.projected_tool_result_part_ids(view),
            background_result_part_ids=self._pending_background_part_ids(view),
        )

    def _append_incoming_messages(self) -> None:
        if self.take_inputs is None or self.pending_input is not None:
            return
        for message in self.take_inputs():
            self._check_cancelled()
            if isinstance(message, CompletionInput):
                self._append_queued_background_result(message)
            elif isinstance(message, SubagentInput):
                self.session.store.append_user_message(
                    self.session.data,
                    message.text,
                    source_event_id=message.event_id,
                    subagent_parent_id=message.parent_session_id,
                    user_timezone=self.session.user_timezone,
                    scheduled_run_id=self.session.scheduled_run_id,
                )
            elif isinstance(message, MessageInput):
                attachments = self._queued_attachments(message)
                message_id = self.session.store.append_user_message(
                    self.session.data,
                    message.text,
                    attachments=attachments,
                    source_event_id=message.event_id,
                    execution_settings=asdict(message.settings) if message.settings is not None else None,
                    user_timezone=self.session.user_timezone,
                    scheduled_run_id=self.session.scheduled_run_id,
                )
                self._retrieve_memory(message.text, message_id)
            else:
                raise TypeError("unsupported inbox input")
        self._check_cancelled()

    def _initial_is_background(self) -> bool:
        return self.initial_input is not None and isinstance(self.initial_input, (CompletionInput, TimerInput, SubagentInput))

    def _queued_attachments(self, message: MessageInput) -> list[UserAttachment]:
        attachments = resolve_queued_attachments(
            message.attachments, store_root=self.session.store.root, session_id=self.session.session_id,
        )
        self._validate_attachments(attachments)
        return attachments

    def _append_initial_input(self) -> str:
        message = self.initial_input
        if isinstance(message, CompletionInput):
            identity = self._append_queued_background_result(message)
            self.session.store.append_session_metadata_updated(self.session.data, memory_turn_eligible=False)
            return identity
        metadata = {'source_event_id': message.event_id}
        attachments = None
        if isinstance(message, MessageInput):
            attachments = self._queued_attachments(message)
            if message.settings is not None:
                metadata['execution_settings'] = asdict(message.settings)
        elif isinstance(message, TimerInput):
            metadata['scheduled_task_id'] = message.task_id
        elif isinstance(message, SubagentInput):
            metadata['subagent_parent_id'] = message.parent_session_id
        else:
            raise TypeError('未知运行输入')
        identity = self.session.store.append_user_message(self.session.data, message.text,
            attachments=attachments, user_timezone=self.session.user_timezone,
            scheduled_run_id=self.session.scheduled_run_id, **metadata)
        if isinstance(message, MessageInput):
            self._retrieve_memory(message.text, identity)
        else:
            self.session.store.append_session_metadata_updated(self.session.data, memory_turn_eligible=False)
        return identity

    def _append_queued_background_result(self, message: CompletionInput) -> str:
        if message.tool_call_id is not None:
            from raccoon.modules.agent.native_async import consume_native_result
            return consume_native_result(
                self.session, message, self.background_manager,
                observe=self._observe_tool_result, emit=self._emit_tool_event,
            )
        existing = self.session.store.background_notification_message_id(self.session.data, message.event_id, job_id=message.job_id, run_id=message.run_id)
        if existing is not None:
            return existing
        fields = asdict(message)
        fields.pop("event_id")
        notification = BackgroundNotification(**fields, session_id=self.session.session_id, ok=message.status == "completed")
        if self.background_manager is not None and self.background_manager.owns_completion(run_id=message.run_id, job_id=message.job_id, session_id=self.session.session_id):
            notification = self.background_manager.finalize_completed(message.job_id, session_id=self.session.session_id)
            if notification is None:
                raise ValueError("completion has no finished job in its owning runtime")
        return self.session.store.append_background_notification(self.session.data,
            content=render_task_notification(notification), job_id=message.job_id,
            tool_name=notification.tool_name, status=notification.status,
            task_id=notification.task_id, observed_revision=notification.observed_revision,
            source_event_id=message.event_id, run_id=message.run_id,
        )

    def _record_native_completion_delivery(self, prepared: PreparedMainRequest) -> None:
        projected = set(prepared.tool_result_part_ids)
        for item in self.session.data.messages:
            for part in item.parts:
                if part.kind == "tool_result" and part.id in projected and part.metadata.get("background_run_id"):
                    identity = {"tool_call_id": part.metadata["tool_call_id"],
                                "run_id": part.metadata["background_run_id"],
                                "event_id": part.metadata["source_event_id"]}
                    self._projected_native_completions[part.id] = identity

    def _context_budget_for_view(
        self,
        view,
        *,
        definitions,
    ) -> ContextBudget:
        messages = self._request_messages(view=view)
        return build_context_budget(
            messages=messages,
            tools=definitions,
            context_window=self.context_window,
            max_output_tokens=self.request_options.max_tokens,
            history_overhead_tokens=self.provider.estimate_replay_tokens(messages),
        )

    def context_budget_for_view(self, view) -> ContextBudget:
        return self._context_budget_for_view(
            view,
            definitions=self._provider_tool_definitions(),
        )

    async def compact_context(self, *, reason: str = "manual", instructions: str = ""):
        """Pi lifecycle: report automatic failures; retry overflow only after success."""
        view = self.session.data
        budget = self.context_budget_for_view(view)
        preparation = prepare_compaction(view, self.compaction_settings, budget.input_tokens)
        if preparation is None:
            return None
        if self.auxiliary_models.context_summary is None:
            if reason == "threshold":
                return None
            raise SummaryError("上下文摘要需要配置 .env 中的 RACCOON_CONTEXT_SUMMARY_*")
        self.session.store.append_event(self.session.data, "compaction_started", {"reason": reason})
        task = asyncio.create_task(generate_compaction(
            preparation, session_id=view.session_id, provider=self.auxiliary_models.context_summary,
            request_options=MainRequestOptions(), instructions=instructions,
        ))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.1)
                self._check_cancelled()
            record = await task
            self._check_cancelled()
            # Validate the same provider projection used on the next request.
            candidate = replace(view, compactions=[*view.compactions, record])
            self._build_provider_messages(candidate, system_prefix=[])
            self.session.store.append_event(self.session.data, "context_compacted", record.to_dict())
            return record
        except (ProviderError, SummaryError, ValueError) as error:
            self.session.store.append_event(self.session.data, "compaction_failed", {"reason": reason, "error": str(error)})
            if reason == "manual":
                raise
            # Pi reports a failed threshold compaction and returns to its caller.
            # No extra "does the original request still fit" decision is made.
            self._publish_response(ChatResponse(
                provider=self.provider.name, model=self.provider.model,
                content=f"上下文压缩失败：{error}", finish_reason="error",
            ))
            return None
        except (asyncio.CancelledError, AgentCancelledError):
            self.session.store.append_event(self.session.data, "compaction_failed", {"reason": reason, "aborted": True})
            raise
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    def _build_provider_messages(self, view, *, system_prefix):
        if not getattr(getattr(self.provider, "capabilities", None), "supports_async_tools", False) and any(
            part.kind == "tool_call" and part.metadata.get("async_execution")
            for message in view.messages for part in message.parts
        ):
            raise ValueError("session contains native async tool history; select an async-capable provider")
        messages = self.context_builder.build_provider_messages(
            view,
            system_prefix=system_prefix,
            store_root=self.session.store.root,
            supports_vision=bool(getattr(getattr(self.provider, "capabilities", None), "supports_vision", False)),
            supports_pdf_input=bool(getattr(getattr(self.provider, "capabilities", None), "supports_pdf_input", False)),
        )
        return messages

    def _append_status_if_changed(self, instruction=None):
        view = self.session.data
        content = render_agent_status(view, self.background_manager, instruction)
        latest = next((message for message in reversed(effective_messages(view))
                       if message.metadata.get("source_kind") == "agent_status"), None)
        if latest is not None and latest.parts[0].content == content:
            return
        self.session.store.append_agent_status(self.session.data, content)

    def _pending_background_part_ids(self, view):
        return tuple(part.id for message in effective_messages(view) for part in message.parts
                     if part.metadata.get("background_delivery") == "pending")

    def _record_background_receipt(self, prepared):
        # Record only results included in this request, not completions arriving during it.
        received = set(prepared.background_result_part_ids)
        for message in self.session.data.messages:
            for part in message.parts:
                if part.id in received:
                    self.session.store.append_message_part_metadata_updated(self.session.data,
                        message_id=message.id, part_id=part.id,
                        metadata={"background_delivery": "received"},
                    )

    def _request_messages(self, *, view=None):
        had_pending = bool(self.session.pending_tool_definitions)
        self.session.flush_tool_definitions()
        resolved_view = self.session.data if had_pending or view is None else view
        system_prefix = self.session.build_system_prefix()
        return self._build_provider_messages(
            resolved_view,
            system_prefix=system_prefix,
        )

    def _provider_tool_definitions(self):
        """基础工具按固定名称顺序提供，扩展定义留在加载时的历史位置。"""
        registry = self.session.tool_registry
        return sorted(
            [definition for definition in self._discoverable_tool_definitions()
             if registry.get(definition.name).exposure == "direct"],
            key=lambda definition: definition.name,
        )

    def _discoverable_tool_definitions(self):
        if self.tools_provider is not None:
            self.session.tool_registry.replace_supplied_tools(self.tools_provider())
        capabilities = getattr(self.provider, "capabilities", None)
        if capabilities is not None and not capabilities.supports_tools:
            return []
        definitions = []
        for definition in self.session.tool_registry.definitions():
            allowed = self.session.allowed_tool_names
            if allowed is not None and definition.name not in allowed:
                continue
            if definition.name in PLANNING_TOOL_NAMES and not self.runtime_capabilities.expose_planning_tools:
                continue
            if self._runtime_tool_visibility_error(ToolCall("catalog", definition.name, {})) is not None:
                continue
            definitions.append(self._augment_tool_definition(definition))
        return definitions

    def _augment_tool_definition(self, definition):
        """给后台可用工具的 schema 附加 run_in_background/background_label 控制字段。

        只有在启用了 background_manager 且工具在允许列表里时才增强，避免向模型暴露它
        无法真正使用的控制字段。
        """

        if self.background_manager is None or not self.session.allow_background_execution:
            return definition
        if definition.name not in self.background_tool_names:
            return definition
        if self.native_async_tools:
            return replace(with_task_handle_control(definition), async_execution=True)
        return with_background_controls(definition)


    def _begin_turn(self) -> None:
        self._projected_native_completions.clear()
        self._native_calls_started.clear()
        self._memory_message_id = None
        self._memory_user_text = ""
        self._memory_submitted = False
        self.turn_started_at = self.clock()
        self._task_plan_reconciliation_attempted = False
        self.turn_telemetry.begin()

    def _validate_tool_call(self, tool_call: ToolCall, *, loaded_definitions=None) -> ToolResult | None:
        error = self.session.tool_authorization_error(tool_call)
        if error is not None:
            return error
        catalog = {definition.name: definition for definition in self._discoverable_tool_definitions()}
        definition = catalog.get(tool_call.name)
        if definition is None:
            return make_error_result(tool_call.name, "Tool is unavailable in the current runtime.", tool_not_routed=True)
        if tool_call.async_execution and (not self.session.allow_background_execution or not self.native_async_tools or not definition.async_execution):
            return make_error_result(tool_call.name, "Tool does not support async execution in the current runtime.", tool_not_routed=True)
        tool = self.session.tool_registry.get(tool_call.name)
        loaded = self.session.loaded_tool_definitions() if loaded_definitions is None else loaded_definitions
        if tool.exposure == "deferred" and loaded.get(tool_call.name) != definition:
            return make_error_result(tool_call.name, "Tool is not loaded. Call tool_search first.", tool_activation_required=True)
        from raccoon.modules.tools.execution import validate_arguments
        return validate_arguments(tool, tool_call.arguments)

    def _observe_tool_result(self, tool_call: ToolCall, result: ToolResult) -> None:
        self.turn_telemetry.observe_tool_result(
            tool_call,
            result,
            elapsed_seconds=self._turn_elapsed_seconds(),
        )

    def _runtime_tool_visibility_error(self, tool_call: ToolCall) -> ToolResult | None:
        if tool_call.name == "view_image" and not bool(getattr(getattr(self.provider, "capabilities", None), "supports_vision", False)):
            return make_error_result(tool_call.name, "当前模型未声明视觉能力，无法查看图片。", tool_not_routed=True)
        if tool_call.name == "think" and not self.runtime_capabilities.expose_think_tool:
            return make_error_result(
                tool_call.name,
                "当前运行模式未暴露 think 工具。",
                tool_not_routed=True,
            )
        if tool_call.name == "ask_user" and not self.runtime_capabilities.allow_user_input:
            return make_error_result(
                tool_call.name,
                "当前运行模式未启用 ask_user。",
                tool_not_routed=True,
            )
        if tool_call.name == "web_search" and not self.runtime_capabilities.expose_web_search_tool:
            return make_error_result(
                tool_call.name,
                "当前运行模式未启用 web_search。",
                tool_not_routed=True,
            )
        if (
            tool_call.name in PLANNING_TOOL_NAMES
            and not self.runtime_capabilities.expose_planning_tools
        ):
            return make_error_result(
                tool_call.name,
                "当前任务被路由为简单单任务，未暴露 TaskPlan 工具；请直接完成并验证目标。",
                tool_not_routed=True,
            )
        if tool_call.name == "delegate" and not self.enable_delegate_tool:
            return make_error_result(
                tool_call.name,
                "当前任务未启用子代理；请使用主代理工具直接完成。",
                tool_not_routed=True,
            )
        return None


    def _append_background_notifications(self) -> None:
        """Drain finished background jobs into the history before calling the provider.

        Each completion becomes one independent ``background_notification`` user
        message.  It never reuses the original ``tool_call_id``, so the provider
        still sees exactly one tool result per assistant tool call.
        """

        if self.background_manager is None:
            return
        for notification in self.background_manager.collect_completed(session_id=self.session.session_id):
            if notification.kind == 'subagent':
                from raccoon.modules.events import EventSourceAdapter
                fields = asdict(notification)
                fields.pop('session_id')
                fields.pop('ok')
                event = EventSourceAdapter(main_session_id=self.session.session_id).background_completed(
                    run_id=notification.run_id, job_id=notification.job_id, session_id=self.session.session_id,
                    ok=notification.ok, result=asdict(notification))
                self._append_queued_background_result(CompletionInput(event_id=event.id, **fields))
                continue
            if notification.tool_call_id is not None:
                from raccoon.modules.events import EventSourceAdapter

                fields = asdict(notification)
                fields.pop("session_id")
                fields.pop("ok")
                # 与调度队列投递共用同一个确定性事件 id；否则成功覆盖记录的是本地
                # 合成名，重放完成事件时无法识别“这条结果已经答过了”。
                event = EventSourceAdapter(main_session_id=self.session.session_id).background_completed(
                    run_id=notification.run_id, job_id=notification.job_id,
                    session_id=self.session.session_id, ok=notification.ok, result=asdict(notification),
                )
                self._append_queued_background_result(CompletionInput(event_id=event.id, **fields))
                continue
            self.session.store.append_background_notification(self.session.data,
                content=render_task_notification(notification),
                job_id=notification.job_id,
                tool_name=notification.tool_name,
                status=notification.status,
                task_id=notification.task_id,
                observed_revision=notification.observed_revision,
                run_id=notification.run_id,
            )

    def _unsettled_native_jobs(self) -> list[str]:
        """Find unrecorded jobs launched by this execution, including across steering."""
        if self.background_manager is None or not self._native_calls_started:
            return []
        closed = {
            part.metadata.get("tool_call_id")
            for message in self.session.data.messages
            for part in message.parts if part.kind == "tool_result"
        }
        return [
            job.id for job in self.background_manager.list(session_id=self.session.session_id)
            if job.tool_call_id in self._native_calls_started and job.tool_call_id not in closed
        ]

    def _request_native_job_cancellation(self) -> None:
        """只取消本执行启动的普通原生作业；协作式，可能仍在运行。"""
        if self.background_manager is None or not self._native_calls_started:
            return
        for job_id in self._unsettled_native_jobs():
            job = self.background_manager.get(job_id, session_id=self.session.session_id)
            if job is not None and job.status == "running":
                self.background_manager.cancel(job_id, session_id=self.session.session_id)

    def _consume_native_job(self, job_id: str) -> bool:
        """把已完成的原生调用写回历史，身份与事件投递保持一致。"""

        from raccoon.modules.events import EventSourceAdapter

        notification = self.background_manager.finalize_completed(job_id, session_id=self.session.session_id)
        if notification is None:
            raise ValueError("Background task has no completed result in its owning runtime")
        event = EventSourceAdapter(main_session_id=self.session.session_id).background_completed(
            run_id=notification.run_id, job_id=notification.job_id,
            session_id=self.session.session_id, ok=notification.ok, result=asdict(notification),
        )
        fields = asdict(notification)
        fields.pop("session_id")
        fields.pop("ok")
        self._append_queued_background_result(CompletionInput(event_id=event.id, **fields))
        return True

    def _wait_for_tasks(
        self,
        handles: list[str] | None = None,
    ) -> ToolResult:
        if handles is not None and (
            not isinstance(handles, list)
            or any(not isinstance(handle, str) or not handle for handle in handles)
        ):
            return make_error_result("wait_for_tasks", "handles must be a list of non-empty strings")
        if self.background_manager is None:
            return make_error_result("wait_for_tasks", "Background execution is not enabled in this runtime.")

        jobs = {}
        if handles is not None:
            unresolved: list[str] = []
            for handle in dict.fromkeys(handles):
                job = self.background_manager.resolve(
                    handle, session_id=self.session.session_id,
                    alias_scope=self._background_alias_scope,
                )
                if job is None:
                    unresolved.append(handle)
                else:
                    jobs[job.id] = job
            if unresolved:
                return make_error_result(
                    "wait_for_tasks",
                    "Unknown, stale, or cross-session task handle: "
                    + ", ".join(unresolved)
                    + ". Use background_status to list tasks in this session.",
                )
            compat = [job for job in jobs.values() if job.tool_call_id is None]
            if compat:
                return make_error_result(
                    "wait_for_tasks",
                    "Compat background jobs cannot be awaited; use background_status: "
                    + ", ".join(job.handle or job.id for job in compat),
                )
        else:
            for job_id in self._unsettled_native_jobs():
                job = self.background_manager.get(job_id, session_id=self.session.session_id)
                if job is not None:
                    jobs[job.id] = job
        selected = list(jobs.values())
        self._wait_for_jobs(list(jobs))
        if not selected:
            return make_text_result("wait_for_tasks", "No matching tasks.", jobs=[])
        lines = [f"{job.handle or '(no handle)'} {job.id}: {job.status}" for job in selected]
        return make_text_result(
            "wait_for_tasks",
            "Selected tasks have finished.\n" + "\n".join(lines),
            jobs=[job.snapshot() for job in selected],
        )

    def _wait_for_jobs(self, job_ids: list[str]) -> None:
        from raccoon.modules.agent.native_async import native_result_recorded

        remaining = set(job_ids)
        waiting = False
        callback = self.run_phase_handler
        try:
            while remaining:
                self._check_cancelled()
                self._check_turn_timeout()
                for job_id in list(remaining):
                    job = self.background_manager.get(job_id, session_id=self.session.session_id)
                    if job is None:
                        raise ValueError("Background task disappeared from its owning runtime")
                    if job.status != "running":
                        # 完成通知可能已经先按 uuid 事件写入原始 tool_result；显式等待
                        # 只能观察终态，不能再写第二条同调用结果。
                        if job.tool_call_id is not None and native_result_recorded(self.session, job.tool_call_id):
                            remaining.remove(job_id)
                            continue
                        self._consume_native_job(job_id)
                        remaining.remove(job_id)
                if remaining:
                    if callback is not None and not waiting:
                        callback("waiting_tools")
                        waiting = True
                    time.sleep(0.05)
        except AgentCancelledError:
            # Stop only this turn's ordinary work; cancellation remains cooperative.
            self._request_native_job_cancellation()
            raise
        finally:
            if waiting and callback is not None:
                callback(None)

    def _settle_native_async_results(self) -> bool:
        jobs = self._unsettled_native_jobs()
        if not jobs:
            return False
        self._check_provider_call_limit()
        self._wait_for_jobs(jobs)
        return True

    async def _settle_native_async_results_async(self) -> bool:
        return await anyio.to_thread.run_sync(self._settle_native_async_results)

    def _check_provider_call_limit(self) -> None:
        limit = self.limits.max_provider_calls
        if limit is not None and self.turn_telemetry.provider_calls >= limit:
            raise _AgentLoopLimitReached(AgentLoopStopReason.PROVIDER_CALL_LIMIT)

    def _reserve_provider_call(self) -> None:
        self._check_provider_call_limit()
        self.turn_telemetry.observe_provider_call()

    def _turn_elapsed_seconds(self) -> float:
        if self.turn_started_at is None:
            return 0.0
        return max(0.0, self.clock() - self.turn_started_at)

    def _persist_turn_telemetry(
        self,
        *,
        status: str,
        stop_reason: str,
        finalize: bool,
        provider_failure_category: str | None = None,
    ) -> None:
        payload = self.turn_telemetry.snapshot(
            status=status,
            stop_reason=stop_reason,
            elapsed_seconds=self._turn_elapsed_seconds(),
            provider_failure_category=provider_failure_category,
            finalize=finalize,
        )
        if payload is not None:
            self.session.store.append_event(self.session.data, "agent_turn_telemetry", payload)

    def _persist_errored_turn(self, exc: BaseException) -> None:
        if not self.turn_telemetry.active:
            return
        if (
            isinstance(exc, AgentCancelledError | KeyboardInterrupt)
            or type(exc).__name__ == "CancelledError"
        ):
            self._append_interrupted_tool_results()
            self._persist_turn_telemetry(
                status="interrupted",
                stop_reason="interrupted",
                finalize=True,
            )
            return
        provider_category = exc.kind.value if isinstance(exc, ProviderError) else None
        stop_reason = provider_category or type(exc).__name__
        self._persist_turn_telemetry(
            status="errored",
            stop_reason=stop_reason,
            provider_failure_category=provider_category,
            finalize=True,
        )

    def _check_turn_timeout(self) -> None:
        limit = self.limits.max_turn_seconds
        if limit is None or self.turn_started_at is None:
            return
        if self.clock() - self.turn_started_at >= limit:
            raise _AgentLoopLimitReached(AgentLoopStopReason.TURN_TIMEOUT)

    def _is_cancelled(self) -> bool:
        return self.cancellation_token is not None and self.cancellation_token.is_cancelled

    def _check_cancelled(self) -> None:
        if self.cancellation_token is not None:
            self.cancellation_token.raise_if_cancelled()

    def _tool_round_limit_response(self, response: ChatResponse) -> ChatResponse:
        """工具轮次上限命中后，只保存纯文本说明，避免写入未执行的 tool_call。"""

        return self._limit_response(AgentLoopStopReason.TOOL_ROUND_LIMIT, raw=response.raw)

    def _limit_response(self, reason: AgentLoopStopReason, *, raw: dict | None = None) -> ChatResponse:
        messages = {
            AgentLoopStopReason.PROVIDER_CALL_LIMIT: (f"provider 调用次数达到上限（max_provider_calls={self.limits.max_provider_calls}），已停止继续执行。"),
            AgentLoopStopReason.TURN_TIMEOUT: (f"本轮任务耗时达到上限（max_turn_seconds={self.limits.max_turn_seconds}），已停止继续执行。"),
            AgentLoopStopReason.TOOL_ROUND_LIMIT: (f"工具调用轮次达到上限（max_tool_rounds={self.limits.max_tool_rounds}），已停止继续执行工具。"),
        }
        return ChatResponse(
            provider=self.provider.name,
            model=self.provider.model,
            content=messages[reason],
            tool_calls=[],
            finish_reason=reason.value,
            raw=raw,
        )

    def _interrupted_response(self) -> ChatResponse:
        return ChatResponse(
            provider=self.provider.name,
            model=self.provider.model,
            content="当前任务已中断。",
            tool_calls=[],
            finish_reason="interrupted",
            raw={"interrupted": True},
        )


class _AgentLoopLimitReached(Exception):
    def __init__(self, reason: AgentLoopStopReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


async def run_agent(run: AgentRun) -> ChatResponse:
    """接入输入、调用模型、执行工具；直到结束、失败或用户停止。"""
    try:
        run._begin_turn()
        run._repair_interrupted_tool_calls_before_provider_request()
        run._check_cancelled()
        run._append_initial_input()
        rounds = 0
        try:
            if run.limits.max_tool_rounds is not None and run.limits.max_tool_rounds <= 0:
                response = run._limit_response(AgentLoopStopReason.TOOL_ROUND_LIMIT)
            else:
                runtime_instruction = None
                while True:
                    response = await run.request_model_response(runtime_instruction=runtime_instruction)
                    runtime_instruction = None
                    run._check_cancelled()
                    if response.tool_calls:
                        run.session.store.append_assistant_response(run.session.data, response,
                            scheduled_run_id=run.session.scheduled_run_id)
                        run._publish_response(response)
                        await tool_execution.execute_tool_batch(run, response.tool_calls)
                        run.session.flush_tool_definitions()
                        rounds += 1
                        if run.limits.max_tool_rounds is not None and rounds >= run.limits.max_tool_rounds:
                            response = run._tool_round_limit_response(response)
                            break
                        continue
                    if await run._settle_native_async_results_async():
                        continue
                    runtime_instruction = run._final_reconciliation_instruction()
                    if runtime_instruction is not None:
                        continue
                    break
        except _AgentLoopLimitReached as error:
            response = run._limit_response(error.reason)
        except AgentCancelledError:
            run._append_interrupted_tool_results()
            response = run._interrupted_response()
        if run._is_cancelled():
            run._request_native_job_cancellation()
            run._append_interrupted_tool_results()
            response = run._interrupted_response()
        result = run.finish_turn(response)
        if response.finish_reason in {None, "stop"} and run.compaction_settings.enabled:
            budget = run.context_budget_for_view(run.session.data)
            if budget.input_tokens > budget.context_window - run.compaction_settings.reserve_tokens:
                await run.compact_context(reason="threshold")
        return result
    except BaseException as error:
        run._persist_errored_turn(error)
        raise
