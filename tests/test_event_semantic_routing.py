from __future__ import annotations

import asyncio
import json

import pytest

from raccoon.runtime.model_types import MessagePart
from raccoon.modules.events.models import Event
from raccoon.app.conversations import SessionSnapshot
from raccoon.modules.events.semantic_routing import (
    ROUTING_MODES,
    EventRoutingError,
    MessageRouteClassifier,
    parse_event_routing,
)
from raccoon.runtime.model_provider import ChatProvider
from raccoon.runtime.model_types import ChatRequest, ChatResponse


class FakeProvider(ChatProvider):
    """记录请求并按预设返回；可选门控与取消抑制。"""

    def __init__(
        self,
        response: ChatResponse | None = None,
        *,
        gate: asyncio.Event | None = None,
        suppress_cancel: bool = False,
        finish_on_cancel: bool = False,
        error: Exception | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self.gate = gate
        self.suppress_cancel = suppress_cancel
        self.finish_on_cancel = finish_on_cancel
        self.requests: list[ChatRequest] = []
        self.entered = asyncio.Event()

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    def complete(self, request: ChatRequest) -> ChatResponse:
        raise AssertionError("semantic routing must use acomplete")

    async def acomplete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        self.entered.set()
        if self.gate is not None:
            while not self.gate.is_set():
                try:
                    await self.gate.wait()
                except asyncio.CancelledError:
                    if self.finish_on_cancel:
                        break
                    if not self.suppress_cancel:
                        raise
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    @property
    def calls(self) -> int:
        return len(self.requests)


def user_event(
    name: str = "incoming",
    *,
    session: str = "main",
    text: str | None = None,
    kind: str = "user",
    event_type: str = "user.message",
    extra: dict[str, object] | None = None,
) -> Event:
    payload: dict[str, object] = {"origin": {"kind": kind}, "content": {"text": name if text is None else text}}
    if extra:
        payload.update(extra)
    return Event(id=name, type=event_type, source="test", session_id=session, payload=payload)


def snapshot(*events: Event, session: str = "main") -> SessionSnapshot:
    return SessionSnapshot(session, tuple(events))


def prior() -> SessionSnapshot:
    return snapshot(user_event("prior", text="fix the login bug", session="main"))


def reply(mode: str, event_id: str = "incoming") -> ChatResponse:
    return ChatResponse(
        provider="fake",
        model="fake-model",
        parts=[MessagePart(kind="text", content=json.dumps({"mode": mode, "event_id": event_id}, ensure_ascii=False))],
    )


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("mode", sorted(ROUTING_MODES))
def test_classify_returns_each_canonical_mode(mode):
    async def scenario():
        provider = FakeProvider(reply(mode))
        router = MessageRouteClassifier(provider)
        assert await router.classify(user_event(), prior()) == mode
        assert provider.calls == 1
        body = json.loads(provider.requests[0].messages[1].content)
        assert body["incoming_event"]["event_id"] == "incoming"

    run(scenario())


BAD_OUTPUTS = [
    'not JSON',
    '{"mode":"steer","mode":"queue","event_id":"incoming"}',
    '{"mode":"steer","event_id":"another-request"}',
    '{"mode":"unknown","event_id":"incoming"}',
]


@pytest.mark.parametrize("content", BAD_OUTPUTS)
def test_bad_output_is_rejected_and_falls_back_to_queue(content):
    assert parse_event_routing(content, event_id="incoming") is None

    async def scenario():
        provider = FakeProvider(ChatResponse(
            provider="fake",
            model="fake-model",
            parts=[MessagePart(kind="text", content=content)],
        ))
        router = MessageRouteClassifier(provider)
        assert await router.classify(user_event(), prior()) == "queue"
        assert provider.calls == 1

    run(scenario())


@pytest.mark.parametrize("mode", [*sorted(ROUTING_MODES), "follow_up"])
def test_explicit_mode_bypasses_provider(mode):
    async def scenario():
        provider = FakeProvider(reply("interrupt"))
        router = MessageRouteClassifier(provider)
        expected = "queue" if mode == "follow_up" else mode
        assert await router.classify(user_event(), prior(), explicit_mode=mode) == expected
        assert provider.calls == 0

    run(scenario())


@pytest.mark.parametrize("event", [
    user_event("timer", event_type="timer.trigger", kind="system"),
    user_event("hook", event_type="webhook.push", kind="external"),
    user_event("sys", kind="system"),
])
def test_non_user_events_skip_the_provider(event):
    async def scenario():
        provider = FakeProvider(reply("interrupt"))
        router = MessageRouteClassifier(provider)
        assert await router.classify(event, prior()) == "queue"
        assert provider.calls == 0

    run(scenario())


def test_cross_session_snapshot_is_rejected():
    async def scenario():
        provider = FakeProvider(reply("interrupt"))
        router = MessageRouteClassifier(provider)
        with pytest.raises(EventRoutingError):
            await router.classify(user_event(), snapshot(user_event("prior"), session="other"))
        assert provider.calls == 0
        mixed = snapshot(user_event("prior"), user_event("stray", session="other"))
        with pytest.raises(EventRoutingError):
            await router.classify(user_event(), mixed)
        assert provider.calls == 0

    run(scenario())


def test_request_options_are_respected():
    async def scenario():
        provider = FakeProvider(reply("queue"))
        router = MessageRouteClassifier(provider, reasoning_effort="low")
        await router.classify(user_event(), prior())
        request = provider.requests[0]
        assert request.reasoning_effort == "low"

    run(scenario())


def test_timeout_with_cancellation_suppressed_discards_late_result():
    async def scenario():
        gate = asyncio.Event()
        provider = FakeProvider(reply("interrupt"), gate=gate, suppress_cancel=True)
        router = MessageRouteClassifier(provider, timeout_seconds=0.01)

        async def release():
            await asyncio.sleep(0.05)
            gate.set()

        releaser = asyncio.create_task(release())
        try:
            assert await router.classify(user_event(), prior()) == "queue"
        finally:
            await asyncio.wait_for(releaser, timeout=0.1)
        assert provider.calls == 1

    run(scenario())


def test_caller_cancellation_propagates():
    async def scenario():
        provider = FakeProvider(reply("interrupt"), gate=asyncio.Event())
        router = MessageRouteClassifier(provider, timeout_seconds=5.0)
        task = asyncio.create_task(router.classify(user_event(), prior()))
        await asyncio.wait_for(provider.entered.wait(), timeout=0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
