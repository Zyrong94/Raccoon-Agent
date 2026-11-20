"""Download QQ image attachments into durable, session-owned references."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import httpx
from PIL import Image

from raccoon.modules.sessions.user_attachments import MAX_ATTACHMENTS_PER_MESSAGE, MAX_IMAGE_BYTES, UserAttachment, prepare_queued_attachments, validate_tool_image


QQ_IMAGE_HOSTS = frozenset({
    'multimedia.nt.qq.com.cn', 'gchat.qpic.cn', 'c2cpicdw.qpic.cn',
    'offpic.qpic.cn', 'thirdqq.qlogo.cn',
})


def _image_url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 8192 or any(ord(c) <= 32 for c in value):
        raise ValueError('图片地址无效。')
    url = 'https:' + value if value.startswith('//') else value
    if '://' not in url:
        url = 'https://' + url
    try:
        parts = urlsplit(url)
        valid = (parts.scheme == 'https' and parts.hostname in QQ_IMAGE_HOSTS
                 and parts.port in (None, 443) and not parts.username and not parts.password
                 and not parts.fragment and '\\' not in url)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('图片地址不是受支持的 QQ HTTPS 图片来源。')
    return url


def _stage_image(raw: bytes, declared_type: str, path: Path) -> UserAttachment:
    formats = {'PNG': ('image/png', '.png'), 'JPEG': ('image/jpeg', '.jpg'),
               'GIF': ('image/gif', '.gif'), 'WEBP': ('image/webp', '.webp')}
    try:
        with Image.open(BytesIO(raw)) as image:
            media_type, suffix = formats[image.format]
            if image.width * image.height > 40_000_000:
                raise ValueError('图片尺寸过大。')
        if declared_type == 'image/jpg':
            declared_type = 'image/jpeg'
        if declared_type != media_type:
            raise ValueError('图片内容与声明格式不一致。')
        validate_tool_image(raw, media_type)
    except (OSError, KeyError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise ValueError('图片格式不支持、文件损坏或尺寸过大，请发送 PNG、JPG、GIF 或 WebP。') from None
    path = path.with_suffix(suffix)
    path.write_bytes(raw)
    return UserAttachment('image', path, path.name, media_type, len(raw), 'qq')


class QQImageInput:
    def __init__(self, client: httpx.AsyncClient, *, store_root: Path, session_id: str):
        self.client, self.store_root, self.session_id = client, store_root, session_id

    async def prepare(self, attachments: list[dict]):
        if (not isinstance(attachments, list) or not 1 <= len(attachments) <= MAX_ATTACHMENTS_PER_MESSAGE
                or any(not isinstance(item, dict) for item in attachments)):
            raise ValueError('每条消息需要 1～16 张图片。')
        sources = []
        for item in attachments:
            media_type = item.get('content_type')
            if not isinstance(media_type, str) or not media_type.startswith('image/'):
                raise ValueError('QQ 当前只接收图片附件，请发送 PNG、JPG、GIF 或 WebP。')
            sources.append((_image_url(item.get('url')), media_type))
        try:
            async with asyncio.timeout(60):
                with TemporaryDirectory(prefix='raccoon-qq-image-') as staging:
                    images = []
                    for index, (url, media_type) in enumerate(sources, 1):
                        raw = await self._download(url)
                        images.append(await asyncio.to_thread(
                            _stage_image, raw, media_type, Path(staging) / f'image-{index}',
                        ))
                    return await asyncio.to_thread(
                        prepare_queued_attachments, images,
                        store_root=self.store_root, session_id=self.session_id,
                    )
        except (httpx.HTTPError, TimeoutError):
            raise ValueError('图片下载失败或超时，请重新 @机器人 并发送图片。') from None

    async def _download(self, url: str) -> bytes:
        async with self.client.stream('GET', url, timeout=20, follow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError(f'图片下载失败（HTTP {response.status_code}），请重新发送图片。')
            length = response.headers.get('content-length')
            if length and (not length.isdecimal() or int(length) > MAX_IMAGE_BYTES):
                raise ValueError('图片大小超过 20 MiB 限制。')
            content = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                if len(content) + len(chunk) > MAX_IMAGE_BYTES:
                    raise ValueError('图片大小超过 20 MiB 限制。')
                content.extend(chunk)
            return bytes(content)
