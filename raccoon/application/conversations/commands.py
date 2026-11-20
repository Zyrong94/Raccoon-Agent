"""Result returned by the QQ permission command adapter."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CommandResult:
    handled: bool
    output: str = ""
    action: dict[str, Any] | None = None
