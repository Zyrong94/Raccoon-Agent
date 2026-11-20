"""默认权限策略：没有命中显式授权时，根据模式、动作和目标决定。

ask 允许项目内普通读取和只读 Git，其余按具体操作询问或拒绝。
auto 使用同一基础策略，ASK 由 Agent 层交给独立模型审核；bypass 暂时沿用已有行为。
"""

from __future__ import annotations

import re
import ipaddress
from pathlib import Path
from urllib.parse import urlparse

from raccoon.modules.permissions.types import (
    PermissionAction,
    PermissionDecision,
    PermissionDecisionKind,
    PermissionMode,
    PermissionRequest,
)

_SENSITIVE_ENV_KEYWORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE")
_SENSITIVE_FILENAMES = {".env"}
_SENSITIVE_SUFFIXES = {".pem", ".key"}
_READONLY_GIT_COMMANDS = {"status", "diff", "log"}
_SHELL_CONTROL_PATTERN = re.compile(r"(&&|\|\||\$\(|[;&|<>`\r\n])")


class DefaultPermissionPolicy:
    """基于项目根目录的默认权限策略。"""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    def decide(self, request: PermissionRequest, *, mode: PermissionMode) -> PermissionDecision:
        if mode == PermissionMode.BYPASS:
            return PermissionDecision(kind=PermissionDecisionKind.ALLOW, reason="bypass 模式允许权限请求。")
        if request.action in {
            PermissionAction.READ_PATH,
            PermissionAction.WRITE_PATH,
            PermissionAction.DELETE_PATH,
        }:
            return self._decide_path(request)
        if request.action == PermissionAction.READ_ENV:
            return self._decide_env(request)
        if request.action == PermissionAction.GIT_OPERATION:
            return self._decide_git(request)
        if request.action == PermissionAction.EXECUTE_SHELL:
            return self._decide_shell(request)
        if request.action == PermissionAction.NETWORK_REQUEST:
            if _is_private_network_target(request.target):
                return PermissionDecision(kind=PermissionDecisionKind.ASK, reason="访问本机、内网或链路本地地址需要用户确认。")
            return PermissionDecision(kind=PermissionDecisionKind.ASK, reason="网络请求需要用户确认。")
        if request.action == PermissionAction.MCP_TOOL:
            return PermissionDecision(kind=PermissionDecisionKind.ASK, reason="MCP 工具调用需要用户确认。")
        return PermissionDecision(kind=PermissionDecisionKind.ASK, reason="未知权限请求需要用户确认。")

    def _decide_path(self, request: PermissionRequest) -> PermissionDecision:
        targets = self._resolve_paths(request.target, cwd=request.cwd)
        inside_root = all(self._is_inside_project(target) for target in targets)
        sensitive = any(self._is_sensitive_path(target) for target in targets)

        if request.action == PermissionAction.READ_PATH:
            if inside_root and not sensitive:
                return PermissionDecision(kind=PermissionDecisionKind.ALLOW, reason="允许读取项目根目录内普通路径。")
            return PermissionDecision(kind=PermissionDecisionKind.ASK, reason="读取项目根目录外或敏感路径需要用户确认。")

        if request.action == PermissionAction.WRITE_PATH:
            if not inside_root:
                return PermissionDecision(kind=PermissionDecisionKind.ASK, reason="写入项目根目录外路径需要用户确认。")
            if sensitive:
                return PermissionDecision(kind=PermissionDecisionKind.ASK, reason="写入敏感路径需要用户确认。")
            return PermissionDecision(kind=PermissionDecisionKind.ASK, reason="写入文件需要用户确认。")

        if request.action == PermissionAction.DELETE_PATH:
            if not inside_root:
                return PermissionDecision(kind=PermissionDecisionKind.DENY, reason="拒绝删除项目根目录外路径。")
            return PermissionDecision(kind=PermissionDecisionKind.ASK, reason="删除路径需要用户确认。")

        return PermissionDecision(kind=PermissionDecisionKind.ASK, reason="路径操作需要用户确认。")

    def _decide_env(self, request: PermissionRequest) -> PermissionDecision:
        key = request.target.upper()
        if any(keyword in key for keyword in _SENSITIVE_ENV_KEYWORDS):
            return PermissionDecision(kind=PermissionDecisionKind.DENY, reason="拒绝读取或展示敏感环境变量明文。")
        return PermissionDecision(kind=PermissionDecisionKind.ASK, reason="读取环境变量需要用户确认。")

    def _decide_git(self, request: PermissionRequest) -> PermissionDecision:
        command = request.target.strip()
        if _has_shell_control_operator(command):
            return PermissionDecision(kind=PermissionDecisionKind.ASK, reason="包含 shell 控制符的 git 操作需要用户确认。")
        subcommand = command.split(maxsplit=1)[0] if command else ""
        if subcommand in _READONLY_GIT_COMMANDS and self._request_cwd_inside_root(request):
            return PermissionDecision(kind=PermissionDecisionKind.ALLOW, reason="允许项目根目录内只读 git 操作。")
        return PermissionDecision(kind=PermissionDecisionKind.ASK, reason="git 操作需要用户确认。")

    def _decide_shell(self, request: PermissionRequest) -> PermissionDecision:
        command = request.target.strip()
        if _has_shell_control_operator(command):
            return PermissionDecision(kind=PermissionDecisionKind.ASK, reason="包含 shell 控制符的命令需要用户确认。")
        return PermissionDecision(kind=PermissionDecisionKind.ASK, reason="shell 命令需要用户确认。")

    def _resolve_path(self, value: str, *, cwd: Path | None) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = (cwd or self.project_root) / path
        return path.resolve()

    def _resolve_paths(self, value: str, *, cwd: Path | None) -> list[Path]:
        values = [part.strip() for part in value.splitlines() if part.strip()]
        if not values:
            values = [part.strip() for part in value.split(", ") if part.strip()]
        return [self._resolve_path(part, cwd=cwd) for part in values or [value]]

    def _is_inside_project(self, path: Path) -> bool:
        return path == self.project_root or self.project_root in path.parents

    def _request_cwd_inside_root(self, request: PermissionRequest) -> bool:
        cwd = (request.cwd or self.project_root).resolve()
        return cwd == self.project_root or self.project_root in cwd.parents

    def _is_sensitive_path(self, path: Path) -> bool:
        relative_parts = []
        try:
            relative_parts = list(path.relative_to(self.project_root).parts)
        except ValueError:
            relative_parts = list(path.parts)

        if ".git" in relative_parts:
            return True
        if path.name in _SENSITIVE_FILENAMES:
            return True
        return path.suffix.lower() in _SENSITIVE_SUFFIXES


def _has_shell_control_operator(command: str) -> bool:
    return bool(_SHELL_CONTROL_PATTERN.search(command))


def _is_private_network_target(target: str) -> bool:
    parsed = urlparse(target)
    hostname = parsed.hostname or target.split("/", 1)[0].split(":", 1)[0]
    hostname = hostname.rstrip(".").lower()
    if hostname in {"localhost", "ip6-localhost"} or hostname.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved
