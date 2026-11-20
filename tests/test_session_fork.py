"""Fork the recorded facts and reject a log the current store cannot read."""

import json

import pytest

from raccoon.modules.sessions.errors import SessionUnsupportedSchemaError
from raccoon.modules.sessions.fork import fork_session
from raccoon.modules.sessions.store import SessionStore


def test_fork_copies_only_the_prefix_before_an_edited_message(tmp_path):
    store = SessionStore(tmp_path / ".raccoon")
    source = store.create(title="Original")
    store.append_user_message(source, "Keep this message", user_timezone="UTC")
    edited = store.append_user_message(source, "Edit this message", user_timezone="UTC")
    path = store.sessions_dir / f"{source.session_id}.jsonl"
    original_log = path.read_bytes()

    fork_id = fork_session(store, tmp_path, source.session_id, title="Edited", before_message_id=edited)

    assert fork_id != source.session_id
    forked = store.load(fork_id)
    assert forked.metadata["title"] == "Edited"
    assert [message.parts[0].content for message in forked.messages] == ["Keep this message"]
    assert path.read_bytes() == original_log


def test_fork_rejects_unsupported_schema_before_writing_a_new_log(tmp_path):
    store = SessionStore(tmp_path / ".raccoon")
    path = store.sessions_dir / "old.jsonl"
    event = {
        "id": "create",
        "session_id": "old",
        "type": "session_created",
        "created_at": "2026-11-19T00:00:00+00:00",
        "payload": {"context_event_schema_version": "future"},
    }
    source_log = (json.dumps(event) + "\n").encode("utf-8")
    path.write_bytes(source_log)

    with pytest.raises(SessionUnsupportedSchemaError, match="future"):
        fork_session(store, tmp_path, "old")

    assert list(store.sessions_dir.glob("*.jsonl")) == [path]
    assert path.read_bytes() == source_log
