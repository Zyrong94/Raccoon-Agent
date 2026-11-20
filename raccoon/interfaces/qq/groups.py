"""QQ group binding, commands and delivery adapter."""
import asyncio
import hashlib
import logging
from pathlib import Path
import secrets
import time
from raccoon.application.conversations.group_chat import GroupChatManager
from raccoon.modules.permissions.types import PermissionMode
log = logging.getLogger(__name__)

class QQGroups:
    def __init__(self, state, *, project: Path, data_root: Path, runtime_factory, context_factory,
                 members: str = 'owner', mode: str = 'ask', clock=time.time, media_client=None):
        if members not in {'owner','all'}:
            raise ValueError('QQ group members must be owner or all')
        self.state, self.project, self.data_root = state, project, data_root
        self.factory, self.members, self.mode, self.clock = runtime_factory, members, PermissionMode(mode), clock
        self.media_client = media_client
        self.hosts = GroupChatManager(data_root, create_context=lambda row, root: context_factory(
            row, root, project=self.project, runtime_factory=self.factory, mode=self.mode,
            members=self.members, media_client=self.media_client))
        self.contexts, self.failed = self.hosts.contexts, self.hosts.failed
        self.state.db.executescript('''
            CREATE TABLE IF NOT EXISTS qq_groups (
                group_key TEXT PRIMARY KEY, group_openid TEXT NOT NULL UNIQUE,
                private_owner TEXT NOT NULL, member_owner TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS qq_group_codes (
                digest TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
        ''')
        if 'title' not in {row[1] for row in self.state.db.execute('PRAGMA table_info(qq_groups)')}:
            self.state.db.execute("ALTER TABLE qq_groups ADD COLUMN title TEXT NOT NULL DEFAULT ''")
            self.state.db.commit()

    def bindings(self):
        return self.state.db.execute('SELECT * FROM qq_groups WHERE private_owner=? ORDER BY group_key',
                                     (self.state.get('owner'),)).fetchall()

    def issue_code(self, owner: str) -> str:
        if not owner or owner != self.state.get('owner'):
            raise ValueError('Group codes require the paired private owner')
        code = secrets.token_hex(8)
        with self.state.db:
            self.state.db.execute('DELETE FROM qq_group_codes WHERE expires<=?',(self.clock(),))
            self.state.db.execute('INSERT INTO qq_group_codes VALUES (?,?,?)',
                                 (hashlib.sha256(code.encode()).hexdigest(),owner,self.clock()+600))
        return code

    def _reply(self, group_id, msg_id, text):
        self.state.enqueue_text(group_id,msg_id,text,target_kind='group')

    async def receive(self, data: dict):
        group_id, msg_id, text = data.get('group_openid'),data.get('id'),data.get('content', '')
        author = data.get('author') or {}
        member = author.get('member_openid')
        if (not all(isinstance(value,str) and value and len(value)<=256 for value in (group_id,msg_id,member))
                or not isinstance(text,str) or author.get('bot')):
            return
        text = text.strip()
        if not text and not data.get('attachments'):
            return
        key = hashlib.sha256(group_id.encode()).hexdigest()
        row = self.state.db.execute('SELECT * FROM qq_groups WHERE group_key=?',(key,)).fetchone()
        if text.startswith('/pair '):
            digest = hashlib.sha256(text[6:].strip().encode()).hexdigest()
            with self.state.db:
                token = self.state.db.execute('SELECT * FROM qq_group_codes WHERE digest=?',(digest,)).fetchone()
                if token is not None and token['expires']>self.clock() and token['owner']==self.state.get('owner') and row is None:
                    self.state.db.execute('INSERT INTO qq_groups(group_key,group_openid,private_owner,member_owner) VALUES (?,?,?,?)',(key,group_id,token['owner'],member))
                    self.state.db.execute('DELETE FROM qq_group_codes WHERE digest=?',(digest,))
                    self._reply(group_id,msg_id,'此群已绑定。群成员可 @机器人 聊天；模式由本机群配置决定。历史按群独立保存，可在 Web 的“群聊记录”查看。')
                else:
                    self._reply(group_id,msg_id,'群绑定码无效、已过期，或此群已绑定。请本人先私聊机器人发送 /group-code。')
            return
        if row is None or row['private_owner']!=self.state.get('owner'):
            self._reply(group_id,msg_id,'此群尚未绑定。请机器人已配对本人先私聊发送 /group-code，再到群里 @机器人 /pair 绑定码。')
            return
        if member!=row['member_owner'] and (self.members!='all' or text.startswith(('/permission','/mode ','/group-','/ui-pair'))):
            if self.members=='all':
                self._reply(group_id,msg_id,'此操作仅允许绑定本群的本人执行。')
            return
        if text == '/group-new':
            old = self.contexts.pop(key,None)
            if old is not None:
                await old.close()
            from raccoon.interfaces.qq.session import prepare_main_session
            _, queue = prepare_main_session(self._root(key),new_session=True,private_only=False)
            await queue.close()
            self.failed.discard(key)
            self._reply(group_id,msg_id,'已新建本群会话。旧记录保留，旧任务不再继续。其他群和私聊不受影响。')
            return
        if text.startswith('/group-name '):
            title = text[12:].strip()
            if not 1<=len(title)<=60 or any(ord(c)<32 for c in title):
                self._reply(group_id,msg_id,'群名称需要 1～60 个字符，不能包含换行。')
                return
            with self.state.db:
                self.state.db.execute('UPDATE qq_groups SET title=? WHERE group_key=?',(title,key))
            self._reply(group_id,msg_id,'群聊记录名称已更新：'+title)
            return
        try:
            context = await self._context(row)
        except Exception:
            self.failed.add(key)
            # Keep the traceback local: the reply stays generic, diagnosis needs the stack.
            log.exception('QQ group conversation unavailable')
            self._reply(group_id,msg_id,'本群会话暂不可用或旧任务中断。请本人检查本机日志，确认放弃旧任务后 @机器人 /group-new。')
            return
        converted = {**data,'content':text,'author':{**author,'user_openid':member}}
        await context.channel._handle(converted)

    def _root(self, key):
        return self.hosts._root(key)

    async def _context(self, row):
        return await self.hosts.get(row)

    async def restore(self):
        for row in self.bindings():
            root = self._root(row['group_key'])
            if not (root/'channels'/'main_session.json').exists():
                continue
            try:
                await self._context(row)
            except Exception as error:
                self.failed.add(row['group_key'])
                log.warning('QQ group recovery required (%s)',type(error).__name__)

    async def sender_loop(self, api, online):
        while True:
            await online.wait()
            for context in list(self.contexts.values()):
                async with context.delivery_lock:
                    if context.closed:
                        continue
                    context.channel.report_failures()
                    for row in context.state.pending_outbound():
                        if not await context.channel.deliver_one(api,row):
                            break
            await asyncio.sleep(1)

    async def close(self):
        await self.hosts.close()
