"""权限相关 slash command。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from raccoon.app.session_runtime import SessionRuntime

from dataclasses import dataclass

from raccoon.application.conversations.commands import CommandResult


@dataclass(slots=True)
class PermissionCommandHandler:
    """处理 `/mode` 权限策略切换。"""

    runner: SessionRuntime

    def handle(self, text: str) -> CommandResult:
        command = " ".join(text.strip().split())
        if not command.startswith("/"):
            return CommandResult(handled=False)

        if command == "/mode":
            return CommandResult(
                handled=True,
                output=(f"Permission mode: {self.runner.session.mode}\n" "Available: ask, auto, bypass"),
            )

        if command.startswith("/mode "):
            raw_mode = command.split(" ", 1)[1].strip().lower()
            try:
                mode = self.runner.session.set_permission_mode(raw_mode)
            except ValueError:
                return CommandResult(
                    handled=True,
                    output="Unknown permission mode. Available: ask, auto, bypass",
                )
            return CommandResult(handled=True, output=f"Permission mode set to: {mode.value}")

        return CommandResult(handled=False)
