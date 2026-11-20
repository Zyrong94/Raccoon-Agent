from raccoon.modules.permissions.delegation import DelegatedPermissionManager
from raccoon.modules.permissions.grants import PermissionGrantStore
from raccoon.modules.permissions.manager import PermissionManager
from raccoon.modules.permissions.policy import DefaultPermissionPolicy
from raccoon.modules.permissions.types import (
    PermissionAction, PermissionDecisionKind, PermissionGrant, PermissionMode,
    PermissionRequest, PermissionScopeType,
)


def grant(path, *, action=PermissionAction.WRITE_PATH, effect='allow'):
    return PermissionGrant('saved', effect, action, PermissionScopeType.EXACT_PATH, str(path), 'test')


def test_worktree_maps_frozen_parent_grants_and_refuses_approval(tmp_path):
    source, worktree = tmp_path / 'source', tmp_path / 'worktree'
    source.mkdir()
    worktree.mkdir()
    parent = PermissionManager(policy=DefaultPermissionPolicy(source), grants=PermissionGrantStore([
        grant(source / 'allowed.txt'), grant(source / 'private.txt', action=PermissionAction.READ_PATH, effect='deny'),
    ]))
    child = DelegatedPermissionManager(parent, worktree)
    parent.mode = PermissionMode.BYPASS
    parent.grants.add(grant(source / 'new.txt'))
    restored = DelegatedPermissionManager.restore(child.snapshot(), worktree)
    for manager in (child, restored):
        def decision(action, path):
            return manager.preflight(PermissionRequest('r', action, str(worktree / path))).kind
        assert decision(PermissionAction.WRITE_PATH, 'allowed.txt') == PermissionDecisionKind.ALLOW
        assert decision(PermissionAction.WRITE_PATH, 'new.txt') == PermissionDecisionKind.DENY
        assert decision(PermissionAction.READ_PATH, 'private.txt') == PermissionDecisionKind.DENY
        assert manager.preflight(PermissionRequest('network', PermissionAction.NETWORK_REQUEST, 'https://example.com')).kind == PermissionDecisionKind.DENY
        assert manager.preflight(PermissionRequest('outside', PermissionAction.WRITE_PATH, str(source / 'allowed.txt'))).kind == PermissionDecisionKind.DENY
        assert manager.resolve_confirmation(PermissionRequest('n', PermissionAction.NETWORK_REQUEST, 'https://example.com'), 'allow_once').kind == PermissionDecisionKind.DENY
