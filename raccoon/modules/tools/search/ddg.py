"""DuckDuckGo 搜索适配器。"""

from __future__ import annotations

import asyncio

from raccoon.modules.tools.search._http import valid_url
from raccoon.modules.tools.search.base import SearchEngine, SearchResult

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None


class DDGSearch(SearchEngine):
    def __init__(self, timeout_seconds: int = 25):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, num_results: int = 8) -> list[SearchResult]:
        if not query.strip():
            raise ValueError("query 不能为空")
        if num_results <= 0:
            raise ValueError("num_results 必须大于 0")
        if DDGS is None:
            raise RuntimeError("缺少 ddgs 依赖")

        def run_search():
            return DDGS(timeout=self.timeout_seconds).text(query, max_results=num_results, backend="duckduckgo")

        hits = await asyncio.to_thread(run_search)
        if not isinstance(hits, list):
            raise ValueError("DDG 搜索响应格式无效")
        results = []
        for item in hits:
            if not isinstance(item, dict) or not valid_url(item.get("href")):
                continue
            title = item.get("title")
            snippet = item.get("body")
            results.append(SearchResult(title=title if isinstance(title, str) else "", url=item["href"], snippet=snippet if isinstance(snippet, str) else "", engine="ddg"))
        return results
