from types import SimpleNamespace

import pytest

import raccoon.modules.permissions.grants as grants_module
from raccoon.modules.permissions.grants import PermissionGrantStore
from raccoon.modules.permissions.manager import PermissionManager
from raccoon.modules.permissions.policy import DefaultPermissionPolicy
from raccoon.modules.permissions.types import PermissionAction, PermissionDecisionKind
from raccoon.modules.tools.permissions import permission_request_for_tool
from raccoon.modules.tools.shell import create_shell_tool
from tests.test_permissions_grants import _grant


PREFIX = '"C:/Program Files/nodejs/node.exe" "C:/tools/opencli/dist/main.js" hackernews top --'


def shell_request(tmp_path, command=PREFIX + ' 10', **arguments):
    return permission_request_for_tool(create_shell_tool(tmp_path), {'command': command, **arguments})


@pytest.mark.parametrize('env', [None, {}])
def test_plain_cli_prefix_still_matches_without_environment_override(tmp_path, env):
    request = shell_request(tmp_path, env=env)
    decision = PermissionGrantStore([_grant('read', scope_value=PREFIX)]).matching_decision(request)
    assert decision.kind == PermissionDecisionKind.ALLOW


@pytest.mark.parametrize('env', [{'NODE_OPTIONS': '--require C:/private/inject.js'},
                                {'PAYLOAD': 'echo injected'}, {'LANG': 'en_US.UTF-8'}])
def test_environment_override_cannot_use_automatic_shell_allow(tmp_path, env):
    request = shell_request(tmp_path, env=env)
    assert request.metadata['arguments']['env'] == env
    store = PermissionGrantStore([_grant('read', scope_value=PREFIX)])
    assert store.matching_decision(request) is None
    manager = PermissionManager(policy=DefaultPermissionPolicy(tmp_path), grants=store)
    assert manager.preflight(request).kind == PermissionDecisionKind.ASK


@pytest.mark.parametrize('suffix', ['%PAYLOAD%', '!PAYLOAD!', '^malformed'])
@pytest.mark.parametrize('platform', ['nt', 'posix'])
def test_windows_expansion_does_not_match_allow_but_remains_denied(tmp_path, monkeypatch, suffix, platform):
    request = shell_request(tmp_path, command=PREFIX + ' ' + suffix)
    # Replace only this module's OS reference; do not change pathlib's host platform.
    monkeypatch.setattr(grants_module, 'os', SimpleNamespace(name=platform))
    allow = _grant('read', scope_value=PREFIX)
    decision = PermissionGrantStore([allow]).matching_decision(request)
    if platform == 'nt':
        assert decision is None
    else:
        assert decision.kind == PermissionDecisionKind.ALLOW
    deny = _grant('blocked', effect='deny', scope_value=PREFIX)
    assert PermissionGrantStore([allow, deny]).matching_decision(request).kind == PermissionDecisionKind.DENY


def test_environment_override_cannot_bypass_deny(tmp_path):
    request = shell_request(tmp_path, env={'NODE_OPTIONS': '--require C:/private/inject.js'})
    store = PermissionGrantStore([_grant('read', scope_value=PREFIX),
                                 _grant('blocked', effect='deny', scope_value=PREFIX)])
    assert store.matching_decision(request).kind == PermissionDecisionKind.DENY


@pytest.mark.parametrize('suffix', ['&& echo injected', '; echo injected', '\necho injected', '$(echo injected)'])
def test_existing_shell_control_rejection_is_preserved(tmp_path, suffix):
    request = shell_request(tmp_path, command=PREFIX + ' ' + suffix)
    assert PermissionGrantStore([_grant('read', scope_value=PREFIX)]).matching_decision(request) is None


def test_non_shell_command_prefix_scope_is_unchanged(tmp_path):
    request = shell_request(tmp_path, command='status', env={'LANG': 'en_US.UTF-8'})
    request.action = PermissionAction.GIT_OPERATION
    grant = _grant('git', action=PermissionAction.GIT_OPERATION, scope_value='status')
    assert PermissionGrantStore([grant]).matching_decision(request).kind == PermissionDecisionKind.ALLOW
