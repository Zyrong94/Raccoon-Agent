"""Agent 用户回合遥测。

遥测只保存控制循环的结构化计数，不保存提示词、工具参数、工具输出或密钥。事件通过
append-only session JSONL 持久化，但不会被 session view 投影成 provider 消息。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from raccoon.modules.sessions.identity import stable_json_hash
from raccoon.runtime.model_types import ToolCall
from raccoon.modules.tools.types import ToolResult


TELEMETRY_SCHEMA_VERSION = 1


@dataclass(slots=True)
class AgentTurnTelemetry:
    """累计一个用户回合的可观察执行指标。"""

    active: bool = False
    snapshot_index: int = 0
    provider_calls: int = 0
    provider_failure_category: str | None = None
    tool_calls: int = 0
    tool_failures: int = 0
    tool_call_counts: dict[str, int] = field(default_factory=dict)
    _tool_call_fingerprints: dict[str, int] = field(default_factory=dict, repr=False)
    repeated_tool_calls: int = 0
    max_identical_tool_calls: int = 0
    first_mutation_sequence: int | None = None
    first_mutation_elapsed_seconds: float | None = None
    validation_count: int = 0
    latest_validation_ok: bool | None = None
    latest_validation_tool: str | None = None

    def begin(self) -> None:
        self.active = True
        self.snapshot_index = 0
        self.provider_calls = 0
        self.provider_failure_category = None
        self.tool_calls = 0
        self.tool_failures = 0
        self.tool_call_counts.clear()
        self._tool_call_fingerprints.clear()
        self.repeated_tool_calls = 0
        self.max_identical_tool_calls = 0
        self.first_mutation_sequence = None
        self.first_mutation_elapsed_seconds = None
        self.validation_count = 0
        self.latest_validation_ok = None
        self.latest_validation_tool = None

    def observe_provider_call(self) -> None:
        if self.active:
            self.provider_calls += 1


    def observe_tool_result(self, tool_call: ToolCall, result: ToolResult, *, elapsed_seconds: float) -> None:
        if not self.active:
            return
        self.tool_calls += 1
        self.tool_call_counts[tool_call.name] = self.tool_call_counts.get(tool_call.name, 0) + 1
        if not result.ok:
            self.tool_failures += 1

        fingerprint = stable_json_hash(
            {"name": tool_call.name, "arguments": tool_call.arguments},
            length=24,
        )
        identical_count = self._tool_call_fingerprints.get(fingerprint, 0) + 1
        self._tool_call_fingerprints[fingerprint] = identical_count
        self.max_identical_tool_calls = max(self.max_identical_tool_calls, identical_count)
        if identical_count > 1:
            self.repeated_tool_calls += 1

        if self.first_mutation_sequence is None and is_mutation_result(tool_call, result):
            self.first_mutation_sequence = self.tool_calls
            self.first_mutation_elapsed_seconds = max(0.0, elapsed_seconds)
        if is_validation_call(tool_call):
            self.validation_count += 1
            self.latest_validation_ok = result.ok
            self.latest_validation_tool = tool_call.name


    def snapshot(
        self,
        *,
        status: str,
        stop_reason: str,
        elapsed_seconds: float,
        provider_failure_category: str | None = None,
        finalize: bool,
    ) -> dict[str, Any] | None:
        if not self.active:
            return None
        self.snapshot_index += 1
        if provider_failure_category is not None:
            self.provider_failure_category = provider_failure_category
        payload: dict[str, Any] = {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "snapshot_index": self.snapshot_index,
            "status": status,
            "stop_reason": stop_reason,
            "elapsed_seconds": round(max(0.0, elapsed_seconds), 6),
            "provider_calls": self.provider_calls,
            "provider_failure_category": self.provider_failure_category,
            "tool_calls": self.tool_calls,
            "tool_failures": self.tool_failures,
            "tool_call_counts": dict(sorted(self.tool_call_counts.items())),
            "repeated_tool_calls": self.repeated_tool_calls,
            "max_identical_tool_calls": self.max_identical_tool_calls,
            "first_mutation_sequence": self.first_mutation_sequence,
            "first_mutation_elapsed_seconds": (
                round(self.first_mutation_elapsed_seconds, 6)
                if self.first_mutation_elapsed_seconds is not None
                else None
            ),
            "validation_count": self.validation_count,
            "latest_validation_ok": self.latest_validation_ok,
            "latest_validation_tool": self.latest_validation_tool,
        }
        if finalize:
            self.active = False
        return payload


MUTATION_TOOL_NAMES = frozenset({"write", "edit", "apply_patch", "delete"})


VALIDATION_TOOL_NAMES = frozenset({"diagnostics", "review"})


PROCESS_MUTATION_TOOL_NAMES = frozenset({"process_start", "process_stop"})


_VALIDATION_COMMAND_RE = re.compile(
    r"(?:^|[;&|\s])(?:"
    r"pytest|python\s+-m\s+(?:pytest|unittest)|unittest|"
    r"cargo\s+(?:test|check|build)|go\s+(?:test|vet|build)|"
    r"npm\s+(?:test|run\s+(?:test|lint|check|build))|"
    r"pnpm\s+(?:test|lint|check|build)|yarn\s+(?:test|lint|check|build)|"
    r"ruff(?:\s+check)?|mypy|pyright|tsc|eslint|"
    r"make\s+(?:test|check|lint)|cmake\s+--build|"
    r"curl|wget"
    r")(?:\s|$)",
    re.IGNORECASE,
)


_ASSERTIVE_VALIDATION_RE = re.compile(
    r"(?:"
    r"(?:^|[;&|\s])(?:test|\[|grep\s+-q|cmp|diff)(?:\s|$)|"
    r"\bassert\b|\braise\s+SystemExit\b|"
    r"\bcurl\b[^\n;&|]*(?:--fail(?:-with-body)?|-f)(?:\s|$)|"
    r"\bwget\b[^\n;&|]*(?:--spider|--server-response)(?:\s|$)|"
    r"\bexit\s+[1-9]\d*\b"
    r")",
    re.IGNORECASE,
)


_MUTATION_COMMAND_RE = re.compile(
    r"(?:"
    r"(?:^|[;&|\s])(?:rm|del|erase|rmdir|mkdir|md|touch|mv|move|cp|copy|"
    r"chmod|chown|install|tee|set-content|add-content|new-item|remove-item|"
    r"move-item|copy-item|rename-item|systemctl\s+(?:start|restart|enable)|"
    r"service\s+\S+\s+(?:start|restart)|apt(?:-get)?\s+install|yum\s+install|"
    r"dnf\s+install|apk\s+add|pip\s+install|uv\s+pip\s+install|"
    r"npm\s+install|pnpm\s+install|yarn\s+add|git\s+(?:apply|checkout|restore)"
    r")(?:\s|$)"
    r"|(?:^|[^<])>>?\s*[^&|]"
    r"|\bsed\s+-i\b"
    r")",
    re.IGNORECASE,
)


def is_validation_call(tool_call: ToolCall) -> bool:
    if tool_call.name in VALIDATION_TOOL_NAMES:
        return True
    if tool_call.name not in {"shell", "python_exec"}:
        return False
    command = _command_text(tool_call)
    return bool(
        command
        and (
            _VALIDATION_COMMAND_RE.search(command)
            or _ASSERTIVE_VALIDATION_RE.search(command)
        )
    )


def is_mutation_result(tool_call: ToolCall, result: ToolResult) -> bool:
    if not result.ok:
        return False
    if tool_call.name in MUTATION_TOOL_NAMES:
        return True
    if tool_call.name in PROCESS_MUTATION_TOOL_NAMES:
        return True
    if tool_call.name == "git_diff":
        return _has_real_diff(result)
    if tool_call.name not in {"shell", "python_exec"}:
        return False
    command = _command_text(tool_call)
    return bool(command and _MUTATION_COMMAND_RE.search(command))


def _has_real_diff(result: ToolResult) -> bool:
    content = result.content.strip()
    return bool(content and content != "没有 diff。")


def _command_text(tool_call: ToolCall) -> str:
    if not isinstance(tool_call.arguments, dict):
        return ""
    if tool_call.name == "python_exec":
        return str(tool_call.arguments.get("code") or "")
    return str(tool_call.arguments.get("command") or "")
