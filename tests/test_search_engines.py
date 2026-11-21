"""结构化搜索引擎适配测试。"""

from __future__ import annotations

import asyncio
from urllib import error

import pytest

from raccoon.modules.tools.search import ExaSearch, SearchEngine, SearchResult, TavilySearch
import raccoon.modules.tools.search.exa as exa_module
import raccoon.modules.tools.search.tavily as tavily_module


class FakeResponse:
    def __init__(self, body: str):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body.encode("utf-8")


def test_exa_search_returns_structured_results(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["request"] = req
        captured["timeout"] = timeout
        return FakeResponse(
            '{"results":['
            '{"title":"Raccoon docs","url":"https://example.com/docs",'
            '"text":"Full page text.","extract":"Old extract",'
            '"highlights":["An event-driven, asynchronous agent.","Runs locally."],"publishedDate":"2026-10-20"},'
            '{"title":"Second page","url":"https://example.com/second"}'
            ']}'
        )

    monkeypatch.setenv("EXA_API_KEY", "secret-token")
    monkeypatch.setattr(exa_module.request, "urlopen", fake_urlopen)

    engine = ExaSearch(timeout_seconds=7)
    results = asyncio.run(engine.search("event-driven agents", num_results=2))

    assert isinstance(engine, SearchEngine)
    assert results == [
        SearchResult(
            title="Raccoon docs",
            url="https://example.com/docs",
            snippet="An event-driven, asynchronous agent. Runs locally.",
            engine="exa",
            published_date="2026-10-20",
            content="Full page text.",
        ),
        SearchResult(title="Second page", url="https://example.com/second", snippet="", engine="exa"),
    ]
    assert captured["request"].full_url == "https://api.exa.ai/search"
    assert captured["request"].get_header("X-api-key") == "secret-token"
    assert captured["request"].data == b'{"query":"event-driven agents","numResults":2,"type":"auto","contents":{"text":{"maxCharacters":20000}}}'
    assert captured["timeout"] == 7


def test_exa_search_accepts_empty_results(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "key")
    monkeypatch.setattr(exa_module.request, "urlopen", lambda req, timeout: FakeResponse('{"results":[]}'))

    assert asyncio.run(ExaSearch().search("no matches")) == []


def test_exa_search_uses_summary_when_highlights_are_absent(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "key")
    monkeypatch.setattr(
        exa_module.request,
        "urlopen",
        lambda req, timeout: FakeResponse('{"results":[{"title":"Page","url":"https://example.com","summary":"Page summary"}]}'),
    )

    assert asyncio.run(ExaSearch().search("query")) == [
        SearchResult(title="Page", url="https://example.com", snippet="Page summary", engine="exa")
    ]


def test_exa_search_uses_extract_as_content_fallback(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "key")
    monkeypatch.setattr(
        exa_module.request,
        "urlopen",
        lambda req, timeout: FakeResponse('{"results":[{"title":"Page","url":"https://example.com","extract":"Extracted page text"}]}'),
    )

    assert asyncio.run(ExaSearch().search("query")) == [
        SearchResult(title="Page", url="https://example.com", snippet="", engine="exa", content="Extracted page text")
    ]


@pytest.mark.parametrize("body", ['not json', '{}', '{"results":{}}'])
def test_exa_search_rejects_malformed_response(monkeypatch, body):
    monkeypatch.setenv("EXA_API_KEY", "key")
    monkeypatch.setattr(exa_module.request, "urlopen", lambda req, timeout: FakeResponse(body))

    with pytest.raises(ValueError, match="Exa 搜索响应格式无效"):
        asyncio.run(ExaSearch().search("query"))


def test_exa_search_ignores_results_without_valid_url(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "key")
    monkeypatch.setattr(
        exa_module.request,
        "urlopen",
        lambda req, timeout: FakeResponse('{"results":[{"title":"bad","url":""},{"title":"ok","url":"https://example.com"}]}'),
    )

    assert asyncio.run(ExaSearch().search("query")) == [SearchResult(title="ok", url="https://example.com", snippet="", engine="exa")]


def test_exa_search_requires_key_without_network_call(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setattr(exa_module.request, "urlopen", lambda req, timeout: pytest.fail("unexpected request"))

    with pytest.raises(ValueError, match="EXA_API_KEY"):
        asyncio.run(ExaSearch().search("query"))


def test_exa_search_propagates_transport_error(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "key")

    def fail(req, timeout):
        raise error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(exa_module.request, "urlopen", fail)

    with pytest.raises(error.HTTPError) as exc:
        asyncio.run(ExaSearch().search("query"))
    assert exc.value.code == 401


@pytest.mark.parametrize("query, limit", [(" ", 1), ("valid", 0)])
def test_exa_search_rejects_invalid_input(monkeypatch, query, limit):
    monkeypatch.setenv("EXA_API_KEY", "key")
    monkeypatch.setattr(exa_module.request, "urlopen", lambda req, timeout: pytest.fail("unexpected request"))

    with pytest.raises(ValueError):
        asyncio.run(ExaSearch().search(query, num_results=limit))


def test_tavily_search_returns_structured_results(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["request"] = req
        captured["timeout"] = timeout
        return FakeResponse(
            '{"results":['
            '{"title":"Research guide","url":"https://example.com/guide",'
            '"content":"Relevant passage.","raw_content":"Full cleaned page.","published_date":"2026-10-20"},'
            '{"title":"Second page","url":"https://example.com/second","content":"Short passage.","raw_content":null}'
            ']}'
        )

    monkeypatch.setenv("TAVILY_API_KEY", "secret-token")
    monkeypatch.setattr(tavily_module.request, "urlopen", fake_urlopen)

    engine = TavilySearch(timeout_seconds=7)
    results = asyncio.run(engine.search("event-driven agents", num_results=2))

    assert isinstance(engine, SearchEngine)
    assert results == [
        SearchResult(
            title="Research guide",
            url="https://example.com/guide",
            snippet="Relevant passage.",
            engine="tavily",
            published_date="2026-10-20",
            content="Full cleaned page.",
        ),
        SearchResult(title="Second page", url="https://example.com/second", snippet="Short passage.", engine="tavily"),
    ]
    assert captured["request"].full_url == "https://api.tavily.com/search"
    assert captured["request"].get_header("Authorization") == "Bearer secret-token"
    assert captured["request"].data == b'{"query":"event-driven agents","max_results":2,"include_raw_content":true,"include_published_date":true}'
    assert captured["timeout"] == 7


def test_tavily_search_accepts_empty_results(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "key")
    monkeypatch.setattr(tavily_module.request, "urlopen", lambda req, timeout: FakeResponse('{"results":[]}'))

    assert asyncio.run(TavilySearch().search("no matches")) == []


@pytest.mark.parametrize("body", ['not json', '{}', '{"results":{}}'])
def test_tavily_search_rejects_malformed_response(monkeypatch, body):
    monkeypatch.setenv("TAVILY_API_KEY", "key")
    monkeypatch.setattr(tavily_module.request, "urlopen", lambda req, timeout: FakeResponse(body))

    with pytest.raises(ValueError, match="Tavily 搜索响应格式无效"):
        asyncio.run(TavilySearch().search("query"))


def test_tavily_search_ignores_results_without_valid_url(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "key")
    monkeypatch.setattr(
        tavily_module.request,
        "urlopen",
        lambda req, timeout: FakeResponse('{"results":[{"title":"bad","url":""},{"title":"ok","url":"https://example.com","content":"Excerpt"}]}'),
    )

    assert asyncio.run(TavilySearch().search("query")) == [
        SearchResult(title="ok", url="https://example.com", snippet="Excerpt", engine="tavily")
    ]


def test_tavily_search_requires_key_without_network_call(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(tavily_module.request, "urlopen", lambda req, timeout: pytest.fail("unexpected request"))

    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        asyncio.run(TavilySearch().search("query"))


def test_tavily_search_propagates_transport_error(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "key")

    def fail(req, timeout):
        raise error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(tavily_module.request, "urlopen", fail)

    with pytest.raises(error.HTTPError) as exc:
        asyncio.run(TavilySearch().search("query"))
    assert exc.value.code == 401


@pytest.mark.parametrize("query, limit", [(" ", 1), ("valid", 0), ("valid", 21)])
def test_tavily_search_rejects_invalid_input(monkeypatch, query, limit):
    monkeypatch.setenv("TAVILY_API_KEY", "key")
    monkeypatch.setattr(tavily_module.request, "urlopen", lambda req, timeout: pytest.fail("unexpected request"))

    with pytest.raises(ValueError):
        asyncio.run(TavilySearch().search(query, num_results=limit))
