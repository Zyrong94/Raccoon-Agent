"""Reactor 兼容 Bing 网关搜索适配器。"""

from __future__ import annotations

import os
from uuid import uuid4

from raccoon.modules.tools.search._http import post_json, valid_url
from raccoon.modules.tools.search.base import SearchEngine, SearchResult


class BingSearch(SearchEngine):
    def __init__(self, timeout_seconds: int = 25):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, num_results: int = 8) -> list[SearchResult]:
        if not query.strip():
            raise ValueError("query 不能为空")
        if num_results <= 0:
            raise ValueError("num_results 必须大于 0")
        url = os.environ.get("BING_SEARCH_URL")
        api_key = os.environ.get("BING_SEARCH_API_KEY")
        if not url or not valid_url(url) or not api_key:
            raise ValueError("需要 BING_SEARCH_URL 和 BING_SEARCH_API_KEY")
        payload = await post_json(
            url,
            {"request_id": str(uuid4()), "model": "bing-search", "messages": [{"role": "user", "content": query}], "count": num_results, "stream": False},
            {"Authorization": f"Bearer {api_key}"},
            self.timeout_seconds,
            "Bing",
        )
        pages = payload.get("webPages")
        if pages is None and isinstance(payload.get("queryContext"), dict):
            return []
        hits = pages.get("value") if isinstance(pages, dict) else None
        if not isinstance(hits, list):
            raise ValueError("Bing 搜索响应格式无效")
        results = []
        for item in hits:
            if not isinstance(item, dict) or not valid_url(item.get("url")):
                continue
            title = item.get("name")
            snippet = item.get("snippet")
            results.append(SearchResult(title=title if isinstance(title, str) else "", url=item["url"], snippet=snippet if isinstance(snippet, str) else "", engine="bing"))
        return results
