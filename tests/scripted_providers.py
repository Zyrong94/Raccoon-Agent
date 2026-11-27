"""Scripted model providers shared by offline tests."""

import asyncio
from raccoon.runtime.model_types import MessagePart
from dataclasses import dataclass, field
import threading

from raccoon.runtime.model_provider import ChatProvider
from raccoon.runtime.model_types import ChatResponse, ProviderCapabilities


def response(text="done", *, calls=None):
    return ChatResponse(
        provider="fake",
        model="fake",
        finish_reason="tool_calls" if calls else "stop",
        parts=[MessagePart(kind="text", content=text)] + [MessagePart.from_tool_call(call) for call in (calls or [])],
    )


@dataclass
class FakeProvider(ChatProvider):
    responses: list[ChatResponse]
    block_first: bool = False
    requests: list = field(default_factory=list)
    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)

    @property
    def name(self):
        return "fake"

    @property
    def model(self):
        return "fake"

    @property
    def capabilities(self):
        return ProviderCapabilities()

    def complete(self, request):
        self.requests.append(request)
        if self.block_first and len(self.requests) == 1:
            self.entered.set()
            assert self.release.wait(5), "test did not release provider"
        return self.responses.pop(0)

    async def acomplete(self, request):
        return await asyncio.to_thread(self.complete, request)


class BoundaryProvider(FakeProvider):
    def complete(self, request):
        return response("{}")  # Existing task-boundary classifier may fall back; no real LLM.


class AsyncProvider(FakeProvider):
    @property
    def capabilities(self):
        return ProviderCapabilities(supports_async_tools=True)
