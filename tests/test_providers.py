"""模型接口仅要求异步完整响应。"""
import asyncio

from raccoon.runtime.model_provider import ChatProvider
from raccoon.runtime.model_types import ChatMessage, ChatRequest, ChatResponse


class CompleteProvider(ChatProvider):
    name = "test"
    model = "test-model"

    async def acomplete(self, request):
        return ChatResponse(provider=self.name, model=self.model, content=request.messages[0].content)


def test_provider_returns_complete_response_without_sync_or_stream_interface():
    provider = CompleteProvider()
    request = ChatRequest(messages=[ChatMessage(role="user", content="hello")])
    response = asyncio.run(provider.acomplete(request))
    assert response.content == "hello"
    assert not hasattr(provider, "complete")
    assert not hasattr(provider, "astream")
