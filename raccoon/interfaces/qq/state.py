"""QQ delivery metadata, not a second conversation history."""

from __future__ import annotations

import json
import sqlite3
import hashlib
from contextlib import nullcontext
from pathlib import Path


class QQState:
    def __init__(self, path: Path, *, target_kind: str = 'user', target_id: str | None = None) -> None:
        if target_kind not in {'user','group'} or (target_kind=='group' and not target_id):
            raise ValueError('Invalid QQ delivery destination')
        self.target_kind, self.target_id = target_kind, target_id
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inbound (
                id TEXT PRIMARY KEY, packet TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS outbound (
                id INTEGER PRIMARY KEY, openid TEXT NOT NULL, msg_id TEXT NOT NULL,
                msg_seq INTEGER NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, error TEXT);
            CREATE TABLE IF NOT EXISTS reply_sequence (msg_id TEXT PRIMARY KEY, seq INTEGER NOT NULL);
        """)
        if 'payload' not in {row[1] for row in self.db.execute('PRAGMA table_info(outbound)')}:
            self.db.execute('ALTER TABLE outbound ADD COLUMN payload TEXT')
            self.db.commit()
        if 'target_kind' not in {row[1] for row in self.db.execute('PRAGMA table_info(outbound)')}:
            self.db.execute("ALTER TABLE outbound ADD COLUMN target_kind TEXT NOT NULL DEFAULT 'user'")
            self.db.commit()

    def close(self) -> None:
        self.db.close()

    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(value)))

    def set(self, key: str, value) -> None:
        with self.db:
            self._set(key, value)

    def admit(self, packet: dict) -> None:
        """Durable admission and resume checkpoint must commit together."""
        with self.db:
            if packet.get("t") in {'C2C_MESSAGE_CREATE','GROUP_AT_MESSAGE_CREATE'}:
                message_id = packet["d"].get("id")
                if not isinstance(message_id, str) or not message_id:
                    raise ValueError("QQ message lacks ID")
                identity = message_id
                if packet['t'] == 'GROUP_AT_MESSAGE_CREATE':
                    group = packet['d'].get('group_openid')
                    if not isinstance(group,str) or not group or len(group)>256:
                        raise ValueError('QQ group message lacks group ID')
                    identity = 'group:' + hashlib.sha256(group.encode()).hexdigest() + ':' + message_id
                self.db.execute("INSERT OR IGNORE INTO inbound(id,packet) VALUES (?,?)",
                                (identity, json.dumps(packet, ensure_ascii=False)))
            if packet.get("t") == "READY":
                self._set("gateway_session", packet["d"]["session_id"])
            if isinstance(packet.get("s"), int):
                self._set("gateway_seq", packet["s"])

    def next_inbound(self):
        return self.db.execute("SELECT id,packet FROM inbound WHERE done=0 ORDER BY rowid LIMIT 1").fetchone()

    def inbound_done(self, message_id: str) -> None:
        with self.db:
            self.db.execute("UPDATE inbound SET done=1 WHERE id=?", (message_id,))

    def abandon_pending(self, *, private_only: bool = False) -> None:
        """Explicit main-session rotation keeps records but drops old deliveries."""
        with self.db:
            if private_only:
                private_ids = [(row['id'],) for row in self.db.execute('SELECT id,packet FROM inbound WHERE done=0')
                               if json.loads(row['packet']).get('t')=='C2C_MESSAGE_CREATE']
                self.db.executemany('UPDATE inbound SET done=2 WHERE id=?',private_ids)
                self.db.execute("UPDATE outbound SET status='abandoned' WHERE status='pending' AND target_kind='user'")
            else:
                self.db.execute("UPDATE inbound SET done=2 WHERE done=0")
                self.db.execute("UPDATE outbound SET status='abandoned' WHERE status='pending'")

    def enqueue_text(self, openid: str, msg_id: str, content: str, *, target_kind: str | None = None) -> None:
        if not content:
            return
        with self.db:
            row = self.db.execute("SELECT seq FROM reply_sequence WHERE msg_id=?", (msg_id,)).fetchone()
            seq = row[0] if row else 0
            # Leave margin below QQ's text limit, including for UTF-8 Chinese text.
            chunks, current, size = [], [], 0
            for char in content:
                width = len(char.encode("utf-8"))
                if size + width > 2000:
                    chunks.append("".join(current)); current, size = [], 0
                current.append(char); size += width
            if current:
                chunks.append("".join(current))
            for chunk in chunks:
                seq += 1
                self.db.execute("INSERT INTO outbound(openid,msg_id,msg_seq,content,target_kind) VALUES (?,?,?,?,?)",
                                (self.target_id or openid, msg_id, seq, chunk, target_kind or self.target_kind))
            self.db.execute("INSERT OR REPLACE INTO reply_sequence VALUES (?,?)", (msg_id, seq))

    def pending_outbound(self):
        return self.db.execute("SELECT * FROM outbound WHERE status='pending' ORDER BY id").fetchall()

    def enqueue_markdown(self, openid: str, msg_id: str, content: str, *, buttons=None, fallback: str | None = None) -> int:
        from raccoon.interfaces.qq.messages import markdown_message
        payload = markdown_message(content, buttons if self.get('qq_keyboard_supported') is not False else None)
        if self.get('qq_markdown_supported') is False:
            payload = {'version': 1, 'msg_type': 0, 'content': fallback or content}
        return self.enqueue_payload(openid, msg_id, payload, fallback or content)

    def enqueue_payload(self, openid: str, msg_id: str, payload: dict, fallback: str) -> int:
        with nullcontext() if self.db.in_transaction else self.db:
            row = self.db.execute('SELECT seq FROM reply_sequence WHERE msg_id=?', (msg_id,)).fetchone()
            seq = (row[0] if row else 0) + 1
            cursor = self.db.execute('INSERT INTO outbound(openid,msg_id,msg_seq,content,payload,target_kind) VALUES (?,?,?,?,?,?)',
                (self.target_id or openid, msg_id, seq, fallback, json.dumps(payload, ensure_ascii=False),self.target_kind))
            self.db.execute('INSERT OR REPLACE INTO reply_sequence VALUES (?,?)', (msg_id, seq))
        return cursor.lastrowid

    def replace_payload(self, row_id: int, payload: dict) -> None:
        with self.db:
            self.db.execute('UPDATE outbound SET payload=? WHERE id=? AND status=\'pending\'',
                            (json.dumps(payload, ensure_ascii=False), row_id))

    def delivered(self, row_id: int) -> bool:
        row = self.db.execute('SELECT status FROM outbound WHERE id=?', (row_id,)).fetchone()
        return row is not None and row[0] == 'sent'

    def outbound_result(self, row_id: int, *, error: str | None = None, permanent: bool = False) -> None:
        with self.db:
            if error is None:
                self.db.execute("UPDATE outbound SET status='sent', error=NULL WHERE id=?", (row_id,))
            else:
                self.db.execute("""UPDATE outbound SET attempts=attempts+1, error=?,
                    status=CASE WHEN ? OR attempts>=4 THEN 'failed' ELSE 'pending' END WHERE id=?""",
                                (error, permanent, row_id))

    def enqueue_failure_notice(self, event_id: str, openid: str, msg_id: str) -> None:
        key = "failure_notice:" + event_id
        if self.get(key):
            return
        # Mark only after the notice is durable. A crash here may repeat a notice,
        # but cannot replay the failed agent tools.
        self.enqueue_text(openid, msg_id, "本次执行未完成。请在电脑查看 Raccoon 的本地事件记录后再决定下一步。")
        self.set(key, True)
