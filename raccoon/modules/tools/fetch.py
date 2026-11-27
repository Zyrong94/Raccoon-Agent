"""`web_fetch`：网页正文与原始文本读取。"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
from email.message import Message
from urllib import error, parse, request

from bs4 import BeautifulSoup

from raccoon.modules.permissions.types import PermissionAction
from raccoon.modules.tools.types import Tool, ToolPermissionSpec, ToolResult, make_error_result, make_text_result
from raccoon.modules.tools.introspection import tool_from_function
from raccoon.utils.text import truncate

DEFAULT_FETCH_TIMEOUT_SECONDS = 20
DEFAULT_MAX_CHARS = 20000
PROXY_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


def create_web_fetch_tool() -> Tool:
    """创建网页读取工具；HTML 优先 Jina，失败时清洗已下载的原网页。"""

    def web_fetch(url: str, timeout_seconds: int = DEFAULT_FETCH_TIMEOUT_SECONDS, max_chars: int = DEFAULT_MAX_CHARS) -> ToolResult:
        """读取明确 URL；网页返回清洗后的正文纯文本，API 和源码返回原始文本。"""

        parsed = parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return make_error_result("web_fetch", "只支持 http 和 https URL")
        if _is_private_or_local_url(parsed):
            return make_error_result("web_fetch", "拒绝访问本机、内网或链路本地地址")
        if timeout_seconds <= 0:
            return make_error_result("web_fetch", "timeout_seconds 必须大于 0")
        if max_chars <= 0:
            return make_error_result("web_fetch", "max_chars 必须大于 0")
        if _resolves_to_private_ip(parsed.hostname or "", scheme=parsed.scheme):
            return make_error_result("web_fetch", "目标域名无法解析或解析到内网地址", url=url)

        req = request.Request(url, headers={"User-Agent": "Raccoon/0.1"})
        opener = request.build_opener(_SafeRedirectHandler)
        try:
            with opener.open(req, timeout=timeout_seconds) as response:
                text, content_type = _read_text_response(response)
                status = getattr(response, "status", None)
                headers = dict(response.getheaders())
                final_url = response.geturl() if hasattr(response, "geturl") else url
        except (error.URLError, OSError, ValueError) as exc:
            return make_error_result("web_fetch", f"请求失败：{exc}", url=url)

        data = {"url": url, "final_url": final_url, "status": status, "headers": headers, "format": "text"}
        prefix = ""
        if content_type in {"text/html", "application/xhtml+xml"}:
            article_html = text
            try:
                article_html = _fetch_jina_html(opener, final_url, timeout_seconds)
            except (error.URLError, OSError, ValueError) as exc:
                hint = "；请配置或检查环境变量 JINA_API_KEY" if isinstance(exc, error.HTTPError) and exc.code == 401 else ""
                data["warning"] = f"Jina 网页抓取失败：{exc}{hint}；已回退到原网页正文提取"
            try:
                title, text = _extract_article(article_html, final_url)
                prefix = f"标题：{title}\n来源：{final_url}\n\n"
                data.update(title=title, format="text", cleaned=True)
            except Exception as exc:
                # Extraction failure is visible; raw HTML is not presented as an article.
                text = article_html
                warning = f"正文提取失败，以下内容未经正文清洗：{exc}"
                prefix = f"来源：{final_url}\n\n[{warning}]\n\n"
                data.update(format="html", cleaned=False, warning=warning)

        content, truncated = truncate(text, max_chars, suffix="\n\n[响应已截断]")
        return make_text_result("web_fetch", prefix + content, truncated=truncated, **data)

    tool = tool_from_function(web_fetch, supports_background=True)
    tool.permission = ToolPermissionSpec(
        action=PermissionAction.NETWORK_REQUEST,
        target_arg="url",
        reason="读取网页需要网络访问；HTML 网页会通过 Jina Reader 提取。",
    )
    return tool


def _read_text_response(response) -> tuple[str, str]:
    headers = Message()
    for key, value in response.getheaders():
        headers[key] = value
    content_type = headers.get_content_type()
    if not (content_type.startswith("text/") or content_type.endswith(("+json", "+xml")) or content_type in {"application/json", "application/xml", "application/javascript", "application/x-python-code"}):
        raise ValueError(f"不支持非文本响应：{content_type}；PDF 请保存到本地后使用 PDF 工具")
    body = response.read()
    try:
        return body.decode(headers.get_content_charset() or "utf-8", errors="replace"), content_type
    except LookupError:
        return body.decode("utf-8", errors="replace"), content_type


def _fetch_jina_html(opener, url: str, timeout_seconds: int) -> str:
    headers = {"Content-Type": "application/json", "X-Return-Format": "html", "X-Timeout": str(timeout_seconds)}
    if api_key := os.getenv("JINA_API_KEY"):
        headers["Authorization"] = f"Bearer {api_key}"
    req = request.Request("https://r.jina.ai/", data=json.dumps({"url": url}).encode("utf-8"), headers=headers)
    with opener.open(req, timeout=timeout_seconds) as response:
        text, _ = _read_text_response(response)
    if not text.strip() or text.startswith("Error:"):
        raise ValueError("Jina 未返回有效网页内容")
    return text


def _extract_article(source: str, url: str) -> tuple[str, str]:
    page = BeautifulSoup(source, "html.parser")
    for element in page.find_all(["nav", "footer", "script", "style", "noscript"]):
        element.decompose()

    # Keep whole content regions: article scoring can discard API definitions and short sections.
    regions = page.select("main, [role='main']") or page.find_all("article") or [page.body or page]
    selected_ids = {id(region) for region in regions}
    regions = [region for region in regions if not any(id(parent) in selected_ids for parent in region.parents)]
    text = "\n\n".join(region.get_text(" ", strip=True) for region in regions).strip()
    if not text:
        raise ValueError("未能提取网页正文")
    titles = [" ".join(node.get_text(" ", strip=True).split()) for node in page.select("title, h1")]
    title = next((title for title in titles if title), "Untitled")
    return title, text


# Retain the Python factory import for existing integrations; only web_fetch is registered.
create_fetch_tool = create_web_fetch_tool


def _is_private_or_local_url(parsed: parse.ParseResult) -> bool:
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not hostname:
        return True
    if hostname in {"localhost", "ip6-localhost"} or hostname.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved


class _SafeRedirectHandler(request.HTTPRedirectHandler):
    """Reject redirects to private, local, or newly-resolved private targets."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = parse.urlparse(newurl)
        if parsed.scheme not in ("http", "https"):
            raise error.HTTPError(
                newurl, code, f"不安全的重定向协议：{parsed.scheme}", headers, fp,
            )
        if _is_private_or_local_url(parsed):
            raise error.HTTPError(
                newurl, code, "重定向目标为本机、内网或链路本地地址", headers, fp,
            )
        if _resolves_to_private_ip(parsed.hostname or "", scheme=parsed.scheme):
            raise error.HTTPError(
                newurl, code, "重定向目标解析到内网地址", headers, fp,
            )
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        original = parse.urlparse(req.full_url)
        if redirected is not None and (original.scheme, original.hostname, original.port) != (parsed.scheme, parsed.hostname, parsed.port):
            redirected.remove_header("Authorization")
        return redirected


def _resolves_to_private_ip(hostname: str, *, scheme: str | None = None) -> bool:
    """Check DNS targets, allowing proxy Fake-IP only for requests using an HTTP proxy."""

    hostname = hostname.rstrip(".").lower()
    if not hostname:
        return True
    proxy = request.getproxies().get(scheme or "", "")
    try:
        proxy_url = parse.urlparse(proxy if "://" in proxy else f"http://{proxy}")
        allow_fake_ip = bool(proxy and proxy_url.hostname and proxy_url.scheme in {"http", "https"} and not request.proxy_bypass(hostname))
    except ValueError:
        allow_fake_ip = False
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return True
    for info in infos:
        address_str = info[4][0]
        try:
            address = ipaddress.ip_address(address_str)
        except ValueError:
            continue
        # A configured HTTP proxy receives the hostname and handles Fake-IP routing.
        if allow_fake_ip and address in PROXY_FAKE_IP_NETWORK:
            continue
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
        ):
            return True
    return False
