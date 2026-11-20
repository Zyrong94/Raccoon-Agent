"""Injected entry-point factories retain identity and delivery/lifecycle behavior."""

import asyncio
from types import SimpleNamespace

from raccoon.interfaces.qq.groups import QQGroups
from raccoon.interfaces.qq.state import QQState
from tests.qq_fixtures import group_packet


def test_qq_adapter_uses_injected_context_for_delivery_and_close(tmp_path):
    async def scenario():
        state = QQState(tmp_path / 'qq.sqlite3')
        state.set('owner', 'private-owner')
        calls, messages, closed = [], [], []
        media = object()

        def runtime_factory(**kwargs):
            raise AssertionError('The adapter must leave runtime creation to its context factory')

        async def handle(packet):
            messages.append(packet)

        async def close():
            closed.append(True)

        async def context_factory(row, root, **kwargs):
            calls.append((dict(row), root, kwargs))
            return SimpleNamespace(channel=SimpleNamespace(_handle=handle), close=close)

        groups = QQGroups(state, project=tmp_path, data_root=tmp_path,
            runtime_factory=runtime_factory, context_factory=context_factory,
            members='all', mode='bypass', media_client=media)
        try:
            await groups.receive(group_packet('/pair ' + groups.issue_code('private-owner'))['d'])
            await groups.receive(group_packet('hello', member='teammate')['d'])
            await groups.receive(group_packet('again', message_id='g2')['d'])
            assert len(calls) == 1
            row, root, kwargs = calls[0]
            assert root == tmp_path / 'channels' / 'groups' / row['group_key']
            assert kwargs['runtime_factory'] is runtime_factory
            assert kwargs['media_client'] is media
            assert kwargs['members'] == 'all' and kwargs['mode'].value == 'bypass'
            assert [message['content'] for message in messages] == ['hello', 'again']
            assert messages[0]['author']['user_openid'] == 'teammate'
        finally:
            await groups.close()
            state.close()
        assert closed == [True]

    asyncio.run(scenario())
