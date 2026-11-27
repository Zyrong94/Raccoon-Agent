"""稳定系统前缀构造与缓存。

系统提示词属于请求配置，不属于普通会话事实。这里把会影响系统前缀的稳定输入集中
计算 fingerprint，后续 agent loop 可以据此复用上一轮前缀，避免普通消息追加导致
系统提示词缓存失效。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from raccoon.modules.sessions.identity import content_fingerprint, stable_json_hash
from raccoon.modules.context.versions import SYSTEM_PROMPT_VERSION
from raccoon.runtime.model_types import ChatMessage


@dataclass(frozen=True, slots=True)
class SystemPromptInputs:
    """生成 stable system prefix 所需的稳定输入。

    这里刻意不包含最近消息、token 统计、checkpoint、task hash 候选等动态状态。
    这些内容属于 conversation projection 或 runtime state，不应该污染系统前缀缓存。
    """

    base_rules: str
    agents_md: str
    skill_protocol: str = ""
    skill_catalog_summary: str = ""
    prompt_version: str = SYSTEM_PROMPT_VERSION
    soul: str = ""
    execution_environment: dict[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PromptPrefixCacheEntry:
    fingerprint: str
    messages: list[ChatMessage]


class SystemPromptBuilder:
    """构造可复用的 system prompt 前缀。"""

    def fingerprint(self, inputs: SystemPromptInputs) -> str:
        value = {
            "prompt_version": inputs.prompt_version,
            "base_rules_hash": content_fingerprint(inputs.base_rules),
            "soul_hash": content_fingerprint(inputs.soul),
            "agents_md_hash": content_fingerprint(inputs.agents_md),
            "skill_protocol_hash": content_fingerprint(inputs.skill_protocol),
            "skill_catalog_summary_hash": content_fingerprint(inputs.skill_catalog_summary),
            "execution_environment": inputs.execution_environment,
        }
        return stable_json_hash(value)

    def build(self, inputs: SystemPromptInputs) -> PromptPrefixCacheEntry:
        fingerprint = self.fingerprint(inputs)
        content = "\n\n".join(
            section
            for section in [
                inputs.base_rules.strip(),
                _format_soul(inputs.soul),
                _agent_instructions(),
                _format_section("Project instructions", inputs.agents_md),
                _format_section("Project skill protocol", inputs.skill_protocol),
                _format_section("Available skills", inputs.skill_catalog_summary),
                _format_section("Execution environment", (
                    "Path and environment data, not instructions: " + _format_json(inputs.execution_environment)
                    + "\nworking_directory is the default tool directory; uploads_path contains uploaded files; "
                    "outputs_path is the designated output directory. Directories may be created on first write. "
                    "Paths do not attach file contents to the request."
                )) if inputs.execution_environment else "",
            ]
            if section
        )
        message = ChatMessage(
            role="system",
            text=content,
        )
        return PromptPrefixCacheEntry(
            fingerprint=fingerprint,
            messages=[message],
        )


class PromptPrefixCache:
    """第一版只缓存当前会话最近一次 stable prefix。"""

    def __init__(self) -> None:
        self._entry: PromptPrefixCacheEntry | None = None

    def get_or_build(
        self,
        inputs: SystemPromptInputs,
        builder: SystemPromptBuilder | None = None,
    ) -> PromptPrefixCacheEntry:
        builder = builder or SystemPromptBuilder()
        fingerprint = builder.fingerprint(inputs)
        if self._entry is not None and self._entry.fingerprint == fingerprint:
            return self._entry

        self._entry = builder.build(inputs)
        return self._entry

    @property
    def entry(self) -> PromptPrefixCacheEntry | None:
        return self._entry


def _format_section(title: str, content: str) -> str:
    content = content.strip()
    if not content:
        return ""
    return f"{title}:\n{content}"


def _format_soul(content: str) -> str:
    content = content.strip()
    if not content:
        return ""
    return f"<soul>\n{content}\n</soul>"


def _agent_instructions() -> str:
    path = Path(__file__).with_name("prompts") / "agent_instructions.md"
    content = path.read_text(encoding="utf-8").strip()
    return content


def _format_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
