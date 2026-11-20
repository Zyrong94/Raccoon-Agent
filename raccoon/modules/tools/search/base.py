"""多引擎搜索共享的数据与调用接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    engine: str
    published_date: str | None = None
    content: str = ""
    # False means no known truncation, not a guarantee of a complete page.
    content_truncated: bool = False


class SearchEngine(ABC):
    @abstractmethod
    async def search(self, query: str, num_results: int = 8) -> list[SearchResult]:
        """搜索并返回统一格式的结果；请求失败时抛出异常。"""
