"""多引擎搜索编排测试。"""

from __future__ import annotations

import asyncio

import pytest

from raccoon.modules.tools.search import BingSearch, DDGSearch, ExaSearch, MultieSearch, ParallelSearch, SearchEngine, SearchResult, SerperSearch, SogouSearch, TavilySearch


class FakeSearch(SearchEngine):
    def __init__(self, results=None, error=None):
        self.results = [] if results is None else results
        self.error = error
        self.calls = []

    async def search(self, query: str, num_results: int = 8) -> list[SearchResult]:
        self.calls.append((query, num_results))
        if self.error:
            raise self.error
        return self.results


def result(url: str, engine: str) -> SearchResult:
    return SearchResult(title=url, url=url, snippet="", engine=engine)


def test_multie_search_defaults_to_ddg_without_keys(monkeypatch):
    for name in (
        "EXA_API_KEY", "TAVILY_API_KEY", "PARALLEL_API_KEY", "SERPER_SEARCH_API_KEY",
        "BING_SEARCH_API_KEY", "BING_SEARCH_URL", "SOGOU_SEARCH_API_KEY", "SOGOU_SEARCH_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    assert [type(engine) for engine in MultieSearch().engines] == [DDGSearch]


def test_multie_search_enables_configured_engines(monkeypatch):
    for name in ("EXA_API_KEY", "TAVILY_API_KEY", "PARALLEL_API_KEY", "SERPER_SEARCH_API_KEY", "BING_SEARCH_API_KEY", "SOGOU_SEARCH_API_KEY"):
        monkeypatch.setenv(name, "test-key")
    monkeypatch.setenv("BING_SEARCH_URL", "https://bing-gateway.example/search")
    monkeypatch.setenv("SOGOU_SEARCH_URL", "https://sogou-gateway.example/search")

    assert [type(engine) for engine in MultieSearch().engines] == [ExaSearch, TavilySearch, DDGSearch, ParallelSearch, SerperSearch, BingSearch, SogouSearch]


def test_multie_search_preserves_engine_order_and_limits_results():
    exa = FakeSearch([result("https://example.com/a", "exa"), result("https://example.com/b", "exa")])
    tavily = FakeSearch([result("https://example.com/c", "tavily")])

    found = asyncio.run(MultieSearch([exa, tavily]).search("agents", num_results=2))

    assert found == [exa.results[0], exa.results[1]]
    assert exa.calls == [("agents", 2)]
    assert tavily.calls == [("agents", 2)]


def test_multie_search_runs_engines_concurrently():
    started = 0
    both_started = asyncio.Event()

    class WaitingSearch(SearchEngine):
        def __init__(self, name):
            self.name = name

        async def search(self, query: str, num_results: int = 8) -> list[SearchResult]:
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await both_started.wait()
            return [result(f"https://example.com/{self.name}", self.name)]

    found = asyncio.run(asyncio.wait_for(MultieSearch([WaitingSearch("first"), WaitingSearch("second")]).search("agents"), timeout=1))

    assert started == 2
    assert [item.engine for item in found] == ["first", "second"]


def test_multie_search_deduplicates_conservative_url_variants():
    first = FakeSearch([result("HTTPS://Example.com:443/article?lang=en#top", "exa")])
    second = FakeSearch(
        [
            result("https://example.com/article?lang=en#section", "tavily"),
            result("https://example.com/article?lang=zh", "tavily"),
        ]
    )

    found = asyncio.run(MultieSearch([first, second]).search("agents"))

    assert found == [first.results[0], second.results[1]]


def test_multie_search_keeps_results_when_one_engine_fails():
    failing = FakeSearch(error=RuntimeError("provider unavailable"))
    working = FakeSearch([result("https://example.com/a", "tavily")])

    assert asyncio.run(MultieSearch([failing, working]).search("agents")) == working.results


def test_multie_search_distinguishes_empty_results_from_all_failures():
    assert asyncio.run(MultieSearch([FakeSearch(), FakeSearch()]).search("agents")) == []

    with pytest.raises(RuntimeError, match="所有搜索引擎均失败"):
        asyncio.run(MultieSearch([FakeSearch(error=RuntimeError("one")), FakeSearch(error=RuntimeError("two"))]).search("agents"))


def test_multie_search_rejects_empty_engines_and_invalid_input():
    with pytest.raises(ValueError, match="至少配置一个搜索引擎"):
        MultieSearch([])
    engine = FakeSearch()
    with pytest.raises(ValueError, match="query"):
        asyncio.run(MultieSearch([engine]).search(" "))
    with pytest.raises(ValueError, match="num_results"):
        asyncio.run(MultieSearch([engine]).search("agents", num_results=0))
    assert engine.calls == []
