"""网页正文读取与原始文本保留；只替换外部 HTTP 传输。"""

from raccoon.modules.tools.execution import execute_tool


from email.message import Message
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from raccoon.modules.permissions.types import PermissionAction
from raccoon.modules.tools import create_builtin_registry
import raccoon.modules.tools.fetch as fetch_module


PAGE_URL = "https://example.com/papers/index"
ARTICLE = "This paper introduces a method for understanding agents and their tools. " * 12
HTML = f"""<html><head><title>Agent paper</title><base href="/research/"></head>
<body><nav>Navigation junk</nav><article><h1>Agent paper</h1><p>{ARTICLE}</p>
<p>Read the <a href="code">official code</a>.</p></article>
<script>secretScript()</script><footer>Footer junk</footer></body></html>"""


class Response:
    status = 200

    def __init__(self, text, content_type="text/html; charset=utf-8", url=PAGE_URL):
        self.body = text if isinstance(text, bytes) else text.encode("utf-8")
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body

    def getheaders(self):
        return list(self.headers.items())

    def geturl(self):
        return self.url


@pytest.fixture
def transport(monkeypatch):
    responses = []
    requests = []

    class Opener:
        def open(self, req, timeout=None):
            requests.append(req)
            if not responses:
                raise AssertionError("unexpected HTTP request")
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(fetch_module.request, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(fetch_module.socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))])
    monkeypatch.setattr(fetch_module.request, "getproxies", lambda: {})
    monkeypatch.setattr(fetch_module.request, "proxy_bypass", lambda hostname: False)
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    return responses, requests


def run_fetch(tmp_path, **arguments):
    registry = create_builtin_registry(tmp_path, include_network_tools=True)
    return execute_tool(registry, "web_fetch", {"url": PAGE_URL, **arguments})


@pytest.mark.parametrize(
    ("content_type", "text"),
    [
        ("application/json", '{"default_branch":"main","html_url":"https://github.com/org/repo"}'),
        ("text/markdown", "# README\n\n[Source](./src/main.py)\n"),
        ("text/plain", 'def infer(x):\n    return "<html> is data, not a webpage"\n'),
    ],
)
def test_web_fetch_preserves_api_markdown_and_source(tmp_path, transport, content_type, text):
    responses, requests = transport
    responses.append(Response(text, content_type))
    result = run_fetch(tmp_path)
    assert result.ok
    assert result.content == text
    assert result.name == "web_fetch"
    assert result.data["format"] == "text"
    assert not result.data["truncated"]
    assert len(requests) == 1  # Raw data must not be sent through HTML extraction.


@pytest.mark.parametrize("container", ['main', 'div role="main"', 'article', 'body'])
def test_web_fetch_preserves_reference_definitions_tables_and_code(tmp_path, transport, container):
    tag = container.split()[0]
    reference = f"""<html><head><title>SQLite reference</title><base href="/library/"></head>
    <{container}><nav>Navigation junk</nav><h1>SQLite reference</h1>
    <section><h2>Tutorial</h2><p>{ARTICLE}</p></section>
    <section><h2>Reference</h2><dl>
    <dt id="sqlite3.Connection.autocommit">Connection.autocommit</dt>
    <dd>False: PEP 249 transactions. True: SQLite autocommit.
    <code>LEGACY_TRANSACTION_CONTROL</code>: pre-3.12 transaction control.</dd>
    <dt><code>Connection.isolation_level</code></dt><dd>Only affects <code>LEGACY_TRANSACTION_CONTROL</code>.</dd>
    </dl><table><tr><th>Mode</th><th>Effect</th></tr>
    <tr><td>False</td><td>Always open a transaction</td></tr></table>
    <pre><code>with con:\n    con.execute("SELECT 1")\n</code></pre>
    <p><a href="sqlite3.html#transaction-control">Transaction control</a></p></section>
    <script>secretScript()</script><style>secretStyle</style><noscript>Browser notice</noscript>
    <footer>Footer junk</footer></{tag}></html>"""
    responses, _ = transport
    responses.extend([Response(reference), Response(reference)])

    result = run_fetch(tmp_path)

    assert result.ok
    assert "LEGACY_TRANSACTION_CONTROL" in result.content
    assert "Connection.isolation_level" in result.content
    assert "pre-3.12 transaction control" in result.content
    assert "Always open a transaction" in result.content
    assert "Mode Effect False Always open a transaction" in result.content
    assert "```" not in result.content
    assert 'with con:\n    con.execute("SELECT 1")' in result.content
    assert "Transaction control" in result.content
    assert "https://example.com/library/sqlite3.html#transaction-control" not in result.content
    assert "Reference" in result.content
    assert result.data["format"] == "text"
    for junk in ("Navigation junk", "secretScript", "secretStyle", "Browser notice", "Footer junk"):
        assert junk not in result.content
    assert result.data["cleaned"] is True
    assert result.data["truncated"] is False


def test_extract_article_preserves_all_articles_inside_main():
    source = f"""<html><head><title>Documentation</title></head><body>
    <aside>Outside sidebar</aside><main><h1>Documentation</h1>
    <article><h2>Guide</h2><p>{ARTICLE}</p></article>
    <article><h2>API</h2><dl><dt>Connection.autocommit</dt>
    <dd><code>LEGACY_TRANSACTION_CONTROL</code></dd></dl></article></main></body></html>"""

    title, content = fetch_module._extract_article(source, PAGE_URL)

    assert title == "Documentation"
    assert "Guide" in content
    assert "API" in content
    assert "LEGACY_TRANSACTION_CONTROL" in content
    assert "Outside sidebar" not in content


def test_extract_article_uses_heading_when_title_is_missing():
    title, content = fetch_module._extract_article(
        "<html><body><main><h1>API reference</h1><p>Definition</p></main></body></html>", PAGE_URL
    )
    assert title == "API reference"
    assert "Definition" in content


def test_web_fetch_returns_plain_text_with_title_source_and_link_labels(tmp_path, transport, monkeypatch):
    responses, requests = transport
    responses.extend([Response("<html>initial page</html>"), Response(HTML)])
    monkeypatch.setenv("JINA_API_KEY", "test-jina-key")
    result = run_fetch(tmp_path)
    assert result.ok
    assert "标题：Agent paper" in result.content
    assert "This paper introduces a method" in result.content
    assert "official code" in result.content
    assert "https://example.com/research/code" not in result.content
    assert PAGE_URL in result.content
    assert "Navigation junk" not in result.content
    assert "secretScript" not in result.content
    assert "Footer junk" not in result.content
    assert result.data["title"] == "Agent paper"
    assert result.data["format"] == "text"
    assert result.data["cleaned"] is True
    assert requests[1].full_url == "https://r.jina.ai/"
    assert json.loads(requests[1].data) == {"url": PAGE_URL}
    assert requests[1].get_header("X-return-format") == "html"
    assert requests[1].get_header("Authorization") == "Bearer test-jina-key"
    assert "test-jina-key" not in repr(result)


def test_web_fetch_uses_redirect_destination_as_article_source(tmp_path, transport):
    responses, requests = transport
    final_url = "https://docs.example.org/articles/paper"
    html = HTML.replace('<base href="/research/">', "")
    responses.extend([Response("<html></html>", url=final_url), Response(html)])
    result = run_fetch(tmp_path)
    assert result.ok
    assert final_url in result.content
    assert "official code" in result.content
    assert result.data["url"] == PAGE_URL
    assert result.data["final_url"] == final_url
    assert json.loads(requests[1].data) == {"url": final_url}


@pytest.mark.parametrize("failure", [URLError("service unavailable"), TimeoutError("timed out"), Response("")])
def test_web_fetch_reuses_downloaded_html_when_jina_fails(tmp_path, transport, failure):
    responses, requests = transport
    responses.extend([Response(HTML), failure])
    result = run_fetch(tmp_path)
    assert result.ok
    assert ARTICLE.strip() in result.content
    assert "official code" in result.content
    assert result.data["cleaned"] is True
    assert result.data["format"] == "text"
    assert "Jina" in result.data["warning"]
    assert result.data["url"] == PAGE_URL
    assert len(requests) == 2


def test_web_fetch_keeps_uncleaned_state_if_direct_fallback_also_cannot_extract(tmp_path, transport):
    responses, requests = transport
    responses.extend([Response("<html><body></body></html>"), TimeoutError("timed out")])

    result = run_fetch(tmp_path)

    assert result.ok
    assert result.data["cleaned"] is False
    assert result.data["format"] == "html"
    assert "未经正文清洗" in result.content
    assert len(requests) == 2


def test_web_fetch_marks_uncleaned_html_when_extraction_fails(tmp_path, transport, monkeypatch):
    responses, _ = transport
    responses.extend([Response(HTML), Response(HTML)])
    monkeypatch.setattr(fetch_module, "_extract_article", lambda *args: (_ for _ in ()).throw(ValueError("bad HTML")), raising=False)
    result = run_fetch(tmp_path)
    assert result.ok
    assert result.data["cleaned"] is False
    assert result.data["format"] == "html"
    assert "未经正文清洗" in result.content
    assert "bad HTML" in result.data["warning"]
    assert HTML in result.content


def test_web_fetch_keeps_source_and_truncation_notice_for_long_article(tmp_path, transport):
    responses, _ = transport
    responses.extend([Response(HTML), Response(HTML)])
    result = run_fetch(tmp_path, max_chars=40)
    assert result.ok
    assert result.data["truncated"] is True
    assert PAGE_URL in result.content
    assert "响应已截断" in result.content


def test_web_fetch_decodes_declared_text_encoding(tmp_path, transport):
    responses, _ = transport
    responses.append(Response("论文源码".encode("gb18030"), "text/plain; charset=gb18030"))
    result = run_fetch(tmp_path)
    assert result.ok
    assert result.content == "论文源码"


def test_web_fetch_rejects_binary_response(tmp_path, transport):
    responses, requests = transport
    responses.append(Response(b"%PDF-1.7\x00", "application/pdf"))
    result = run_fetch(tmp_path)
    assert not result.ok
    assert "application/pdf" in result.error
    assert len(requests) == 1


def test_web_fetch_rejects_hostname_resolving_to_private_address(tmp_path, transport, monkeypatch):
    _, requests = transport
    monkeypatch.setattr(fetch_module.socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))])
    result = run_fetch(tmp_path)
    assert not result.ok
    assert "内网" in result.error
    assert not requests


def test_web_fetch_permission_targets_original_url(tmp_path):
    tool = create_builtin_registry(tmp_path, include_network_tools=True).get("web_fetch")
    assert tool is not None
    assert tool.permission.action == PermissionAction.NETWORK_REQUEST
    assert tool.permission.target_arg == "url"


def test_web_fetch_explains_jina_authentication_failure(tmp_path, transport):
    responses, _ = transport
    responses.extend([Response(HTML), HTTPError("https://r.jina.ai/", 401, "Unauthorized", {}, None)])
    result = run_fetch(tmp_path)
    assert result.ok
    assert ARTICLE.strip() in result.content
    assert "JINA_API_KEY" in result.data["warning"]
    assert "401" in result.data["warning"]


def test_redirect_does_not_forward_jina_key_to_another_origin(transport):
    req = Request("https://r.jina.ai/", data=b"{}", headers={"Authorization": "Bearer test-key"})
    redirect = fetch_module._SafeRedirectHandler().redirect_request(req, None, 302, "Found", {}, "https://other.example.org/")
    assert redirect.get_header("Authorization") is None


def test_web_fetch_marks_empty_article_as_uncleaned(tmp_path, transport):
    responses, _ = transport
    responses.extend([Response(HTML), Response("<html><body></body></html>")])
    result = run_fetch(tmp_path)
    assert result.ok
    assert result.data["cleaned"] is False
    assert "未经正文清洗" in result.content


def test_web_fetch_reports_initial_http_failure_without_trying_jina(tmp_path, transport):
    responses, requests = transport
    responses.append(HTTPError(PAGE_URL, 403, "Forbidden", {}, None))
    result = run_fetch(tmp_path)
    assert not result.ok
    assert "403" in result.error
    assert len(requests) == 1


@pytest.mark.parametrize("arguments", [{"timeout_seconds": 0}, {"max_chars": 0}])
def test_web_fetch_rejects_invalid_limits_before_network(tmp_path, transport, arguments):
    _, requests = transport
    result = run_fetch(tmp_path, **arguments)
    assert not result.ok
    assert "必须大于 0" in result.error
    assert not requests


def test_web_fetch_truncates_raw_text_without_losing_notice(tmp_path, transport):
    responses, _ = transport
    responses.append(Response("abcdef", "text/plain"))
    result = run_fetch(tmp_path, max_chars=3)
    assert result.ok
    assert result.content == "abc\n\n[响应已截断]"
    assert result.data["truncated"] is True


@pytest.mark.parametrize(
    ("proxies", "bypass", "addresses", "allowed"),
    [
        ({"https": "http://127.0.0.1:7897"}, False, ["198.18.0.133"], True),
        ({"https": "http://127.0.0.1:7897"}, False, ["198.19.255.254"], True),
        ({}, False, ["198.18.0.133"], False),
        ({"https": "http://127.0.0.1:7897"}, True, ["198.18.0.133"], False),
        ({"http": "http://127.0.0.1:7897"}, False, ["198.18.0.133"], False),
        ({"all": "http://127.0.0.1:7897"}, False, ["198.18.0.133"], False),
        ({"https": "socks5://127.0.0.1:7897"}, False, ["198.18.0.133"], False),
        ({"https": "http://127.0.0.1:7897"}, False, ["10.0.0.1"], False),
        ({"https": "http://127.0.0.1:7897"}, False, ["198.18.0.133", "127.0.0.1"], False),
        ({"https": "http://127.0.0.1:7897"}, False, ["::1"], False),
    ],
)
def test_web_fetch_allows_fake_ip_only_with_effective_http_proxy(tmp_path, transport, monkeypatch, proxies, bypass, addresses, allowed):
    responses, _ = transport
    responses.append(Response("public article", "text/plain"))
    monkeypatch.setattr(fetch_module.request, "getproxies", lambda: proxies)
    monkeypatch.setattr(fetch_module.request, "proxy_bypass", lambda hostname: bypass)
    monkeypatch.setattr(fetch_module.socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0)) for address in addresses])
    result = run_fetch(tmp_path)
    assert result.ok is allowed
    if allowed:
        assert result.content == "public article"
    else:
        assert "内网" in result.error


@pytest.mark.parametrize("url", ["https://198.18.0.133/", "https://127.0.0.1/", "https://localhost/", "https://192.168.1.1/"])
def test_proxy_does_not_allow_literal_private_targets(tmp_path, transport, monkeypatch, url):
    monkeypatch.setattr(fetch_module.request, "getproxies", lambda: {"https": "http://127.0.0.1:7897"})
    result = execute_tool(create_builtin_registry(tmp_path, include_network_tools=True), "web_fetch", {"url": url})
    assert not result.ok
    assert "内网" in result.error


@pytest.mark.parametrize("bypass", [False, True])
def test_redirect_checks_fake_ip_against_destination_proxy_bypass(transport, monkeypatch, bypass):
    monkeypatch.setattr(fetch_module.request, "getproxies", lambda: {"https": "http://127.0.0.1:7897"})
    monkeypatch.setattr(fetch_module.request, "proxy_bypass", lambda hostname: bypass)
    monkeypatch.setattr(fetch_module.socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.133", 0))])
    req = Request(PAGE_URL)
    destination = "https://other.example.org/article"
    handler = fetch_module._SafeRedirectHandler()
    if bypass:
        with pytest.raises(HTTPError):
            handler.redirect_request(req, None, 302, "Found", {}, destination)
    else:
        redirected = handler.redirect_request(req, None, 302, "Found", {}, destination)
        assert redirected.full_url == destination
