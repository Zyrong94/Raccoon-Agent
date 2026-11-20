"""Run conversation turns against the current session and AgentRun.

Keep session replacement, cancellation and pending input at the conversation boundary.
"""

from __future__ import annotations


import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from contextlib import contextmanager, ExitStack
from pathlib import Path
from typing import Any

import anyio

from raccoon.application.conversations.model_switching import ROUTING_MODEL_REF, create_routing_model, model_request_options
from raccoon.config.models import ModelCatalogError
from raccoon.integrations.providers.factory import create_provider_for_model

from raccoon.runtime.cancellation import CancellationToken
from raccoon.modules.agent.run import AgentRun, run_agent, AgentRunPhase, ToolExecutionEvent
from raccoon.runtime.background import BackgroundJobManager
from raccoon.modules.agent.loop_limits import AgentLoopLimits
from raccoon.modules.agent.runtime_capabilities import AgentRuntimeCapabilities
from raccoon.modules.agent.session import AgentSession
from raccoon.app.payloads import MessageInput
from raccoon.modules.permissions.types import PermissionMode
from raccoon.modules.permissions.sandbox_access import SandboxAccessMode
from raccoon.runtime.user_input import UserInputRequest
from raccoon.modules.context.context_builder import ContextBuilder
from raccoon.modules.context.compaction import CompactionSettings
from raccoon.runtime.model_provider import ChatProvider
from raccoon.runtime.model_types import ChatResponse, MainRequestOptions
from raccoon.modules.tools.types import Tool


@dataclass(slots=True)
class SessionRuntime:
    """普通聊天入口，把当前 session 交给 AgentRun 执行一轮。"""

    session: AgentSession
    provider: ChatProvider
    tools: list[Tool] | None = None
    tools_provider: Callable[[], list[Tool]] | None = None
    tool_catalog_status: Callable[[], str] | None = None
    context_builder: ContextBuilder | None = None
    compaction_settings: CompactionSettings | None = None
    memory_service: Any | None = None
    limits: AgentLoopLimits | None = None
    request_options: MainRequestOptions = field(default_factory=MainRequestOptions)
    routing_provider: ChatProvider | None = None
    routing_request_options: MainRequestOptions | None = None
    routing_timeout_seconds: float | None = None
    context_window: int | None = None
    active_run: AgentRun | None = field(default=None, init=False, repr=False)
    input_event_handler: Callable[[UserInputRequest | None, str | None], None] | None = None
    response_handler: Callable[[ChatResponse], None] | None = None
    tool_event_handler: Callable[[ToolExecutionEvent], None] | None = None
    run_phase_handler: Callable[[AgentRunPhase | None], None] | None = None
    background_manager: BackgroundJobManager | None = None
    native_async_tools: bool = False
    runtime_capabilities: AgentRuntimeCapabilities = field(
        default_factory=AgentRuntimeCapabilities.interactive
    )
    _cancellation_lock: threading.Lock = field(default_factory=threading.Lock)
    _active_cancellation_token: CancellationToken | None = None
    execution_root: Path | None = None
    _scheduling_installed: bool = False

    application: Any = field(default=None, repr=False)
    catalog: Any = None
    model_catalog: Any = None
    model_ref: str = ""
    project_path: Path | None = None
    app_config: Any = None
    mcp_status: Callable | None = None
    on_shutdown: Callable | None = None
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.on_shutdown is not None:
            self.on_shutdown()

    @contextmanager
    def execution_guard(self):
        if self.execution_root is None:
            yield
            return
        from raccoon.runtime.session_locks import session_lock
        import portalocker
        lock = session_lock(self.execution_root, self.session.session_id)
        try:
            lock.acquire()
        except portalocker.exceptions.LockException:
            raise ValueError('此会话正由另一执行器处理，请稍后重试；定时任务审批请到定时任务页处理') from None
        try:
            self.session.store.refresh(self.session.data)
            yield
        finally:
            lock.release()

    @property
    def session_id(self) -> str:
        return self.session.session_id


    def update_model_catalog(self, model_config: dict) -> None:
        """Apply a Web model-library edit; adding a model does not switch this session."""
        app_config = replace(self.app_config, model_config=model_config)
        catalog = app_config.model_catalog()
        current_ref = f"{self.provider.name}/{self.provider.model}"
        profile = catalog.models.get(current_ref)
        provider = None
        if profile is not None and profile != self.model_catalog.models.get(current_ref):
            provider = create_provider_for_model(app_config, profile)
            options = model_request_options(profile)
            effort = self.session.data.metadata.get("reasoning_effort")
            if effort:
                if effort not in profile.supported_reasoning_efforts:
                    raise ModelCatalogError("当前推理强度不受新模型配置支持，请重新选择")
                options = replace(options, extra_body={**options.extra_body, "reasoning_effort": effort})
        routing_profile = catalog.models.get(ROUTING_MODEL_REF)
        routing_changed = routing_profile != self.model_catalog.models.get(ROUTING_MODEL_REF)
        if routing_changed:
            routing_provider, routing_options = create_routing_model(app_config, routing_profile)
        self.app_config = app_config
        self.model_catalog = catalog
        if provider is not None:
            self.set_model(provider, request_options=options, context_window=profile.context_window)
        if routing_changed:
            self.routing_provider = routing_provider
            self.routing_request_options = routing_options

    def switch_model(self, spec: str) -> None:
        ref = spec.strip()
        if not ref or any(character.isspace() for character in ref) or "/" not in ref:
            raise ValueError("模型目录模式需要使用 <provider>/<model>，且不能包含空白")
        profile = self.model_catalog.models.get(ref)
        if profile is None:
            raise ModelCatalogError(f"未配置模型：{ref}，请从全局模型目录重新选择")
        provider = create_provider_for_model(self.app_config, profile)
        options = model_request_options(profile)
        self.session.store.append_session_metadata_updated(self.session.data, model_ref=ref, reasoning_effort="")
        self.set_model(provider, request_options=options, context_window=profile.context_window)
        self.model_ref = ref

    def set_effort(self, effort: str | None) -> None:
        model_ref = f"{self.provider.name}/{self.provider.model}"
        profile = self.model_catalog.models[model_ref]
        if effort is not None and effort not in profile.supported_reasoning_efforts:
            raise ValueError("当前模型不支持这个推理强度")
        extra_body = dict(self.request_options.extra_body)
        extra_body.pop("reasoning", None)
        extra_body.pop("reasoning_effort", None)
        selected_effort = profile.request.reasoning_effort if effort is None else effort
        if selected_effort is not None:
            extra_body["reasoning_effort"] = selected_effort
        # 元数据中的 None 不覆盖旧值；空字符串明确表示恢复模型默认值。
        self.session.store.append_session_metadata_updated(
            self.session.data,
            reasoning_effort="" if effort is None else effort,
        )
        self.request_options = replace(self.request_options, extra_body=extra_body)

    def set_model(
        self,
        provider: ChatProvider,
        *,
        request_options: MainRequestOptions,
        context_window: int | None,
    ) -> None:
        self.provider = provider
        self.request_options = request_options
        self.context_window = context_window
        self.native_async_tools = (
            self.runtime_capabilities.enable_native_async_tools
            and bool(getattr(getattr(provider, "capabilities", None), "supports_async_tools", False))
        )

    @property
    def last_pending_input(self) -> UserInputRequest | None:
        return self.active_run.pending_input if self.active_run is not None else None

    def answer_input(self, request_id: str, answer: str) -> None:
        if self.active_run is None:
            raise ValueError("当前没有等待回答的运行")
        self.active_run.answer_input(request_id, answer)

    async def _request_input(self, request: UserInputRequest) -> str:
        loop = self.active_run
        if loop is None:
            raise RuntimeError("没有正在执行的 Agent 循环，无法等待输入")
        answer = await loop.wait_for_input(request, loop.cancellation_token, self.input_event_handler)
        # 界面可在等待期间重新订阅；继续使用同一协程，只更新事件出口。
        loop.response_handler = self.response_handler
        loop.tool_event_handler = self.tool_event_handler
        loop.run_phase_handler = self.run_phase_handler
        return answer


    def cancel_current_turn(self) -> None:
        with self._cancellation_lock:
            if self._active_cancellation_token is not None:
                self._active_cancellation_token.cancel()

    def _begin_cancellable_turn(self) -> CancellationToken:
        token = CancellationToken()
        with self._cancellation_lock:
            self._active_cancellation_token = token
        return token

    def _finish_cancellable_turn(self, token: CancellationToken) -> None:
        with self._cancellation_lock:
            if self._active_cancellation_token is token:
                self.active_run = None
                self._active_cancellation_token = None

    def _start_turn(self, *, initial_input=None, take_inputs=None, token: CancellationToken | None = None) -> AgentRun:
        token = token or self._begin_cancellable_turn()
        loop = self.create_run(token, initial_input=initial_input, take_inputs=take_inputs)
        self.active_run = loop
        return loop

    async def run_turn(self, initial_input, take_inputs) -> ChatResponse:
        """执行一轮已由统一事件入口安排的输入。"""
        if self._active_cancellation_token is not None:
            raise ValueError("当前会话已有正在执行或等待回答的回合")
        token = self._begin_cancellable_turn()

        async def execute_agent():
            with ExitStack() as scope:
                settings = initial_input.settings if isinstance(initial_input, MessageInput) else None
                if settings is not None:
                    mode = PermissionMode(settings.permission_mode)
                    sandbox_mode = SandboxAccessMode.UNRESTRICTED if mode == PermissionMode.BYPASS else SandboxAccessMode.PROJECT
                    scope.enter_context(self.session.sandbox_access.use_mode(sandbox_mode))
                    if self.session.permission_manager is not None:
                        scope.enter_context(self.session.permission_manager.use_mode(mode))
                run = self._start_turn(initial_input=initial_input, take_inputs=take_inputs, token=token)
                return await run_agent(run)

        try:
            return await self._run_in_worker_thread(execute_agent(), token)
        finally:
            self._finish_cancellable_turn(token)

    async def _run_in_worker_thread(self, coroutine, token: CancellationToken):
        worker = asyncio.create_task(anyio.to_thread.run_sync(_run_coroutine_in_thread, coroutine))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            token.cancel()
            # A cancelled asyncio await does not stop the Python thread or its tools.
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    token.cancel()
                except Exception:
                    break
            if not worker.cancelled():
                worker.exception()
            raise

    def _current_tools(self) -> list[Tool] | None:
        """Resolve tools once per loop so the session registry sees that same list."""

        return self.tools_provider() if self.tools_provider is not None else self.tools

    def create_run(self, cancellation_token: CancellationToken, *, initial_input=None, take_inputs=None) -> AgentRun:
        provider, request_options, context_window = self.provider, self.request_options, self.context_window
        if isinstance(initial_input, MessageInput) and initial_input.settings is not None:
            provider, request_options, context_window = self.model_for_turn(initial_input.settings.model_ref)
        kwargs = {
            "session": self.session,
            "application": self.application,
            "request_input": self._request_input if self.runtime_capabilities.allow_user_input and self.input_event_handler is not None else None,
            "provider": provider,
            "request_options": request_options,
            "routing_provider": self.routing_provider,
            "routing_request_options": self.routing_request_options,
            "routing_timeout_seconds": self.routing_timeout_seconds,
            "context_window": context_window,
            "tools": self._current_tools(),
            "tools_provider": self.tools_provider,
            "tool_catalog_status": self.tool_catalog_status,
            "context_builder": self.context_builder,
            "compaction_settings": self.compaction_settings,
            "memory_service": self.memory_service,
            "limits": self.limits,
            "tool_event_handler": self.tool_event_handler,
            "run_phase_handler": self.run_phase_handler,
            "cancellation_token": cancellation_token,
            "background_manager": self.background_manager,
            "runtime_capabilities": self.runtime_capabilities,
        }
        kwargs["response_handler"] = self.response_handler
        kwargs["initial_input"] = initial_input
        kwargs["take_inputs"] = take_inputs
        native_async_tools = self.native_async_tools
        if provider is not self.provider:
            native_async_tools = (self.runtime_capabilities.enable_native_async_tools and self.background_manager is not None
                                  and getattr(provider.capabilities, "supports_async_tools", False))
        if native_async_tools:
            kwargs["native_async_tools"] = True
        loop = AgentRun(**kwargs)
        return loop

    def model_for_turn(self, model_ref):
        if model_ref == self.model_ref:
            return self.provider, self.request_options, self.context_window
        profile = self.model_catalog.models.get(model_ref)
        if profile is None:
            raise ModelCatalogError(f"未配置模型：{model_ref}")
        return create_provider_for_model(self.app_config, profile), model_request_options(profile), profile.context_window

    def _waiting_for_input_response(self, pending: UserInputRequest | None) -> ChatResponse:
        return ChatResponse(
            provider=self.provider.name,
            model=self.provider.model,
            content=pending.question if pending else "等待用户输入。",
            finish_reason="waiting_for_user_input",
            raw={"pending_input": pending},
        )


def _run_coroutine_in_thread(coro):
    return asyncio.run(coro)
