"""非流式请求等待期间的取消、超时和错误传播。"""
import asyncio

import pytest

from raccoon.app.payloads import MessageInput
from raccoon.bootstrap.agent_session import new_session
from raccoon.modules.agent.run import AgentRun, run_agent
from raccoon.modules.agent.loop_limits import AgentLoopLimits
from raccoon.modules.sessions.store import SessionStore
from raccoon.runtime.cancellation import CancellationToken
from raccoon.runtime.model_errors import ProviderError, ProviderErrorKind
from raccoon.runtime.model_provider import ChatProvider


class WaitingProvider(ChatProvider):
    name = "waiting"
    model = "offline"

    def __init__(self):
        self.entered = asyncio.Event()
        self.closed = False

    async def acomplete(self, request):
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.closed = True


@pytest.mark.parametrize("stop_reason", ["interrupted", "turn_timeout"])
def test_pending_model_request_is_cancelled_and_closed(tmp_path, stop_reason):
    async def scenario():
        provider = WaitingProvider()
        token = CancellationToken()
        published = []
        clock_value = [0.0]
        session = new_session(store=SessionStore(tmp_path), session_id="waiting")
        run = AgentRun(
            session=session, provider=provider, cancellation_token=token,
            limits=AgentLoopLimits(max_turn_seconds=10),
            clock=lambda: clock_value[0], response_handler=published.append,
            initial_input=MessageInput("request", "hello"),
        )
        turn = asyncio.create_task(run_agent(run))
        try:
            await asyncio.wait_for(provider.entered.wait(), timeout=5)
            assert published == []
            if stop_reason == "interrupted":
                token.cancel()
            else:
                clock_value[0] = 11.0
            result = await asyncio.wait_for(turn, timeout=5)
            assert result.finish_reason == stop_reason
            assert provider.closed
            assert published == [result]
        finally:
            if not turn.done():
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)

    asyncio.run(scenario())


def test_provider_error_is_reported_without_retry_or_answer(tmp_path):
    class FailedProvider(ChatProvider):
        name = "failed"
        model = "offline"
        calls = 0

        async def acomplete(self, request):
            self.calls += 1
            raise ProviderError(ProviderErrorKind.NETWORK_ERROR, "offline failure")

    provider = FailedProvider()
    published = []
    session = new_session(store=SessionStore(tmp_path), session_id="failure")
    run = AgentRun(session=session, provider=provider, response_handler=published.append,
                   initial_input=MessageInput("request", "hello"))
    with pytest.raises(ProviderError, match="offline failure"):
        asyncio.run(run_agent(run))
    assert provider.calls == 1
    assert published == []
    assert all(message.role != "assistant" for message in session.store.load(session.session_id).messages)
