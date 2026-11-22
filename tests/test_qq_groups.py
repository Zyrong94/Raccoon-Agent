"""Real local group state and schedulers; no credentials or live QQ traffic."""

import asyncio
import json

import httpx
import pytest

from raccoon.interfaces.qq.state import QQState
from raccoon.bootstrap.group_chat import QQGroups
from raccoon.bootstrap.runtime import create_runtime
from raccoon.interfaces.qq.api import QQApi
from tests.runtime_fixtures import FakeMcpManager
from tests.scripted_providers import FakeProvider, response
from tests.qq_fixtures import group_packet, packet


def test_group_inbox_deduplicates_by_group_without_colliding_with_private(tmp_path):
    state=QQState(tmp_path/'qq.db')
    try:
        state.admit(packet(message_id='same'))
        state.admit(group_packet(group='a',message_id='same'))
        state.admit(group_packet(group='a',message_id='same'))
        state.admit(group_packet(group='b',message_id='same'))
        state.admit(group_packet(group='c',message_id='same',kind='GROUP_MESSAGE_CREATE'))
        assert state.db.execute('SELECT count(*) FROM inbound').fetchone()[0]==3
    finally:
        state.close()


def test_group_outbox_keeps_destination_after_reopen(tmp_path):
    state=QQState(tmp_path/'group.db',target_kind='group',target_id='group-a')
    state.enqueue_text('member-owner','m1','群文字')
    state.enqueue_markdown('teammate','m2','群卡片',buttons=[{'label':'状态','command':'/status'}])
    state.close()
    reopened=QQState(tmp_path/'group.db')
    try:
        assert [(row['target_kind'],row['openid']) for row in reopened.pending_outbound()]==[('group','group-a'),('group','group-a')]
    finally:
        reopened.close()


def test_group_api_uses_group_endpoint_and_removes_private_only_enter():
    async def scenario():
        sent=[]
        def handler(req):
            if req.url.host=='bots.qq.com':return httpx.Response(200,json={'access_token':'fake','expires_in':7200})
            sent.append(req);return httpx.Response(200,json={'id':'reply'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            from raccoon.interfaces.qq.messages import markdown_message
            api=QQApi('fake','fake',client)
            await api.send_group_message('g/a','m1',2,markdown_message('测试',[{'label':'状态','command':'/status'}]))
        assert sent[0].url.raw_path==b'/v2/groups/g%2Fa/messages'
        body=json.loads(sent[0].content)
        assert body['msg_id']=='m1' and body['msg_seq']==2 and body['msg_type']==2
        assert 'enter' not in body['keyboard']['content']['rows'][0]['buttons'][0]['action']
    asyncio.run(scenario())


def test_group_code_expiry_reuse_and_wrong_private_owner(tmp_path):
    async def scenario():
        state=QQState(tmp_path/'channels/qq.sqlite3');state.set('owner','private-owner')
        clock=[100.]
        groups=QQGroups(state,project=tmp_path,data_root=tmp_path,clock=lambda:clock[0])
        try:
            with pytest.raises(ValueError):groups.issue_code('other-owner')
            code=groups.issue_code('private-owner');clock[0]+=601
            await groups.receive(group_packet('/pair '+code)['d'])
            assert not groups.bindings()
            code=groups.issue_code('private-owner')
            await groups.receive(group_packet('/pair '+code)['d'])
            assert len(groups.bindings())==1
            await groups.receive(group_packet('/pair '+code,group='group-b',member='another')['d'])
            assert len(groups.bindings())==1 and not groups.contexts
        finally:
            await groups.close();state.close()
    asyncio.run(scenario())


def test_private_rotation_preserves_pending_group_deliveries(tmp_path):
    from raccoon.interfaces.qq.session import prepare_main_session
    root=tmp_path/'channels'
    state=QQState(root/'qq.sqlite3')
    state.admit(packet('private',message_id='private-pending'))
    state.admit(group_packet('group',message_id='group-pending'))
    state.enqueue_text('private-owner','p1','private reply')
    state.enqueue_text('group-a','g1','group reply',target_kind='group')
    state.close()
    _, service = prepare_main_session(tmp_path, new_session=True)
    asyncio.run(service.close())
    state=QQState(root/'qq.sqlite3')
    try:
        inbound=state.next_inbound()
        assert inbound and json.loads(inbound['packet'])['t']=='GROUP_AT_MESSAGE_CREATE'
        assert [r['target_kind'] for r in state.pending_outbound()]==['group']
    finally:
        state.close()





def test_group_chat_keeps_replies_and_session_history_separate(tmp_path):
    def factory(**kwargs):
        assert kwargs["app_config"].project_config["memory"]["enabled"] is False
        return create_runtime(
            **kwargs, provider=FakeProvider([response("Group reply")]), tools=[],
            mcp_manager_factory=lambda _: FakeMcpManager(),
        )

    async def scenario():
        state = QQState(tmp_path / "channels" / "qq.sqlite3")
        state.set("owner", "private-owner")
        groups = QQGroups(state, project=tmp_path, data_root=tmp_path, runtime_factory=factory, members="all")
        try:
            for group, text in (("a", "Only for A"), ("b", "Only for B")):
                code = groups.issue_code("private-owner")
                await groups.receive(group_packet(f"/pair {code}", group=group)["d"])
                await groups.receive(group_packet(text, group=group, member="teammate", message_id="talk")["d"])
            contexts = {context.group_id: context for context in groups.contexts.values()}
            assert contexts["a"].session_id != contexts["b"].session_id
            for group, context in contexts.items():
                key = context.runtime.key_for_session(context.session_id)
                await asyncio.wait_for(context.runtime.wait_for_event(context.runtime.history(key)[0]), timeout=5)
                reply = context.state.pending_outbound()[-1]
                assert reply["openid"] == group and reply["target_kind"] == "group"
                assert reply["content"] == "Group reply"
                history = context.bundle.session.store.load(context.session_id).messages
                user = next(message for message in history if message.role == "user")
                assert f"Only for {group.upper()}" in user.parts[0].content
            assert state.get("reply_msg_id") is None
        finally:
            await groups.close()
            state.close()

    asyncio.run(scenario())


def test_only_the_bound_group_member_can_approve_a_real_write(tmp_path):
    from raccoon.modules.tools.write import create_write_tool
    from raccoon.runtime.model_types import ToolCall

    def factory(**kwargs):
        provider = FakeProvider([
            response("", calls=[ToolCall("write", "write", {"path": "note.txt", "content": "approved"})]),
            response("Done"),
        ])
        return create_runtime(
            **kwargs, provider=provider, tools=[create_write_tool(tmp_path)],
            mcp_manager_factory=lambda _: FakeMcpManager(),
        )

    async def scenario():
        state = QQState(tmp_path / "channels" / "qq.sqlite3")
        state.set("owner", "private-owner")
        groups = QQGroups(state, project=tmp_path, data_root=tmp_path, runtime_factory=factory, members="all")
        try:
            await groups.receive(group_packet("/pair " + groups.issue_code("private-owner"))["d"])
            await groups.receive(group_packet("Write a note", member="teammate", message_id="start")["d"])
            context = next(iter(groups.contexts.values()))
            async with asyncio.timeout(5):
                while not context.channel.permission_code:
                    await asyncio.sleep(0.01)
            command = f"/permission {context.channel.permission_code} allow_once"
            await groups.receive(group_packet(command, member="teammate", message_id="wrong")["d"])
            assert context.session_id in context.runtime.pending_inputs
            assert not (tmp_path / "note.txt").exists()

            for page in range(1, len(context.channel._detail_pages) + 1):
                detail = f"/permission-details {context.channel.permission_code} {page}"
                await groups.receive(group_packet(detail, message_id=f"details-{page}")["d"])
                context.state.outbound_result(context.state.pending_outbound()[-1]["id"])
            await groups.receive(group_packet(command, message_id="approved")["d"])
            key = context.runtime.key_for_session(context.session_id)
            await asyncio.wait_for(context.runtime.wait_for_event(context.runtime.history(key)[0]), timeout=5)
            assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "approved"
        finally:
            await groups.close()
            state.close()

    asyncio.run(scenario())
