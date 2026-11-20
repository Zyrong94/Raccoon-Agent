"""QQ delivery uses real local state; only the remote HTTP service is faked."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from raccoon.interfaces.qq.api import QQApi, QQApiError
from raccoon.interfaces.qq.state import QQState
from raccoon.runtime import UserInputOption, UserInputRequest
from tests.qq_fixtures import channel, packet


def pending(request_id='permission-one'):
    return UserInputRequest(request_id, 'permission_confirmation', '允许浏览网页吗？', [
        UserInputOption('allow_once', 'Allow once'), UserInputOption('deny', 'Deny')])


def test_qq_permission_hides_json_and_long_commands_but_keeps_detail_route(tmp_path):
    from tests.test_permission_presentation import request
    bot,state,runtime=channel(tmp_path)
    state.set('owner','owner');state.set('reply_msg_id','m1')
    runtime.pending_inputs['main']=request()
    try:
        bot.on_response('main',SimpleNamespace(content='',finish_reason='waiting_for_user_input'))
        row=state.pending_outbound()[0]
        visible=json.loads(row['payload'])['markdown']['content']
        assert '允许访问此网站' in visible
        assert 'www.tianqi.com/hangzhou/' in visible
        assert 'permission_request_id' not in visible and 'pending_tool_call' not in visible
        assert '/permission ' not in visible and 'internal-id' not in visible
        assert '/permission ' in row['content']
        buttons=[b for group in json.loads(row['payload'])['keyboard']['content']['rows'] for b in group['buttons']]
        assert any(b['action']['data'].startswith('/permission-details ') for b in buttons)
    finally:
        state.close()


@pytest.mark.parametrize('suffix', ['\ue200cite\ue202turn0search?\ue201',
    '\n\ue200cite\ue202turn0search0\ue202turn1search2\ue201',
    '\ue200cite\ue202turn0search0'])
def test_qq_reply_removes_internal_citations_without_changing_raw_response(tmp_path, suffix):
    bot,state,runtime=channel(tmp_path)
    state.set('owner','owner');state.set('reply_msg_id','m1')
    answer='杭州多云，17～21℃。'
    response=SimpleNamespace(content=answer+suffix,finish_reason='stop')
    try:
        bot.on_response('main',response)
        assert state.pending_outbound()[0]['content']==answer
        assert response.content==answer+suffix
    finally:
        state.close()


def test_qq_preserves_requested_links_and_non_citation_text(tmp_path):
    bot,state,runtime=channel(tmp_path)
    state.set('owner','owner');state.set('reply_msg_id','m1')
    answer='来源：[天气网](https://www.tianqi.com/hangzhou/)；cite 是普通单词。'
    try:
        bot.on_response('main',SimpleNamespace(content=answer,finish_reason='stop'))
        assert state.pending_outbound()[0]['content']==answer
    finally:
        state.close()


def test_old_outbox_migrates_without_losing_text_or_sequence(tmp_path):
    path = tmp_path / 'legacy.db'
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE outbound (id INTEGER PRIMARY KEY, openid TEXT NOT NULL, msg_id TEXT NOT NULL, msg_seq INTEGER NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, error TEXT)")
    con.execute("INSERT INTO outbound(openid,msg_id,msg_seq,content) VALUES ('owner','m1',3,'old')")
    con.commit(); con.close()
    state = QQState(path)
    state.enqueue_markdown('owner', 'm2', '# title', buttons=[{'label': '状态', 'command': '/status'}])
    rows = state.pending_outbound()
    assert rows[0]['content'] == 'old' and rows[0]['msg_seq'] == 3
    body = json.loads(rows[1]['payload'])
    assert body['msg_type'] == 2 and body['markdown']['content'] == '# title'
    assert body['keyboard']['content']['rows'][0]['buttons'][0]['action']['data'] == '/status'
    state.close()
    reopened = QQState(path)
    assert len(reopened.pending_outbound()) == 2
    reopened.close()


def test_permissions_offer_only_real_options_and_bind_short_code(tmp_path):
    async def run():
        bot, state, runtime = channel(tmp_path)
        state.set('owner', 'owner'); state.set('reply_msg_id', 'm1')
        runtime.pending_inputs['main'] = pending()
        bot.on_response('main', SimpleNamespace(content='', finish_reason='waiting_for_user_input'))
        row = state.pending_outbound()[-1]
        body = json.loads(row['payload'])
        buttons = body['keyboard']['content']['rows'][0]['buttons']
        commands = [b['action']['data'] for b in buttons]
        assert [command for command in commands if command.startswith('/permission ')] == [f'/permission permission-one@{bot.permission_code} allow_once', f'/permission permission-one@{bot.permission_code} deny']
        assert '仅本次允许' in str(body)
        code = bot.permission_code
        assert code and len(code) <= 12
        await bot.receive(packet(f'/permission {code} allow_once', sender='intruder', message_id='bad'))
        assert runtime.answers == []
        await bot.receive(packet(f'/permission {code} allow_once', message_id='ok'))
        assert runtime.answers == [('main', 'permission-one', 'allow_once')]
        runtime.pending_inputs['main'] = pending('permission-two')
        await bot.receive(packet(f'/permission {code} allow_once', message_id='stale'))
        assert len(runtime.answers) == 1
        state.close()
    asyncio.run(run())


def test_repeated_permission_scope_never_accepts_previous_button_or_bare_id(tmp_path):
    async def run():
        bot,state,runtime=channel(tmp_path)
        state.set('owner','owner'); state.set('reply_msg_id','m1')
        first=pending('same-scope'); first.payload={'pending_tool_call':{'id':'call1'}}
        runtime.pending_inputs['main']=first
        bot.on_response('main',SimpleNamespace(content='',finish_reason='waiting_for_user_input'))
        old_button=json.loads(state.pending_outbound()[-1]['payload'])['keyboard']['content']['rows'][0]['buttons'][0]['action']['data']
        second=pending('same-scope'); second.payload={'pending_tool_call':{'id':'call2'}}
        runtime.pending_inputs['main']=second
        bot.on_response('main',SimpleNamespace(content='',finish_reason='waiting_for_user_input'))
        await bot.receive(packet(old_button,message_id='stale'))
        await bot.receive(packet('/permission same-scope allow_once',message_id='bare'))
        assert runtime.answers==[]
        await bot.receive(packet(f'/permission {bot.permission_code} allow_once',message_id='valid'))
        assert runtime.answers==[('main','same-scope','allow_once')]
        state.close()
    asyncio.run(run())


@pytest.mark.parametrize('error,expected_type', [(QQApiError(400, 304057), 2), (QQApiError(400, 40034124), 0)])
def test_definitive_rejection_durably_removes_unsupported_format(tmp_path, error, expected_type):
    async def run():
        bot, state, runtime = channel(tmp_path)
        state.enqueue_markdown('owner','m1','# status', buttons=[{'label':'状态','command':'/status'}])
        class Api:
            async def send_message(self, *args):
                raise error
        await bot.deliver_one(Api(), state.pending_outbound()[0])
        row = state.pending_outbound()[0]
        body = json.loads(row['payload'])
        assert body['msg_type'] == expected_type
        assert 'keyboard' not in body
        assert row['msg_seq'] == 1
        assert state.get('qq_keyboard_supported') is False or expected_type == 0
        state.close()
    asyncio.run(run())


def test_timeout_never_downgrades_or_claims_delivery(tmp_path):
    async def run():
        bot,state,runtime = channel(tmp_path)
        state.enqueue_markdown('owner','m1','hello',buttons=[{'label':'状态','command':'/status'}])
        class Api:
            async def send_message(self, *args):
                raise httpx.ReadTimeout('unknown remote delivery')
        before = state.pending_outbound()[0]['payload']
        await bot.deliver_one(Api(), state.pending_outbound()[0])
        row = state.pending_outbound()[0]
        assert row['payload'] == before and row['status'] == 'pending'
        assert row['attempts'] == 1
        state.close()
    asyncio.run(run())


def test_corrupt_outbox_payload_is_failed_without_stopping_host(tmp_path):
    async def run():
        bot,state,runtime=channel(tmp_path)
        state.enqueue_markdown('owner','m1','message')
        row=state.pending_outbound()[0]
        state.db.execute('UPDATE outbound SET payload=? WHERE id=?', ('{bad json',row['id']))
        state.db.commit()
        assert not await bot.deliver_one(object(),state.pending_outbound()[0])
        assert not state.pending_outbound()
        assert state.db.execute('SELECT status FROM outbound WHERE id=?',(row['id'],)).fetchone()[0]=='failed'
        state.close()
    asyncio.run(run())


def test_markdown_api_keeps_message_identity():
    async def run():
        sent=[]
        def handler(req):
            if req.url.host=='bots.qq.com':
                return httpx.Response(200,json={'access_token':'test','expires_in':7200})
            sent.append(json.loads(req.content))
            return httpx.Response(200,json={'id':'reply'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await QQApi('id','secret',client).send_message('owner','m1',4,{'msg_type':2,'markdown':{'content':'# test'}})
        assert sent == [{'msg_type':2,'markdown':{'content':'# test'},'msg_id':'m1','msg_seq':4}]
    asyncio.run(run())


def test_image_upload_uses_platform_reference_without_sending_auth_to_storage():
    async def run():
        seen=[]
        def handler(req):
            seen.append(req)
            if req.url.host=='bots.qq.com':
                return httpx.Response(200,json={'access_token':'test','expires_in':7200})
            if req.url.path.endswith('/prepare'):
                return httpx.Response(200,json={'upload_id':'upload1','presigned_url':'https://test.cos.ap-guangzhou.myqcloud.com/upload'})
            if req.method=='PUT':
                assert 'Authorization' not in req.headers
                return httpx.Response(200,headers={'ETag':'"abc"'})
            return httpx.Response(200,json={'url':'qqbot-media://image/v1/verified','image_id':'img1'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            api=QQApi('id','secret',client)
            result=await api.upload_image(b'\x89PNG\r\n\x1a\nbytes')
        assert result['url']=='qqbot-media://image/v1/verified'
        assert len([r for r in seen if r.method=='PUT'])==1
    asyncio.run(run())


def test_long_permission_details_must_be_delivered_before_allow(tmp_path):
    async def run():
        bot,state,runtime=channel(tmp_path)
        state.set('owner','owner'); state.set('reply_msg_id','m1')
        req=pending(); req.payload={'pending_tool_call':{'arguments':{'command':'x'*5000}}}
        runtime.pending_inputs['main']=req
        bot.on_response('main',SimpleNamespace(content='',finish_reason='waiting_for_user_input'))
        code=bot.permission_code
        assert 'allow_once' not in state.pending_outbound()[0]['content']
        await bot.receive(packet(f'/permission {req.id} allow_once',message_id='early'))
        assert runtime.answers==[]
        for i in range(len(bot._detail_pages)):
            await bot.receive(packet(f'/permission-details {code} {i+1}',message_id=f'detail{i}'))
            state.outbound_result(state.pending_outbound()[-1]['id'])
        await bot.receive(packet(f'/permission {code} allow_once',message_id='approve'))
        assert runtime.answers==[('main',req.id,'allow_once')]
        state.close()
    asyncio.run(run())
