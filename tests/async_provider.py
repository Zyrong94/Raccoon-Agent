"""Run scripted synchronous test doubles through the async provider contract."""

import asyncio


class AsyncProviderMixin:
    async def acomplete(self, request):
        return await asyncio.to_thread(self.complete, request)
