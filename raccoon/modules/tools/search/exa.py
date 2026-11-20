"""Exa REST 搜索适配器。"""

from __future__ import annotations

import asyncio
import os
from urllib import parse, request

from raccoon.modules.tools.search.base import SearchEngine, SearchResult
from raccoon.utils.json_utils import dumps_json, loads_json

EXA_SEARCH_URL = "https://api.exa.ai/search"


class ExaSearch(SearchEngine):
    def __init__(self, timeout_seconds: int = 25):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, num_results: int = 8) -> list[SearchResult]:
        if not query.strip():
            raise ValueError("query 不能为空")
        if num_results <= 0:
            raise ValueError("num_results 必须大于 0")
        api_key = os.environ.get("EXA_API_KEY")
        if not api_key:
            raise ValueError("缺少 EXA_API_KEY")

        body = dumps_json(
            {"query": query, "numResults": num_results, "type": "auto", "contents": {"text": {"maxCharacters": 20000}}}
        ).encode("utf-8")
        req = request.Request(
            EXA_SEARCH_URL,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "X-Api-Key": api_key, "User-Agent": "Raccoon/0.1"},
        )
        raw = await asyncio.to_thread(self._request, req)
        try:
            payload = loads_json(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise ValueError("Exa 搜索响应格式无效") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("Exa 搜索响应格式无效")

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
            highlights = item.get("highlights")
            snippet = " ".join(part for part in highlights if isinstance(part, str)) if isinstance(highlights, list) else ""
            if not snippet and isinstance(item.get("summary"), str):
                snippet = item["summary"]
            title = item.get("title")
            published_date = item.get("publishedDate")
            content = item.get("text") or item.get("extract") or ""
            results.append(
                SearchResult(
                    title=title if isinstance(title, str) else "",
                    url=url,
                    snippet=snippet,
                    engine="exa",
                    published_date=published_date if isinstance(published_date, str) else None,
                    content=content if isinstance(content, str) else "",
                )
            )
        return results

    def _request(self, req: request.Request) -> bytes:
        with request.urlopen(req, timeout=self.timeout_seconds) as response:
            return response.read()
