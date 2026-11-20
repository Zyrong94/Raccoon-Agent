"""权限统一决策入口。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from raccoon.runtime.user_input import UserInputOption, UserInputRequest
from raccoon.modules.permissions.grants import PermissionGrantStore
from raccoon.modules.permissions.policy import DefaultPermissionPolicy
from raccoon.modules.permissions.types import (
    PermissionAction,
    PermissionConfirmationChoice,
    PermissionDecision,
    PermissionDecisionKind,
    PermissionGrant,
    PermissionMode,
    PermissionRequest,
    PermissionScopeType,
)


class PermissionManager:
    """组合长期授权和默认策略。

    预检返回 allow / ask / deny；Agent 层负责等待用户和执行工具。
    """

    def __init__(
        self,
        *,
        policy: DefaultPermissionPolicy,
        grants: PermissionGrantStore | None = None,
        mode: PermissionMode = PermissionMode.ASK,
        allow_user_approval: bool = True,
    ) -> None:
        self.policy = policy
        self.grants = grants or PermissionGrantStore()
        self._mode = PermissionMode(mode)
        self._turn_mode: ContextVar[PermissionMode | None] = ContextVar('permission_mode', default=None)
        self.allow_user_approval = allow_user_approval

    @property
    def mode(self) -> PermissionMode:
        return self._turn_mode.get() or self._mode

    @mode.setter
    def mode(self, mode: PermissionMode) -> None:
        self._mode = PermissionMode(mode)

    @contextmanager
    def use_mode(self, mode: PermissionMode):
        token = self._turn_mode.set(mode)
        try:
            yield
        finally:
            self._turn_mode.reset(token)

    def preflight(self, request: PermissionRequest) -> PermissionDecision:
        request = self.normalize_request(request)
        decision = self.grants.matching_decision(request)
        if decision is None:
            decision = self.policy.decide(request, mode=self.mode)
        if decision.kind == PermissionDecisionKind.ASK and not self.allow_user_approval:
            return self.reject_user_approval(decision.reason)
        return decision

    @staticmethod
    def reject_user_approval(reason: str = "") -> PermissionDecision:
        return PermissionDecision(kind=PermissionDecisionKind.DENY,
            reason="Subagent approval is disabled; ask the parent agent to handle this operation. " + reason)

    def build_confirmation(self, request: PermissionRequest) -> UserInputRequest:
        """把需要审批的操作转换为一个用户问题。"""
        request = self.normalize_request(request)
        scope_type, scope_value = _grant_scope(request, self.policy.project_root)
        allow_always = bool(request.metadata.get("allow_always", True))
        question = f"允许执行权限操作 `{request.action.value}` 吗？\n目标：{request.target}"
        if request.reason:
            question += f"\n原因：{request.reason}"
        options = [
            UserInputOption(id=PermissionConfirmationChoice.DENY.value, label="Deny"),
            UserInputOption(id=PermissionConfirmationChoice.ALLOW_ONCE.value, label="Allow once"),
        ]
        if allow_always:
            options.append(UserInputOption(
                id=PermissionConfirmationChoice.ALLOW_ALWAYS_SAME_SCOPE.value,
                label="Allow always",
                description=f"{scope_type.value}: {scope_value}",
            ))
        return UserInputRequest(
            id=request.id,
            kind="permission_confirmation",
            question=question,
            options=options,
            payload={
                "request_type": "permission_confirmation",
                "permission_request_id": request.id,
                "action": request.action.value,
                "target": request.target,
                "reason": request.reason,
                "scope_type": scope_type.value,
                "scope_value": scope_value,
                "allow_always": allow_always,
            },
        )

    def resolve_confirmation(self, request: PermissionRequest, choice: str) -> PermissionDecision:
        """解析回答、复核权限；长期允许才写入授权存储。"""
        request = self.normalize_request(request)
        normalized, feedback = _normalize_choice(choice)
        if normalized in (PermissionConfirmationChoice.DENY, PermissionConfirmationChoice.REJECT_WITH_FEEDBACK):
            reason = f"用户拒绝了权限请求：{feedback}" if feedback else "用户拒绝了权限请求。"
            return PermissionDecision(kind=PermissionDecisionKind.DENY, reason=reason, feedback=feedback)
        if normalized not in (PermissionConfirmationChoice.ALLOW_ONCE, PermissionConfirmationChoice.ALLOW_ALWAYS_SAME_SCOPE):
            return PermissionDecision(kind=PermissionDecisionKind.DENY, reason=f"未知权限选择：{choice}")

        allow_always = normalized == PermissionConfirmationChoice.ALLOW_ALWAYS_SAME_SCOPE
        if allow_always and not bool(request.metadata.get("allow_always", True)):
            return PermissionDecision(kind=PermissionDecisionKind.DENY, reason="该权限请求不支持长期授权。")
        if not self.allow_user_approval:
            return self.reject_user_approval()
        # 回答不能覆盖等待期间新增的拒绝，也不重复保存已有授权。
        decision = self.preflight(request)
        if decision.kind != PermissionDecisionKind.ASK:
            return decision
        if not allow_always:
            return PermissionDecision(kind=PermissionDecisionKind.ALLOW, reason="用户允许本次执行。")

        scope_type, scope_value = _grant_scope(request, self.policy.project_root)
        grant = PermissionGrant(
            id=f"grant_{request.id}", effect="allow", action=request.action,
            scope_type=scope_type, scope_value=scope_value,
            created_at=datetime.now(timezone.utc).isoformat(),
            reason="用户选择 allow always。",
        )
        self.grants.add(grant)
        return PermissionDecision(kind=PermissionDecisionKind.ALLOW, reason="用户允许同范围后续执行。", grant=grant)

    def normalize_request(self, request: PermissionRequest) -> PermissionRequest:
        """让 manager 入口中的相对路径解析和默认策略保持同一基准。"""

        if request.cwd is not None:
            if request.cwd.is_absolute():
                return request
            return replace(request, cwd=(self.policy.project_root / request.cwd).resolve())
        if request.action not in {
            PermissionAction.READ_PATH,
            PermissionAction.WRITE_PATH,
            PermissionAction.DELETE_PATH,
        }:
            return request
        return replace(request, cwd=self.policy.project_root)


def _grant_scope(request: PermissionRequest, project_root: Path) -> tuple[PermissionScopeType, str]:
    """从实际操作生成长期授权范围，避免放大到整个工具或解释器。"""
    if request.action in {PermissionAction.READ_PATH, PermissionAction.WRITE_PATH, PermissionAction.DELETE_PATH}:
        path = Path(request.target)
        if not path.is_absolute():
            path = (request.cwd or project_root) / path
        return PermissionScopeType.EXACT_PATH, str(path.resolve())
    if request.action == PermissionAction.NETWORK_REQUEST:
        parsed = urlparse(request.target)
        host = parsed.hostname or request.target.split("/", 1)[0].split(":", 1)[0]
        return PermissionScopeType.HOST, host.rstrip(".").lower()
    if request.action == PermissionAction.READ_ENV:
        return PermissionScopeType.ENV_KEY, request.target.upper()
    if request.action == PermissionAction.MCP_TOOL:
        return PermissionScopeType.MCP_TOOL, request.target
    if request.action == PermissionAction.GIT_OPERATION:
        parts = request.target.strip().split()
        prefix = parts[0] if parts else ""
        if len(parts) >= 2 and parts[0] == "git":
            prefix = " ".join(parts[:2])
        return PermissionScopeType.COMMAND_PREFIX, prefix
    # Shell 使用完整命令作为前缀，不能把 python script.py 扩大为 python。
    return PermissionScopeType.COMMAND_PREFIX, request.target.strip()


def _normalize_choice(choice: str) -> tuple[PermissionConfirmationChoice | None, str]:
    normalized = choice.strip().lower()
    for prefix in ("reject_with_feedback:", "reject:"):
        if normalized.startswith(prefix):
            feedback = choice.strip()[len(prefix) :].strip()
            return PermissionConfirmationChoice.REJECT_WITH_FEEDBACK, feedback
    numeric = {
        "1": PermissionConfirmationChoice.DENY,
        "2": PermissionConfirmationChoice.ALLOW_ONCE,
        "3": PermissionConfirmationChoice.ALLOW_ALWAYS_SAME_SCOPE,
        "4": PermissionConfirmationChoice.REJECT_WITH_FEEDBACK,
    }
    if normalized in numeric:
        return numeric[normalized], ""
    for item in PermissionConfirmationChoice:
        if normalized == item.value:
            return item, ""
    return None, ""
