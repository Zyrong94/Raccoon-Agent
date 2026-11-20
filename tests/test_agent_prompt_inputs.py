from raccoon.bootstrap.project_instructions import read_agents_md


def test_read_agents_md_reads_project_root_file(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("项目规则", encoding="utf-8")

    assert read_agents_md(tmp_path) == "项目规则"


def test_read_agents_md_returns_empty_when_missing(tmp_path) -> None:
    assert read_agents_md(tmp_path) == ""


def test_sessions_report_their_workspace_environment(tmp_path, monkeypatch):
    import json
    from raccoon.bootstrap.agent_session import new_session
    from raccoon.modules.sessions.store import SessionStore
    from raccoon.modules.tools.builtin import create_builtin_registry
    from raccoon.runtime.subprocess import execution_environment_snapshot
    monkeypatch.setenv("RACCOON_DISABLE_GLOBAL_SKILLS", "1")
    store = SessionStore(tmp_path / "store")
    parent_root = tmp_path / "project"
    child_root = tmp_path / "worktree"
    parent_root.mkdir()
    child_root.mkdir()
    for session_id, root in [("parent", parent_root), ("child", parent_root), ("worktree", child_root)]:
        session = new_session(
            store=store, session_id=session_id, project_root=root,
            tools=create_builtin_registry(root, include_execution_tools=True).tools(),
        )
        messages = session.build_system_prefix()
        assert len(messages) == 1
        assert json.dumps(str(root.resolve())) in messages[0].content
        environment = execution_environment_snapshot(root)
        assert f'"shell_kind": {json.dumps(environment["shell_kind"])}' in messages[0].content
        assert session.build_system_prefix() == messages
