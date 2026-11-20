"""arXiv official Atom retrieval; no model-facing XML or HTML extraction."""

from __future__ import annotations

from datetime import date, datetime, time as day_time, timedelta, timezone
import re
import threading
import time
from urllib import error, parse, request
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from raccoon.modules.tools.fetch import _SafeRedirectHandler, _resolves_to_private_ip

ENDPOINT = "https://export.arxiv.org/api/query"
PAGE_SIZE = 50
MAX_PAGES = 3
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
NS = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/",
      "arxiv": "http://arxiv.org/schemas/atom"}
_request_lock = threading.Lock()
_last_request = 0.0


def _download(query: str, offset: int, size: int) -> bytes:
    global _last_request
    params = parse.urlencode({"search_query": query, "start": offset, "max_results": size,
                              "sortBy": "lastUpdatedDate", "sortOrder": "descending"})
    if _resolves_to_private_ip("export.arxiv.org", scheme="https"):
        raise ValueError("arXiv 域名无法解析或解析到内网地址")
    req = request.Request(f"{ENDPOINT}?{params}", headers={"User-Agent": "Raccoon/0.1", "Accept": "application/atom+xml"})
    # Serialize this process's requests, including parallel/background tool calls.
    with _request_lock:
        time.sleep(max(0, 3 - (time.monotonic() - _last_request)))
        try:
            with request.build_opener(_SafeRedirectHandler).open(req, timeout=30) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        finally:
            _last_request = time.monotonic()
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("arXiv 响应超出解析上限；未截断后冒充完整结果")
    return body


def _parse_feed(body: bytes) -> tuple[list[dict], int]:
    root = ET.fromstring(body)
    if root.tag != f"{{{NS['a']}}}feed":
        raise ValueError("arXiv 未返回 Atom feed")
    total = int(root.findtext("o:totalResults", namespaces=NS) or "-1")
    if total < 0:
        raise ValueError("arXiv 返回错误或缺少结果总数")
    papers = []
    for entry in root.findall("a:entry", NS):
        def text(path):
            return " ".join((entry.findtext(path, namespaces=NS) or "").split())
        identifier = text("a:id").split("/abs/")[-1]
        match = re.fullmatch(r"(\d{4}\.\d{4,5}|[a-zA-Z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)", identifier)
        if not match:
            raise ValueError("arXiv 条目缺少有效版本编号")
        updated = datetime.fromisoformat(text("a:updated").replace("Z", "+00:00"))
        if updated.tzinfo is None:
            raise ValueError("arXiv 更新时间缺少时区")
        title, abstract = text("a:title"), text("a:summary")
        if not title or not abstract:
            raise ValueError("arXiv 条目缺少标题或摘要")
        base, version = match.groups()
        papers.append({"source": "arxiv", "id": base, "title": title,
                       "authors": [" ".join((node.text or "").split()) for node in entry.findall("a:author/a:name", NS)],
                       "abstract": abstract, "date": updated.astimezone(ZoneInfo("America/New_York")).date().isoformat(),
                       "date_type": "latest_submission", "updated_at": updated.isoformat(),
                       "version": version, "doi": text("arxiv:doi") or None,
                       "paper_url": f"https://arxiv.org/abs/{base}", "pdf_url": f"https://arxiv.org/pdf/{base}"})
    return papers, total


def search_arxiv(query: str, date_from: date, date_to: date, limit: int, offset: int) -> dict:
    papers, seen = [], set()
    position, scanned, total = offset, 0, None
    upper = datetime.combine(date_to + timedelta(days=1), day_time(), ZoneInfo("America/New_York"))
    upper_utc = upper.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
    # First submission cannot follow the current version. Only an upper bound
    # is safe here: a lower bound would lose older papers revised in the window.
    api_query = f"({query}) AND submittedDate:[199101010000 TO {upper_utc}]"

    def result(reason, next_offset, failure=None):
        return {"source": "arxiv", "query": query, "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(), "date_type": "latest_submission", "timezone": "America/New_York",
                "papers": papers, "scanned": scanned, "total_query_results": total,
                "next_offset": next_offset, "stop_reason": reason, "error": failure}

    for _ in range(MAX_PAGES):
        try:
            entries, total = _parse_feed(_download(api_query, position, PAGE_SIZE))
            if not entries and position < total:
                raise ValueError("API 返回空批次但仍有结果，不能判定检索结束")
        except (OSError, ValueError, ET.ParseError) as exc:
            reason = "rate_limited" if isinstance(exc, error.HTTPError) and exc.code == 429 else "request_failed"
            return result(reason, position, str(exc))
        for paper in entries:
            position += 1
            scanned += 1
            paper_date = date.fromisoformat(paper["date"])
            if paper_date < date_from:
                return result("date_range_exhausted", None)
            if paper["id"] in seen:
                continue
            seen.add(paper["id"])
            if paper_date > date_to:
                continue
            papers.append(paper)
            if len(papers) >= limit:
                return result("limit" if position < total else "exhausted", position if position < total else None)
        if position >= total:
            return result("exhausted", None)
    return result("scan_budget", position)
