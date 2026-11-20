"""并发调用多个结构化搜索引擎。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from urllib import parse

from raccoon.modules.tools.search.base import SearchEngine, SearchResult
from raccoon.modules.tools.search.bing import BingSearch
from raccoon.modules.tools.search.ddg import DDGSearch
from raccoon.modules.tools.search.exa import ExaSearch
from raccoon.modules.tools.search.parallel import ParallelSearch
from raccoon.modules.tools.search.serper import SerperSearch
from raccoon.modules.tools.search.sogou import SogouSearch
from raccoon.modules.tools.search.tavily import TavilySearch


class MultieSearch(SearchEngine):
    def __init__(self, engines: Sequence[SearchEngine] | None = None):
        if engines is None:
            enabled = []
            if os.environ.get("EXA_API_KEY"):
                enabled.append(ExaSearch())
            if os.environ.get("TAVILY_API_KEY"):
                enabled.append(TavilySearch())
            enabled.append(DDGSearch())
            if os.environ.get("PARALLEL_API_KEY"):
                enabled.append(ParallelSearch())
            if os.environ.get("SERPER_SEARCH_API_KEY"):
                enabled.append(SerperSearch())
            if os.environ.get("BING_SEARCH_API_KEY") and os.environ.get("BING_SEARCH_URL"):
                enabled.append(BingSearch())
            if os.environ.get("SOGOU_SEARCH_API_KEY") and os.environ.get("SOGOU_SEARCH_URL"):
                enabled.append(SogouSearch())
            self.engines = tuple(enabled)
        else:
            self.engines = tuple(engines)
        if not self.engines:
            raise ValueError("至少配置一个搜索引擎")

    async def search(self, query: str, num_results: int = 8) -> list[SearchResult]:
        if not query.strip():
            raise ValueError("query 不能为空")
        if num_results <= 0:
            raise ValueError("num_results 必须大于 0")

        responses = await asyncio.gather(
            *(engine.search(query, num_results) for engine in self.engines),
            return_exceptions=True,
        )
        found = []
        seen_urls = set()
        failures = []
        for response in responses:
            if isinstance(response, BaseException):
                if not isinstance(response, Exception):
                    raise response
                failures.append(response)
                continue
            for item in response:
                url_key = _canonical_url(item.url)
                if url_key not in seen_urls:
                    seen_urls.add(url_key)
                    found.append(item)
                    if len(found) == num_results:
                        return found

        if len(failures) == len(self.engines):
            raise RuntimeError("所有搜索引擎均失败") from failures[0]
        return found


def _canonical_url(url: str) -> str:
    """仅规范化不改变页面身份的 URL 部分，保留路径和查询参数。"""

    try:
        parts = parse.urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return url
    scheme = parts.scheme.lower()
    if ":" in host:
        host = f"[{host}]"
    userinfo = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = userinfo + host.lower() + (f":{port}" if port is not None and not default_port else "")
    return parse.urlunsplit((scheme, netloc, parts.path, parts.query, ""))
