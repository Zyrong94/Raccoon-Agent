"""其余搜索来源的结构化适配测试。"""

from __future__ import annotations

import asyncio
from urllib import error

import pytest

from raccoon.modules.tools.search import BingSearch, DDGSearch, ParallelSearch, SearchResult, SerperSearch, SogouSearch
import raccoon.modules.tools.search._http as search_http
import raccoon.modules.tools.search.ddg as ddg
from raccoon.utils.json_utils import loads_json


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body.encode("utf-8")


def test_ddg_search_maps_hits_without_a_key(monkeypatch):
    captured = {}

    class FakeDDGS:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        def text(self, query, **kwargs):
            captured["query"] = query
            captured["search"] = kwargs
            return [
                {"title": "Page", "href": "https://example.com/a", "body": "Result snippet"},
                {"title": "Broken", "href": "javascript:alert(1)", "body": "bad"},
            ]

    monkeypatch.setattr(ddg, "DDGS", FakeDDGS)

    found = asyncio.run(DDGSearch(timeout_seconds=7).search("agents", num_results=2))

    assert found == [SearchResult(title="Page", url="https://example.com/a", snippet="Result snippet", engine="ddg")]
    assert captured["init"]["timeout"] == 7
    assert captured["query"] == "agents"
    assert captured["search"]["max_results"] == 2
    assert captured["search"]["backend"] == "duckduckgo"


def test_ddg_search_propagates_library_failure(monkeypatch):
    class FakeDDGS:
        def __init__(self, **kwargs):
            pass

        def text(self, query, **kwargs):
            raise RuntimeError("rate limited")

    monkeypatch.setattr(ddg, "DDGS", FakeDDGS)
    with pytest.raises(RuntimeError, match="rate limited"):
        asyncio.run(DDGSearch().search("agents"))


def test_ddg_search_accepts_empty_results(monkeypatch):
    class FakeDDGS:
        def __init__(self, **kwargs):
            pass

        def text(self, query, **kwargs):
            return []

    monkeypatch.setattr(ddg, "DDGS", FakeDDGS)
    assert asyncio.run(DDGSearch().search("no matches")) == []


@pytest.mark.parametrize("query, count", [(" ", 1), ("agents", 0)])
def test_ddg_search_rejects_invalid_input(monkeypatch, query, count):
    monkeypatch.setattr(ddg, "DDGS", lambda **kwargs: pytest.fail("unexpected search"))
    with pytest.raises(ValueError):
        asyncio.run(DDGSearch().search(query, count))


@pytest.mark.parametrize(
    ("engine", "key_name", "url", "payload", "response", "expected"),
    [
        (
            ParallelSearch,
            "PARALLEL_API_KEY",
            "https://api.parallel.ai/v1/search",
            {"objective": "agents", "search_queries": ["agents"], "mode": "fast", "advanced_settings": {"max_results": 2}},
            '{"results":[{"title":"Page","url":"https://example.com/a","excerpts":["First excerpt","Second excerpt"],"publish_date":"2026-10-20"}]}',
            SearchResult(title="Page", url="https://example.com/a", snippet="First excerpt\nSecond excerpt", engine="parallel", published_date="2026-10-20"),
        ),
        (
            SerperSearch,
            "SERPER_SEARCH_API_KEY",
            "https://google.serper.dev/search",
            {"q": "agents", "num": 2},
            '{"organic":[{"title":"Page","link":"https://example.com/a","snippet":"Result snippet"}]}',
            SearchResult(title="Page", url="https://example.com/a", snippet="Result snippet", engine="serper"),
        ),
        (
            BingSearch,
            "BING_SEARCH_API_KEY",
            "https://bing-gateway.example/search",
            {"model": "bing-search", "messages": [{"role": "user", "content": "agents"}], "count": 2, "stream": False},
            '{"webPages":{"value":[{"name":"Page","url":"https://example.com/a","snippet":"Result snippet"}]}}',
            SearchResult(title="Page", url="https://example.com/a", snippet="Result snippet", engine="bing"),
        ),
        (
            SogouSearch,
            "SOGOU_SEARCH_API_KEY",
            "https://sogou-gateway.example/search",
            {"model": "search_pro_sogou", "messages": [{"role": "user", "content": "agents"}], "count": 2, "stream": False},
            '{"search_result":[{"title":"Page","link":"https://example.com/a","content":"Result snippet"}]}',
            SearchResult(title="Page", url="https://example.com/a", snippet="Result snippet", engine="sogou"),
        ),
    ],
)
def test_rest_search_maps_results(monkeypatch, engine, key_name, url, payload, response, expected):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["request"] = req
        captured["timeout"] = timeout
        return FakeResponse(response)

    monkeypatch.setenv(key_name, "secret-token")
    if engine is BingSearch:
        monkeypatch.setenv("BING_SEARCH_URL", url)
    if engine is SogouSearch:
        monkeypatch.setenv("SOGOU_SEARCH_URL", url)
    monkeypatch.setattr(search_http.request, "urlopen", fake_urlopen)

    found = asyncio.run(engine(timeout_seconds=7).search("agents", num_results=2))

    assert found == [expected]
    assert captured["request"].full_url == url
    assert captured["timeout"] == 7
    body = loads_json(captured["request"].data)
    for key, value in payload.items():
        assert body[key] == value
    if engine is ParallelSearch:
        assert captured["request"].get_header("X-api-key") == "secret-token"
    elif engine is SerperSearch:
        assert captured["request"].get_header("X-api-key") == "secret-token"
    else:
        assert captured["request"].get_header("Authorization") == "Bearer secret-token"


@pytest.mark.parametrize(
    ("engine", "key_name", "url_name"),
    [
        (ParallelSearch, "PARALLEL_API_KEY", None),
        (SerperSearch, "SERPER_SEARCH_API_KEY", None),
        (BingSearch, "BING_SEARCH_API_KEY", "BING_SEARCH_URL"),
        (SogouSearch, "SOGOU_SEARCH_API_KEY", "SOGOU_SEARCH_URL"),
    ],
)
def test_rest_search_requires_configuration(monkeypatch, engine, key_name, url_name):
    monkeypatch.delenv(key_name, raising=False)
    if url_name:
        monkeypatch.delenv(url_name, raising=False)
    monkeypatch.setattr(search_http.request, "urlopen", lambda req, timeout: pytest.fail("unexpected request"))

    with pytest.raises(ValueError):
        asyncio.run(engine().search("agents"))


@pytest.mark.parametrize(
    ("engine", "key_name", "url_name"),
    [
        (ParallelSearch, "PARALLEL_API_KEY", None),
        (SerperSearch, "SERPER_SEARCH_API_KEY", None),
        (BingSearch, "BING_SEARCH_API_KEY", "BING_SEARCH_URL"),
        (SogouSearch, "SOGOU_SEARCH_API_KEY", "SOGOU_SEARCH_URL"),
    ],
)
def test_rest_search_rejects_bad_payload_and_propagates_http_errors(monkeypatch, engine, key_name, url_name):
    monkeypatch.setenv(key_name, "key")
    if url_name:
        monkeypatch.setenv(url_name, "https://gateway.example/search")
    monkeypatch.setattr(search_http.request, "urlopen", lambda req, timeout: FakeResponse("{}"))
    with pytest.raises(ValueError, match="响应格式无效"):
        asyncio.run(engine().search("agents"))

    def fail(req, timeout):
        raise error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(search_http.request, "urlopen", fail)
    with pytest.raises(error.HTTPError) as exc:
        asyncio.run(engine().search("agents"))
    assert exc.value.code == 401


@pytest.mark.parametrize(
    ("engine", "key_name", "url_name", "response"),
    [
        (ParallelSearch, "PARALLEL_API_KEY", None, '{"results":[]}'),
        (SerperSearch, "SERPER_SEARCH_API_KEY", None, '{"searchParameters":{"q":"no matches"}}'),
        (BingSearch, "BING_SEARCH_API_KEY", "BING_SEARCH_URL", '{"queryContext":{"originalQuery":"no matches"}}'),
        (SogouSearch, "SOGOU_SEARCH_API_KEY", "SOGOU_SEARCH_URL", '{"search_result":[]}'),
    ],
)
def test_rest_search_accepts_empty_results(monkeypatch, engine, key_name, url_name, response):
    monkeypatch.setenv(key_name, "key")
    if url_name:
        monkeypatch.setenv(url_name, "https://gateway.example/search")
    monkeypatch.setattr(search_http.request, "urlopen", lambda req, timeout: FakeResponse(response))

    assert asyncio.run(engine().search("no matches")) == []
