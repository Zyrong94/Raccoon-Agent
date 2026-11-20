"""REST 搜索引擎共用的请求与响应校验。"""

from __future__ import annotations

import asyncio
from urllib import parse, request

from raccoon.utils.json_utils import dumps_json, loads_json


async def post_json(url: str, body: dict, headers: dict[str, str], timeout: int, name: str) -> dict:
    req = request.Request(
        url,
        data=dumps_json(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "Raccoon/0.1", **headers},
    )

    def read() -> bytes:
        with request.urlopen(req, timeout=timeout) as response:
            return response.read()

    raw = await asyncio.to_thread(read)
    try:
        payload = loads_json(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"{name} 搜索响应格式无效") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{name} 搜索响应格式无效")
    return payload


def valid_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parts = parse.urlsplit(url)
        return parts.scheme.lower() in ("http", "https") and bool(parts.hostname)
    except ValueError:
        return False
