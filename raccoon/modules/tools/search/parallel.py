"""Parallel Search API v1 适配器。"""

from __future__ import annotations

import os

from raccoon.modules.tools.search._http import post_json, valid_url
from raccoon.modules.tools.search.base import SearchEngine, SearchResult

PARALLEL_SEARCH_URL = "https://api.parallel.ai/v1/search"


class ParallelSearch(SearchEngine):
    def __init__(self, timeout_seconds: int = 25):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, num_results: int = 8) -> list[SearchResult]:
        if not query.strip():
            raise ValueError("query 不能为空")
        if not 1 <= num_results <= 20:
            raise ValueError("num_results 必须在 1 到 20 之间")
        api_key = os.environ.get("PARALLEL_API_KEY")
        if not api_key:
            raise ValueError("缺少 PARALLEL_API_KEY")
        payload = await post_json(
            PARALLEL_SEARCH_URL,
            {"objective": query, "search_queries": [query], "mode": "fast", "advanced_settings": {"max_results": num_results}},
            {"X-Api-Key": api_key},
            self.timeout_seconds,
            "Parallel",
        )
        hits = payload.get("results")
        if not isinstance(hits, list):
            raise ValueError("Parallel 搜索响应格式无效")
        results = []
        for item in hits:
            if not isinstance(item, dict) or not valid_url(item.get("url")):
                continue
            excerpts = item.get("excerpts")
            snippet = "\n".join(part for part in excerpts if isinstance(part, str)) if isinstance(excerpts, list) else ""
            title = item.get("title")
            published_date = item.get("publish_date")
            results.append(
                SearchResult(
                    title=title if isinstance(title, str) else "",
                    url=item["url"],
                    snippet=snippet,
                    engine="parallel",
                    published_date=published_date if isinstance(published_date, str) else None,
                )
            )
        return results
