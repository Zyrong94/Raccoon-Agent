"""Fork existing sessions into new editable sessions."""

from __future__ import annotations

import shutil
import os
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from raccoon.modules.sessions.events import SessionEvent
from raccoon.modules.sessions.thread_data import default_thread_data
from raccoon.modules.sessions.identity import new_event_id, new_session_id
from raccoon.modules.sessions.store import SessionStore
from raccoon.app.payloads import AttachmentRef
from raccoon.modules.sessions.catalog import SessionCatalog, require_usable_record
from raccoon.modules.sessions.errors import SessionNotFoundError
from raccoon.modules.sessions.models import SessionData


def fork_session(
    store: SessionStore,
    project_root: str | Path,
    source_session_id: str,
    *,
    title: str | None = None,
    before_message_id: str | None = None,
    metadata: dict | None = None,
) -> str:
    store.load(source_session_id)
    catalog = SessionCatalog(store.root)
    record = require_usable_record(catalog.get_session(source_session_id))

    events = store.list_events(source_session_id)
    if not events:
        raise SessionNotFoundError(f"session not found: {source_session_id}")
    if before_message_id is not None:
        cutoff = next(
            (
                index
                for index, event in enumerate(events)
                if event.type == "user_message" and event.payload.get("message_id") == before_message_id
            ),
            None,
        )
        if cutoff is None:
            raise ValueError("只能从当前会话中的真实用户消息建立版本")
        # Rebuild retained raw messages, rather than replaying compressed projections.
        events = [
            event
            for event in events[:cutoff]
            if event.type not in {"context_compacted", "compaction_started", "compaction_failed"}
        ]

    forked_session_id = new_session_id()
    forked_events = [_fork_event(event, source_session_id, forked_session_id) for event in events]
    if before_message_id is not None:
        _copy_user_attachments(store, forked_events, source_session_id, forked_session_id)
    if before_message_id is None:
        _copy_official_apps(store, project_root, source_session_id, forked_session_id)
    data = SessionData(session_id=forked_session_id)
    for event in forked_events:
        store.append_event(data, event.type, event.payload, created_at=event.created_at)
    store.append_session_metadata_updated(
        data,
        forked_from=source_session_id,
        title=title or f"Fork of {record.title}",
        thread_data=default_thread_data(
            store_root=store.root,
            session_id=forked_session_id,
            project_root=Path(project_root),
        ),
        **(metadata or {}),
    )

    return forked_session_id


def _copy_user_attachments(store, events, source_session_id, destination_session_id):
    from raccoon.modules.sessions.user_attachments import (
        prepare_queued_attachments,
        resolve_queued_attachments,
    )

    for event in events:
        if event.type != "user_message":
            continue
        parts = [part for part in event.payload.get("parts", []) if part.get("kind") in {"image", "file"}]
        refs = tuple(_user_attachment_ref(part) for part in parts)
        files = resolve_queued_attachments(refs, store_root=store.root, session_id=source_session_id)
        copied = prepare_queued_attachments(files, store_root=store.root, session_id=destination_session_id)
        for part, ref, original in zip(parts, copied, refs):
            if ref.sha256 != original.sha256 or ref.size_bytes != original.size_bytes:
                raise ValueError("附件内容在复制期间已变化")
            part["metadata"]["path"] = ref.relative_path


def _copy_official_apps(store, project_root, source_session_id: str, forked_session_id: str) -> None:
    workspace_id = sha256(os.path.normcase(str(Path(project_root).resolve())).encode()).hexdigest()[:16]
    root = store.root / "official-mcp-apps" / workspace_id
    source = root / source_session_id / "state.bin"
    if source.is_file():
        destination = root / forked_session_id
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination / "state.bin")


def _fork_event(event: SessionEvent, source_session_id: str, forked_session_id: str) -> SessionEvent:
    payload = deepcopy(event.payload)
    if event.type in {"session_created", "session_metadata_updated"}:
        # A separate /fork is not the same accepted Web revision request/family.
        payload.pop("web_message_revision", None)
    if payload.get("session_id") == source_session_id:
        payload["session_id"] = forked_session_id
    return SessionEvent(
        id=new_event_id(),
        session_id=forked_session_id,
        type=event.type,
        payload=payload,
        created_at=event.created_at,
    )


def _user_attachment_ref(part):
    meta = part.get("metadata", {})
    try:
        return AttachmentRef(
            kind=part["kind"],
            relative_path=meta["path"],
            filename=meta["filename"],
            media_type=meta["media_type"],
            size_bytes=meta["bytes"],
            sha256=meta["sha256"],
            source=meta["source"],
        )
    except (KeyError, TypeError) as error:
        raise ValueError("附件记录不完整，请核对原消息") from error
