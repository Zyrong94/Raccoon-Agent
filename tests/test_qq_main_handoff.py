import asyncio

import pytest

from raccoon.bootstrap.main_host import MainSessionBridge
from tests.scripted_providers import FakeProvider
from tests.runtime_fixtures import FakeMcpManager


@pytest.mark.parametrize('web_first', [False, True])
def test_qq_gateway_keeps_rich_connector_after_either_startup_order(tmp_path, monkeypatch, web_first):
    import raccoon.bootstrap.qq as qq
    from raccoon.bootstrap.runtime import create_runtime
    channels = []
    ready = []
    class EndGateway(Exception):
        pass
    def factory(**kwargs):
        return create_runtime(**kwargs, provider=FakeProvider([]), tools=[],
                              mcp_manager_factory=lambda configs: FakeMcpManager())
    original_channel = qq.QQChannel
    def capture_channel(*args, **kwargs):
        bot = original_channel(*args, **kwargs)
        channels.append(bot)
        return bot
    class Gateway:
        def __init__(self, api, state):
            self.online = asyncio.Event()
        async def run(self):
            bot = channels[-1]
            if bot.permission_commands is None:
                await asyncio.Future()  # Temporary connector waits for the idle handoff.
            ready.append(bot)
            state = await bridge.call('snapshot')
            assert state['session_id'] == bot.source.main_session_id
            assert bridge.owner is None
            assert bot.image_input is not None
            assert bot.groups is not None
            assert bot.scheduling is not None
            assert not hasattr(bot, 'ui')
            assert 'surfaces' not in state and 'weather' not in state
            assert bot.permission_commands is not None
            raise EndGateway()
    async def gateway_url(self):
        return 'wss://unused.invalid'
    monkeypatch.setenv('RACCOON_QQ_APP_ID', 'fake-app')
    monkeypatch.setenv('RACCOON_QQ_APP_SECRET', 'fake-secret')
    monkeypatch.setattr(qq, 'create_runtime', factory)
    monkeypatch.setattr(qq, 'QQChannel', capture_channel)
    monkeypatch.setattr(qq, 'QQGateway', Gateway)
    monkeypatch.setattr(qq.QQApi, 'gateway_url', gateway_url)
    async def scenario():
        nonlocal bridge
        bridge = MainSessionBridge(tmp_path, tmp_path / '.raccoon', runtime_factory=factory)
        if web_first:
            original = await bridge.call('snapshot')
        try:
            with pytest.raises(EndGateway):
                async with asyncio.timeout(10):
                    await qq.serve(tmp_path, tmp_path / '.raccoon', tmp_path / '.env.absent')
            assert len(ready) == 1
            if web_first:
                assert ready[0].source.main_session_id == original['session_id']
        finally:
            await bridge.close()
    bridge = None
    asyncio.run(scenario())
