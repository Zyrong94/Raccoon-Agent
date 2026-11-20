"""Frozen parent permissions evaluated in a child's execution directory."""

from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path

from raccoon.modules.permissions.grants import PermissionGrantStore
from raccoon.modules.permissions.manager import PermissionManager
from raccoon.modules.permissions.policy import DefaultPermissionPolicy
from raccoon.modules.permissions.types import (
    PermissionAction, PermissionDecisionKind, PermissionGrant, PermissionMode,
    PermissionScopeType,
)


class DelegatedPermissionManager(PermissionManager):
    def __init__(self, parent: PermissionManager, root: Path):
        self.boundary = PermissionManager(policy=deepcopy(parent.policy),
            grants=PermissionGrantStore(deepcopy(parent.grants.list())), mode=parent.mode)
        super().__init__(policy=DefaultPermissionPolicy(root), mode=parent.mode, allow_user_approval=False)

    def preflight(self, request):
        request = self.normalize_request(request)
        root = self.policy.project_root
        source = self.boundary.policy.project_root

        def parent_path(value):
            path = Path(value).resolve()
            try:
                return source / path.relative_to(root)
            except ValueError:
                return path

        cwd = request.cwd or root
        if request.action in {PermissionAction.READ_PATH, PermissionAction.WRITE_PATH, PermissionAction.DELETE_PATH}:
            # Use the policy's existing multi-target parsing before mapping each project path.
            targets = self.policy._resolve_paths(request.target, cwd=cwd)
            if root != source and any(not path.is_relative_to(root) for path in targets):
                return self.reject_user_approval("The path is outside this child's worktree.")
            request = replace(request, target="\n".join(str(parent_path(path)) for path in targets))
        if request.cwd is not None:
            request = replace(request, cwd=parent_path(cwd))
        decision = self.boundary.preflight(request)
        if decision.kind == PermissionDecisionKind.ASK:
            return self.reject_user_approval(decision.reason)
        return decision

    def snapshot(self):
        return {"root": str(self.boundary.policy.project_root), "mode": self.boundary.mode.value,
                "default_policy": type(self.boundary.policy) is DefaultPermissionPolicy,
                "grants": [asdict(grant) for grant in self.boundary.grants.list()]}

    @classmethod
    def restore(cls, snapshot, root):
        # Custom policy code cannot be reconstructed safely from a default-policy snapshot.
        if not isinstance(snapshot, dict) or snapshot.get("default_policy") is not True:
            raise ValueError("saved child permission boundary is unavailable; refusing execution")
        grants = [PermissionGrant(**{**grant, "action": PermissionAction(grant["action"]),
                    "scope_type": PermissionScopeType(grant["scope_type"])}) for grant in snapshot["grants"]]
        parent = PermissionManager(policy=DefaultPermissionPolicy(snapshot["root"]),
            grants=PermissionGrantStore(grants), mode=PermissionMode(snapshot["mode"]))
        return cls(parent, Path(root))
