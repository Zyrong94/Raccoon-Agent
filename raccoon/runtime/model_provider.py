"""provider 抽象接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from raccoon.runtime.model_types import ChatMessage, ChatRequest, ChatResponse


class ChatProvider(ABC):
    """所有模型 provider 都要实现的统一接口。

    agent 主循环只依赖这个接口，不直接依赖 模型厂商 SDK。
    这样后续切换模型时，只需要替换 provider 实现或配置。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """provider 名称，例如 `official`、`relay`。"""

    @property
    @abstractmethod
    def model(self) -> str:
        """当前 provider 默认使用的模型名称。"""

    @abstractmethod
    async def acomplete(self, request: ChatRequest) -> ChatResponse:
        """异步等待一次完整回复。"""

    def estimate_replay_tokens(self, messages: list[ChatMessage]) -> int:
        """Estimate opaque replay overhead not represented by visible message text."""
        return 0

