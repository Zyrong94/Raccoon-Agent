import asyncio
from types import SimpleNamespace

import httpx
import pytest

from raccoon.interfaces.qq.api import QQApi
from raccoon.interfaces.qq.channel import QQChannel
from raccoon.interfaces.qq.state import QQState
from raccoon.interfaces.qq.scheduling import schedule_command
from tests.test_schedule_event_flow import store_at, create


def test_qq_proactive_message_does_not_reuse_reply_id():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'access_token': 'test', 'expires_in': 7200} if 'getAppAccessToken' in str(request.url) else {'id': 'sent'})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await QQApi('app', 'secret', client).send_proactive_text('owner', 'report')
    asyncio.run(scenario())
    import json
    body = json.loads(requests[-1].content)
    assert body == {'content': 'report', 'msg_type': 0}


def test_qq_schedule_commands_are_scoped_to_origin(tmp_path):
    store = store_at(tmp_path)
    task = create(store)
    replies = []
    channel = SimpleNamespace(scheduling=SimpleNamespace(store=store), source=SimpleNamespace(main_session_id='other'),
                              _reply=lambda owner, msg_id, text: replies.append(text))
    assert asyncio.run(schedule_command(channel, '/schedule', 'owner', 'message'))
    assert '暂无' in replies[-1]
    assert asyncio.run(schedule_command(channel, '/schedule run ' + task['id'], 'owner', 'message'))
    assert store.runs(task['id']) == []


def test_qq_route_change_prevents_send(tmp_path):
    class Api:
        async def send_proactive_text(self, *args):
            raise AssertionError('must not send')
    state = QQState(tmp_path / 'qq.sqlite3')
    state.set('owner', 'new-owner')
    state.enqueue_text('old-owner', '', 'private')
    channel = QQChannel.__new__(QQChannel)
    channel.state = state
    try:
        row = state.pending_outbound()[0]
        assert asyncio.run(channel.deliver_one(Api(), row)) is False
        assert not state.pending_outbound()
    finally:
        state.close()
