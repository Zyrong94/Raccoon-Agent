"""Serper 搜索适配器。"""

from __future__ import annotations

import os

from raccoon.modules.tools.search._http import post_json, valid_url
from raccoon.modules.tools.search.base import SearchEngine, SearchResult

SERPER_SEARCH_URL = "https://google.serper.dev/search"


class SerperSearch(SearchEngine):
    def __init__(self, timeout_seconds: int = 25):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, num_results: int = 8) -> list[SearchResult]:
        if not query.strip():
            raise ValueError("query 不能为空")
        if num_results <= 0:
            raise ValueError("num_results 必须大于 0")
        api_key = os.environ.get("SERPER_SEARCH_API_KEY")
        if not api_key:
            raise ValueError("缺少 SERPER_SEARCH_API_KEY")
        payload = await post_json(SERPER_SEARCH_URL, {"q": query, "num": num_results}, {"X-Api-Key": api_key}, self.timeout_seconds, "Serper")
        hits = payload.get("organic")
        if hits is None and isinstance(payload.get("searchParameters"), dict):
            return []
        if not isinstance(hits, list):
            raise ValueError("Serper 搜索响应格式无效")
        results = []
        for item in hits:
            if not isinstance(item, dict) or not valid_url(item.get("link")):
                continue
            title = item.get("title")
            snippet = item.get("snippet")
            results.append(SearchResult(title=title if isinstance(title, str) else "", url=item["link"], snippet=snippet if isinstance(snippet, str) else "", engine="serper"))
        return results
