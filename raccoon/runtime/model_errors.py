"""provider 错误分类。

agent 主循环后续只应该依赖这些分类决定重试、压缩或提示用户，不应该到处解析各家
provider 的错误字符串。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProviderErrorKind(StrEnum):
    PROMPT_TOO_LONG = "prompt_too_long"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH_ERROR = "auth_error"
    CONFIG_ERROR = "config_error"
    UNSUPPORTED = "unsupported"
    SERVER_ERROR = "server_error"
    API_ERROR = "api_error"
    USER_ABORT = "user_abort"
    NETWORK_ERROR = "network_error"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class ProviderError(Exception):
    kind: ProviderErrorKind
    message: str

    @property
    def retryable(self) -> bool:
        """是否适合按普通瞬时错误策略直接重试。"""

        return self.kind in {
            ProviderErrorKind.TIMEOUT,
            ProviderErrorKind.RATE_LIMIT,
            ProviderErrorKind.NETWORK_ERROR,
            ProviderErrorKind.SERVER_ERROR,
        }

    @property
    def requires_compaction(self) -> bool:
        """是否需要先触发上下文压缩，再尝试恢复。"""

        return self.kind == ProviderErrorKind.PROMPT_TOO_LONG
