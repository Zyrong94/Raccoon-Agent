import pytest

from raccoon.config.subagents_config import SubagentConfigError, load_subagent_config, discover_subagent_configs


def write_subagent(root, name, settings='', soul='SPECIALIST SOUL'):
    directory = root / 'subagents' / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'config.yaml').write_text(f'name: {name}\n{settings}', encoding='utf-8')
    (directory / 'SOUL.md').write_text(soul, encoding='utf-8')
    return directory


def test_namespaces_and_file_overrides_are_independent(tmp_path):
    directory = write_subagent(tmp_path, 'reviewer', 'tools: []\nskills: []\nmax_tool_rounds: 2\n')
    agent_dir = tmp_path / 'agents' / 'reviewer'
    agent_dir.mkdir(parents=True)
    (agent_dir / 'config.yaml').write_text('name: reviewer\ntools: [write]\n', encoding='utf-8')
    sub = load_subagent_config('reviewer', data_root=tmp_path)
    assert sub.tools == () and sub.skills == ()
    assert sub.max_tool_rounds == 2
    assert sub.system_prompt == 'SPECIALIST SOUL'
    (directory / 'SOUL.md').unlink()
    with pytest.raises(SubagentConfigError):
        load_subagent_config('reviewer', data_root=tmp_path)
    assert 'reviewer' not in discover_subagent_configs(data_root=tmp_path)


def test_bundled_roles_load_from_files_without_project_configuration(tmp_path):
    configs = discover_subagent_configs(data_root=tmp_path)
    assert set(configs) == {'researcher', 'reviewer', 'tester', 'coder'}
    assert all(config.system_prompt for config in configs.values())
    assert configs['coder'].allow_background and configs['coder'].requires_worktree
    assert 'write' not in configs['reviewer'].tools
    assert configs['reviewer'].max_tool_rounds < configs['tester'].max_tool_rounds


@pytest.mark.parametrize('settings', [
    '- item', 'description: []', 'model: 123', 'tools: view', 'skills: [3]', 'max_tool_rounds: true', 'max_tool_rounds: 0', 'max_provider_calls: -1', 'max_turn_seconds: .nan', 'max_turn_seconds: .inf',
    'allow_background: yesplease', 'requires_worktree: 1', 'allowed_subagents: []',
    'mode: subagent', 'name: another', 'tools: [delegate]', 'model: other/model', '123: value',
])
def test_invalid_subagent_configuration_fails_closed(tmp_path, settings):
    write_subagent(tmp_path, 'reader', settings)
    with pytest.raises(SubagentConfigError):
        load_subagent_config('reader', data_root=tmp_path)
    assert 'reader' not in discover_subagent_configs(data_root=tmp_path)


@pytest.mark.parametrize('name', ['../reader', '/reader', '', 'reader/path'])
def test_invalid_names_cannot_escape_subagent_directory(tmp_path, name):
    with pytest.raises(SubagentConfigError):
        load_subagent_config(name, data_root=tmp_path)



def test_restored_subagent_uses_the_saved_role_and_tool_boundary(tmp_path):
    from raccoon.bootstrap.agent_session import new_session
    from raccoon.modules.collaboration.sessions import SubagentRequest, create_subagent_session, load_subagent_session
    from raccoon.modules.sessions.store import SessionStore
    from raccoon.modules.tools.view import create_view_tool
    from raccoon.modules.tools.write import create_write_tool

    project = tmp_path / "project"
    project.mkdir()
    store = SessionStore(tmp_path / "history")
    write_subagent(store.root, "reader", "tools: [view]\nskills: []\nmax_tool_rounds: 3\n")
    parent = new_session(store=store, project_root=project,
                         tools=[create_view_tool(project), create_write_tool(project)])
    child = create_subagent_session(parent, SubagentRequest("reader", "Read this"))
    parent.subagent_configs = {}
    restored = load_subagent_session(parent, child.session_id)

    assert restored.subagent_config.system_prompt == "SPECIALIST SOUL"
    assert restored.subagent_config.max_tool_rounds == 3
    assert restored.allowed_tool_names == {"view"}
    assert restored.skill_catalog.skills == []
    assert not restored.permission_manager.allow_user_approval
