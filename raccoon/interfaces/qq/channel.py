"""Authenticated QQ text mapping; no agent construction or model prompts."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import secrets
import hashlib
import inspect
from dataclasses import asdict

from raccoon.interfaces.qq.messages import chunks, literal, reply_text

from raccoon.interfaces.qq.api import QQApiError
from raccoon.interfaces.qq.state import QQState
from raccoon.modules.events import EventSourceAdapter, EventError
from raccoon.runtime import AttachmentRef

log = logging.getLogger(__name__)


class QQChannel:
    def __init__(self, state: QQState, runtime, source: EventSourceAdapter, *,
                 pairing_code: str, pairing_expires: float, permission_commands=None,
                 allow_group_members: bool = False, image_input=None) -> None:
        self.state, self.runtime, self.source = state, runtime, source
        self.pairing_code, self.pairing_expires = pairing_code, pairing_expires
        self.permission_code = ''
        self._permission_identity = None
        self._detail_pages = []
        self._detail_rows = {}
        self.permission_commands = permission_commands
        self._requires_details = False
        self.allow_group_members = allow_group_members and state.target_kind == 'group'
        self.groups = None
        self.image_input = image_input

    async def receive(self, packet: dict) -> None:
        self.state.admit(packet)
        while await self.drain_one():
            pass

    async def drain_one(self) -> bool:
        row = self.state.next_inbound()
        if row is None:
            return False
        incoming = json.loads(row['packet'])
        if hasattr(self.runtime, 'refresh'):
            await self.runtime.refresh()
            self.source.main_session_id = self.runtime.state['session_id']
        if incoming.get('t') == 'GROUP_AT_MESSAGE_CREATE':
            if self.groups is not None:
                await self.groups.receive(incoming['d'])
        else:
            await self._handle(incoming['d'])
        self.state.inbound_done(row["id"])
        return True

    def _reply(self, openid: str, message_id: str, text: str) -> None:
        self.state.enqueue_text(openid, message_id, text)

    async def _handle(self, data: dict) -> None:
        sender = (data.get("author") or {}).get("user_openid")
        text = data.get("content", '')
        message_id = data.get("id")
        if not isinstance(sender, str) or not sender or not isinstance(text, str):
            return
        text = text.strip()
        owner = self.state.get("owner")
        if owner is None:
            if (time.time() < self.pairing_expires and text.startswith("/pair ")
                    and hmac.compare_digest(text[6:].strip().encode("utf-8"), self.pairing_code.encode("utf-8"))):
                self.state.set("owner", sender)
                self.state.set("reply_msg_id", message_id)
                self._reply(sender, message_id, "已绑定你的 Raccoon。现在可以直接发消息。")
                log.info("QQ owner paired")
            return
        if sender != owner:
            if not self.allow_group_members:
                return
            if text.startswith(('/pair','/permission','/ui-pair','/group-')) or text.startswith('/mode '):
                self._reply(sender,message_id,'只有绑定此群的本人可以修改模式或处理系统权限。')
                return
        self.state.set("reply_msg_id", message_id)
        if sender == owner:
            from raccoon.interfaces.qq.scheduling import schedule_command
            if await schedule_command(self, text, owner, message_id):
                return
        if text == '/group-code' and self.groups is not None:
            code = self.groups.issue_code(owner)
            self._reply(owner,message_id,'群绑定码十分钟有效、仅可使用一次。请你本人在目标群 @机器人 发送：\n/pair '+code)
            return
        if text.startswith("/pair "):
            self._reply(sender, message_id, "此机器人已绑定你，无需重复配对。")
            return
        if text == '/mode' or text.startswith('/mode '):
            self._mode_command(sender, message_id, text)
            return
        if text == "/status":
            pending = self.runtime.pending_inputs.get(self.source.main_session_id)
            if pending and pending.kind == 'permission_confirmation':
                self._permission_message(sender, message_id, pending)
            else:
                self._reply(sender, message_id, "Raccoon 在线。" + ("\n" + self._pending_text(pending) if pending else ""))
            return
        if text == '/ui-test':
            self.state.set('qq_keyboard_supported', None)
            self.state.set('qq_markdown_supported', None)
            self.state.enqueue_markdown(sender, message_id, '# QQ 卡片测试\n\n这是一条无副作用的测试消息。\n没有按钮时可回复 /status 或 /ui-test-ok。', buttons=[
                {'label': '查询状态', 'command': '/status'},
                {'label': '确认测试', 'command': '/ui-test-ok', 'confirm': '确认测试，不会批准任何工具'}])
            return
        if text == '/ui-test-ok':
            self._reply(sender, message_id, '按钮指令已收到，没有批准或执行工具。')
            return
        if text.startswith('/permission-details '):
            pending = self.runtime.pending_inputs.get(self.source.main_session_id)
            pieces = text.split()
            self._sync_permission(pending)
            try:
                page = int(pieces[2]) - 1
                if len(pieces) != 3 or pieces[1] != self.permission_code or not 0 <= page < len(self._detail_pages):
                    raise ValueError()
            except (ValueError, IndexError):
                self._reply(sender, message_id, '详情请求已失效，请发送 /status。')
                return
            body = f'权限详情 {page+1}/{len(self._detail_pages)}\n' + self._detail_pages[page]
            next_command = f'/permission-details {self.permission_code} {min(page+2, len(self._detail_pages))}'
            body += '\n\n下一页：' + next_command + '\n看完后发送 /status 查看可用选项。'
            self._detail_rows[page] = self.state.enqueue_markdown(sender, message_id, literal(body), fallback=body)
            return
        if text.startswith("/permission"):
            pieces = text.split()
            pending = self.runtime.pending_inputs.get(self.source.main_session_id)
            self._sync_permission(pending)
            if (len(pieces) != 3 or pending is None or pending.kind != "permission_confirmation"
                    or pieces[1] not in {pending.id + '@' + self.permission_code, self.permission_code} or pieces[2] not in {option.id for option in pending.options}):
                self._reply(sender, message_id, "权限回复无效或已过期，请发送 /status 获取当前命令。")
                return
            if pieces[2] != 'deny' and not self._details_delivered():
                self._reply(sender, message_id, '请先查看全部权限详情，再发送 /status 选择。')
                return
            try:
                answer = self.runtime.answer_run(self.source.main_session_id, pending.id, pieces[2])
                if inspect.isawaitable(answer):
                    await answer
            except EventError:
                self._reply(sender, message_id, "该权限请求已失效。")
            return
        pending = self.runtime.pending_inputs.get(self.source.main_session_id)
        if pending is not None and pending.kind == 'ask_user':
            if not text:
                self._reply(sender, message_id, '请用文字回答当前问题。')
                return
            answer = self.runtime.answer_run(self.source.main_session_id, pending.id, text)
            if inspect.isawaitable(answer):
                await answer
            return
        attachments = ()
        raw_attachments = data.get('attachments')
        if raw_attachments:
            if self.image_input is None:
                self._reply(sender, message_id, '当前 QQ 宿主未启用图片接收，请更新并重启。')
                return
            if not self.runtime.accepts_images(self.source.main_session_id):
                self._reply(sender, message_id, '当前模型未启用视觉能力，无法查看图片。请在本机选择支持图片的模型。')
                return
            cache_key = 'input_attachments:' + hashlib.sha256(message_id.encode()).hexdigest()
            try:
                cached = self.state.get(cache_key)
                if cached is not None:
                    attachments = tuple(AttachmentRef(**item) for item in cached)
                else:
                    attachments = await self.image_input.prepare(raw_attachments)
                    self.state.set(cache_key, [asdict(item) for item in attachments])
            except ValueError as error:
                self._reply(sender, message_id, str(error))
                return
            except Exception as error:
                log.warning('QQ image input failed (%s)', type(error).__name__)
                self._reply(sender, message_id, '图片接收或保存失败，请重新 @机器人 并发送图片。')
                return
        if not text and not attachments:
            return
        if not text:
            text = '请查看所附图片。'
        group_id = self.state.target_id if self.state.target_kind == 'group' else None
        if group_id:
            label = str(data.get('author',{}).get('username') or ('成员 '+hashlib.sha256(sender.encode()).hexdigest()[:6]))[:40]
            text = f'群成员 {json.dumps(label,ensure_ascii=False)}：\n{text}'
        accepted = self.runtime.submit_event(self.source.user_message(source='qq-group' if group_id else 'qq', message_id=message_id,
            conversation_id=group_id or sender, text=text, attachments=attachments))
        if inspect.isawaitable(accepted):
            await accepted

    def _sync_permission(self, pending) -> None:
        identity = (pending.id, json.dumps(pending.payload, sort_keys=True, ensure_ascii=False),
                    pending.question, tuple((o.id, o.label) for o in pending.options)) if pending and pending.kind == 'permission_confirmation' else None
        if identity != self._permission_identity:
            self._permission_identity = identity
            key = 'main-permission:' + self.source.main_session_id
            serialized = json.dumps(identity, ensure_ascii=False) if identity else ''
            saved = self.state.get(key, {})
            self.permission_code = (saved.get('code') if saved.get('identity') == serialized else None) or (secrets.token_hex(4) if identity else '')
            self.state.set(key, {'identity': serialized, 'code': self.permission_code})
            self._detail_rows = {}
            from raccoon.application.delivery.permissions import permission_presentation
            detail = permission_presentation(pending)['details'] if identity else ''
            review = pending.payload.get('prewrite_review') if identity else None
            if isinstance(review, dict):
                detail += '\n\n' + str(review.get('error') or '\n\n'.join(str(f.get('path',''))+'\n'+str(f.get('diff','')) for f in review.get('files',[])))
            self._detail_pages = chunks(detail) if detail else []
            self._requires_details = len(detail.encode('utf-8')) > 1400

    def _details_delivered(self) -> bool:
        return not self._requires_details or all(i in self._detail_rows and self.state.delivered(self._detail_rows[i]) for i in range(len(self._detail_pages)))

    def _permission_message(self, owner, message_id, pending) -> None:
        from raccoon.application.delivery.permissions import permission_presentation
        self._sync_permission(pending)
        view = permission_presentation(pending)
        summary = view['title'] + ('\n' + view['target'] if view['target'] else '')
        if view['description']:
            summary += '\n' + view['description']
        if not self._details_delivered():
            summary += f'\n请先查看完整操作详情（{len(self._detail_pages)} 页）。'
        options = [o for o in view['options'] if o['id'] == 'deny' or self._details_delivered()]
        buttons = [{'label': o['label'], 'command': f"/permission {pending.id}@{self.permission_code} {o['id']}"} for o in options]
        fallback = summary + '\n\n' + '\n'.join(f"{o['label']}: /permission {self.permission_code} {o['id']}" for o in options)
        if self._detail_pages:
            command = f'/permission-details {self.permission_code} 1'
            buttons.append({'label':'查看详情','command':command})
            fallback += '\n查看详情: ' + command
        visible = fallback if self.state.get('qq_keyboard_supported') is False else summary
        if len(buttons) <= 25 and len(fallback.encode('utf-8')) <= 3800:
            self.state.enqueue_markdown(owner, message_id, literal(visible), buttons=buttons, fallback=fallback)
        else:
            self.state.enqueue_text(owner, message_id, fallback)

    def _mode_command(self, owner, message_id, text) -> None:
        if self.permission_commands is None:
            self._reply(owner, message_id, '正在转交主会话，请先完成当前待确认请求，转交后可切换权限模式。' if getattr(self, 'handoff_wait', False)
                        else '此宿主尚未接入权限模式，请更新并重启。')
            return
        pieces = text.split()
        if len(pieces) > 2 or (len(pieces) == 2 and pieces[1] not in {'ask','standard','auto','aggressive','bypass'}):
            self._reply(owner, message_id, '用法：/mode ask、/mode auto 或 /mode bypass')
            return
        if len(pieces) == 2:
            if self.source.main_session_id in self.runtime.pending_inputs:
                self._reply(owner, message_id, '请先处理当前权限或提问，再切换模式。切换模式不会批准当前请求。')
                return
            if self.runtime.busy(self.runtime.key_for_session(self.source.main_session_id)):
                self._reply(owner, message_id, '主会话正在执行，请完成后再切换模式。')
                return
            self.permission_commands.handle(text)
        mode = self.permission_commands.session.mode
        if mode == 'default':
            mode = 'ask'
        names = {'ask':'请求批准','auto':'自动审核','bypass':'全放行'}
        restart_mode = getattr(self,'restart_mode','ask')
        body = f'当前权限模式：{names.get(mode,mode)}（{mode}）\n请求批准：项目内普通读取自动允许，其他操作按规则询问。\n自动审核：模型审核待批准操作，不确定时询问。\n全放行：跳过权限询问。\n仅影响当前 QQ 会话；重启恢复{names.get(restart_mode,restart_mode)}。'
        buttons = [{'label':names[key], 'command':'/mode '+key} for key in names]
        fallback = body + '\n切换：/mode ask | /mode auto | /mode bypass'
        visible = fallback if self.state.get('qq_keyboard_supported') is False else body
        self.state.enqueue_markdown(owner,message_id,literal(visible),buttons=buttons,fallback=fallback)

    @staticmethod
    def _pending_text(pending) -> str:
        text = pending.question
        if pending.kind == "permission_confirmation":
            text += "\n请明确选择并发送完整命令：\n" + "\n".join(
                f"{option.label}: /permission {pending.id} {option.id}" for option in pending.options)
        elif pending.options:
            text += "\n" + "\n".join(f"{o.id}: {o.label}" for o in pending.options)
        return text

    def on_response(self, session_id: str, response, *, route=None) -> None:
        if session_id != self.source.main_session_id:
            raise ValueError("QQ response session mismatch")
        owner, msg_id = route or (self.state.get("owner"), self.state.get("reply_msg_id"))
        if not owner or (not msg_id and route is None):
            raise ValueError("QQ response lacks an authenticated reply route")
        if not msg_id and self.state.get('owner') != owner:
            raise ValueError('QQ 绑定已变化，未向其他人发送')
        pending = self.runtime.pending_inputs.get(session_id)
        if response.finish_reason == 'waiting_for_user_input' and pending and pending.kind == 'permission_confirmation':
            self._permission_message(owner, msg_id, pending)
            return
        text = (self._pending_text(pending) if response.finish_reason == "waiting_for_user_input" and pending
                else reply_text(response.content))
        self._reply(owner, msg_id, text)

    async def intake_loop(self) -> None:
        while True:
            try:
                self.report_failures()
                if await self.drain_one():
                    continue
            except Exception as error:
                log.warning("QQ inbound retained for retry (%s)", type(error).__name__)
            await asyncio.sleep(0.25)

    def report_failures(self) -> None:
        if not hasattr(self.runtime, 'history'):
            return
        key = self.runtime.key_for_session(self.source.main_session_id)
        for receipt in self.runtime.history(key):
            if receipt.status != 'failed':
                continue
            delivery = receipt.event.payload.get('delivery', {})
            if receipt.event.type == 'timer.trigger' and delivery.get('channel') == 'qq':
                owner, msg_id = delivery['target'], ''
                if owner != self.state.get('owner'):
                    continue
            else:
                owner, msg_id = self.state.get('owner'), self.state.get('reply_msg_id')
                if not owner or not msg_id:
                    continue
            self.state.enqueue_failure_notice(receipt.event.id, owner, msg_id)

    async def sender_loop(self, api, online: asyncio.Event) -> None:
        while True:
            await online.wait()
            for row in self.state.pending_outbound():
                if not await self.deliver_one(api, row):
                    break
            await asyncio.sleep(1)

    async def deliver_one(self, api, row) -> bool:
        try:
            payload = json.loads(row['payload']) if row['payload'] else None
            if row['payload'] and (not isinstance(payload, dict) or payload.get('version') != 1):
                raise ValueError('Unsupported outbox record')
        except ValueError:
            self.state.outbound_result(row['id'], error='Invalid outbox payload', permanent=True)
            return False
        try:
            if row['target_kind'] == 'group':
                await api.send_group_message(row['openid'],row['msg_id'],row['msg_seq'],
                    payload or {'msg_type':0,'content':row['content']})
            elif not row['msg_id']:
                if self.state.get('owner') != row['openid']:
                    self.state.outbound_result(row['id'], error='QQ 绑定已变化', permanent=True)
                    return False
                payload = None  # Proactive output uses plain text, including approval fallback commands.
                await api.send_proactive_text(row['openid'], row['content'])
            elif payload:
                await api.send_message(row['openid'], row['msg_id'], row['msg_seq'], payload)
            else:
                await api.send_text(row['openid'], row['msg_id'], row['msg_seq'], row['content'])
        except QQApiError as error:
            if payload and payload.get('msg_type') == 2 and error.code in {304057, 40034011, 40034124, 40034127}:
                if error.code == 304057 and 'keyboard' in payload:
                    payload.pop('keyboard')
                    payload['markdown']['content'] = literal(row['content'])
                    self.state.set('qq_keyboard_supported', False)
                else:
                    payload = {'version': 1, 'msg_type': 0, 'content': row['content']}
                    if error.code == 40034127:
                        self.state.set('qq_markdown_supported', False)
                self.state.replace_payload(row['id'], payload)
                return False
            permanent = 400 <= error.status < 500 and error.status not in (401, 408, 429)
            self.state.outbound_result(row['id'], error=str(error), permanent=permanent)
            log.warning('QQ reply not delivered: %s', error)
            return False
        except Exception as error:
            self.state.outbound_result(row['id'], error=type(error).__name__)
            return False
        self.state.outbound_result(row['id'])
        return True
