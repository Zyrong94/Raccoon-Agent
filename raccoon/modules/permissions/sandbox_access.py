"""Shared sandbox access state."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum


class SandboxAccessMode(StrEnum):
    PROJECT = "project"
    UNRESTRICTED = "unrestricted"


class SandboxAccess:
    def __init__(self, mode: SandboxAccessMode = SandboxAccessMode.PROJECT):
        self._mode = mode
        self._turn_mode: ContextVar[SandboxAccessMode | None] = ContextVar('sandbox_mode', default=None)

    @property
    def mode(self) -> SandboxAccessMode:
        return self._turn_mode.get() or self._mode

    @mode.setter
    def mode(self, mode: SandboxAccessMode) -> None:
        self._mode = mode

    @contextmanager
    def use_mode(self, mode: SandboxAccessMode):
        token = self._turn_mode.set(mode)
        try:
            yield
        finally:
            self._turn_mode.reset(token)

    @property
    def unrestricted(self) -> bool:
        return self.mode == SandboxAccessMode.UNRESTRICTED
