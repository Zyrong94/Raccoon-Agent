"""Forks and reopened sessions keep the correct project and file directories."""

from pathlib import Path

from raccoon.bootstrap.agent_session import new_session, open_session
from raccoon.modules.sessions.fork import fork_session
from raccoon.modules.sessions.store import SessionStore


def test_reopen_keeps_directories_and_fork_gets_its_own(tmp_path):
    store = SessionStore(tmp_path / ".raccoon")
    source = new_session(store=store, project_root=tmp_path, user_timezone="UTC")
    restored = open_session(store=store, project_root=tmp_path, session_id=source.session_id, user_timezone="UTC")
    assert restored.thread_data == source.thread_data

    fork_id = fork_session(store, tmp_path, source.session_id)
    forked = store.load(fork_id).metadata["thread_data"]
    assert forked["workspace_path"] == str(tmp_path.resolve())
    assert Path(forked["uploads_path"]) == store.root / "attachments" / fork_id
    assert Path(forked["outputs_path"]) == store.root / "outputs" / fork_id
    assert forked["uploads_path"] != source.thread_data["uploads_path"]
