"""Tavily REST 搜索适配器。"""

from __future__ import annotations

import asyncio
import os
from urllib import parse, request

from raccoon.modules.tools.search.base import SearchEngine, SearchResult
from raccoon.utils.json_utils import dumps_json, loads_json

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class TavilySearch(SearchEngine):
    def __init__(self, timeout_seconds: int = 25):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, num_results: int = 8) -> list[SearchResult]:
        if not query.strip():
            raise ValueError("query 不能为空")
        if not 1 <= num_results <= 20:
            raise ValueError("num_results 必须在 1 到 20 之间")
        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            raise ValueError("缺少 TAVILY_API_KEY")

        body = dumps_json(
            {"query": query, "max_results": num_results, "include_raw_content": True, "include_published_date": True}
        ).encode("utf-8")
        req = request.Request(
            TAVILY_SEARCH_URL,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}", "User-Agent": "Raccoon/0.1"},
        )
        raw = await asyncio.to_thread(self._request, req)
        try:
            payload = loads_json(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise ValueError("Tavily 搜索响应格式无效") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("Tavily 搜索响应格式无效")

        results = []
        for item in payload["results"]:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str):
                continue
            parsed_url = parse.urlparse(url)
            if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
                continue
            title = item.get("title")
            snippet = item.get("content")
            content = item.get("raw_content")
            published_date = item.get("published_date")
            results.append(
                SearchResult(
                    title=title if isinstance(title, str) else "",
                    url=url,
                    snippet=snippet if isinstance(snippet, str) else "",
                    engine="tavily",
                    published_date=published_date if isinstance(published_date, str) else None,
                    content=content if isinstance(content, str) else "",
                )
            )
        return results

    def _request(self, req: request.Request) -> bytes:
        with request.urlopen(req, timeout=self.timeout_seconds) as response:
            return response.read()
