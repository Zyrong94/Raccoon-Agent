from pathlib import Path

import pytest

from raccoon.bootstrap.project_instructions import read_default_soul


def test_read_default_soul_ignores_missing_or_blank_file(tmp_path: Path) -> None:
    assert read_default_soul(tmp_path) is None
    (tmp_path / "SOUL.md").write_text("  \n", encoding="utf-8")
    assert read_default_soul(tmp_path) is None


def test_read_default_soul_uses_only_data_root_file(tmp_path: Path) -> None:
    role = tmp_path / "agents" / "reader"
    role.mkdir(parents=True)
    (role / "SOUL.md").write_text("Old named instructions", encoding="utf-8")
    assert read_default_soul(tmp_path) is None
    (tmp_path / "SOUL.md").write_text("\n Shared instructions. \n", encoding="utf-8")
    assert read_default_soul(tmp_path) == "Shared instructions."


def test_read_default_soul_reports_unreadable_file(tmp_path: Path) -> None:
    (tmp_path / "SOUL.md").write_bytes(b"\xff")
    with pytest.raises(ValueError, match="cannot read default SOUL"):
        read_default_soul(tmp_path)


def test_reopened_session_uses_soul_from_current_data_root(tmp_path: Path) -> None:
    from raccoon.bootstrap.agent_session import open_session
    from raccoon.modules.sessions.store import SessionStore

    store = SessionStore(tmp_path / "history")
    store.create(session_id="chat")
    (store.root / "SOUL.md").write_text("Wrong data root", encoding="utf-8")
    data_root = tmp_path / "current"
    data_root.mkdir()
    (data_root / "SOUL.md").write_text("Current instructions", encoding="utf-8")
    session = open_session(store=store, session_id="chat", data_root=data_root, user_timezone="UTC")
    assert "Current instructions" in session.build_system_prefix()[0].content
    assert "Wrong data root" not in session.build_system_prefix()[0].content
