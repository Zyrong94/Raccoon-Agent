"""Small official QQ HTTP adapter. Never expose vendor response bodies in errors."""

from __future__ import annotations

import asyncio
import time
import hashlib
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx


class QQApiError(RuntimeError):
    def __init__(self, status: int, code=None) -> None:
        self.status = status
        self.code = code if isinstance(code, int) else None
        super().__init__(f"QQ API HTTP {status}, code={self.code}")


class QQApi:
    def __init__(self, app_id: str, secret: str, client: httpx.AsyncClient) -> None:
        self.app_id, self._secret, self.client = app_id, secret, client
        self._token = ""
        self._expires = 0.0
        self._token_lock = asyncio.Lock()

    async def token(self) -> str:
        async with self._token_lock:
            if time.monotonic() < self._expires:
                return self._token
            response = await self.client.post("https://bots.qq.com/app/getAppAccessToken",
                json={"appId": self.app_id, "clientSecret": self._secret})
            data = self._decode(response)
            if not isinstance(data.get("access_token"), str) or not data["access_token"]:
                raise QQApiError(response.status_code, data.get("code"))
            self._token = data["access_token"]
            self._expires = time.monotonic() + max(1, int(data["expires_in"]) - 60)
            return self._token

    @staticmethod
    def _decode(response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError:
            raise QQApiError(response.status_code) from None
        if not isinstance(data, dict):
            raise QQApiError(response.status_code)
        if response.is_error or data.get("code", 0) not in (0, None):
            raise QQApiError(response.status_code, data.get("code"))
        return data

    async def request(self, method: str, path: str, **kwargs) -> dict:
        for attempt in range(2):
            token = await self.token()
            response = await self.client.request(method, "https://api.sgroup.qq.com" + path,
                headers={"Authorization": "QQBot " + token, "X-Union-Appid": self.app_id}, **kwargs)
            if response.status_code == 401 and attempt == 0:
                self._expires = 0
                continue
            return self._decode(response)
        raise QQApiError(401)

    async def gateway_url(self) -> str:
        data = await self.request("GET", "/gateway/bot")
        url = data.get("url", "")
        if not isinstance(url, str) or not url.startswith("wss://"):
            raise QQApiError(200)
        return url

    async def send_text(self, openid: str, msg_id: str, msg_seq: int, text: str) -> None:
        await self.request("POST", f"/v2/users/{quote(openid, safe='')}/messages",
            json={"content": text, "msg_type": 0, "msg_id": msg_id, "msg_seq": msg_seq})

    async def send_proactive_text(self, openid: str, text: str) -> None:
        """Proactive C2C delivery needs platform entitlement; no expired reply ID."""
        await self.request('POST', f"/v2/users/{quote(openid, safe='')}/messages",
                           json={'content': text, 'msg_type': 0})

    async def send_proactive_file(self, openid: str, path: str | Path) -> None:
        """Upload a local file via QQ's documented binary PUT flow, then send it."""
        path = Path(path)
        name = path.name
        limit = 5 * 1024 * 1024
        if (not name or len(name.encode('utf-8')) > 255
                or any(ord(char) < 32 or ord(char) == 127 or char in '\\/:*?"<>|' for char in name)):
            raise ValueError('Invalid QQ file name')
        if not path.is_file() or not 0 < path.stat().st_size <= limit:
            raise ValueError('QQ file must be nonempty and at most 5 MiB')
        with path.open('rb') as stream:
            content = stream.read(limit + 1)
        if not 0 < len(content) <= limit:
            raise ValueError('QQ file must be nonempty and at most 5 MiB')
        target = f"/v2/users/{quote(openid, safe='')}"
        try:
            # Direct file_data JSON encoding is unspecified; this flow uses documented raw PUT bytes.
            prepared = await self.request('POST', target + '/upload_prepare', json={
                'file_type': 4, 'file_size': str(len(content)), 'file_name': name,
                'md5': hashlib.md5(content).hexdigest(),
                'sha1': hashlib.sha1(content).hexdigest(),
                'md5_10m': hashlib.md5(content[:10002432]).hexdigest()})
            upload_id, parts = prepared.get('upload_id'), prepared.get('parts')
            if not isinstance(upload_id, str) or not upload_id or not isinstance(parts, list) or not parts:
                raise QQApiError(200)
            first_index = parts[0].get('index') if isinstance(parts[0], dict) else None
            # Live QQ responses use 1-based indexes; the public schema documents 0-based indexes.
            if type(first_index) is not int or first_index not in (0, 1):
                raise QQApiError(200)
            uploads = []
            offset = 0
            for index, part in enumerate(parts):
                if (not isinstance(part, dict) or type(part.get('index')) is not int
                        or part['index'] != first_index + index):
                    raise QQApiError(200)
                try:
                    size = int(part['block_size'])
                    url = part['presigned_url']
                    if not isinstance(url, str):
                        raise QQApiError(200)
                    parsed = urlsplit(url)
                    valid_url = (parsed.scheme == 'https'
                        and (parsed.hostname or '').endswith('.myqcloud.com')
                        and not parsed.username and not parsed.password and parsed.port in (None, 443))
                except (KeyError, TypeError, ValueError):
                    raise QQApiError(200) from None
                if not valid_url or not 0 < size <= len(content) - offset:
                    raise QQApiError(200)
                uploads.append((part['index'], url, content[offset:offset + size]))
                offset += size
            if offset != len(content):
                raise QQApiError(200)
            for index, url, block in uploads:
                response = await self.client.put(url, content=block,
                    headers={'Content-Type': 'application/octet-stream'}, follow_redirects=False)
                if not response.is_success:
                    raise QQApiError(response.status_code)
                await self.request('POST', target + '/upload_part_finish', json={
                    'upload_id': upload_id, 'part_index': index, 'block_size': str(len(block)),
                    'md5': hashlib.md5(block).hexdigest()})
            uploaded = await self.request('POST', target + '/files', json={
                'upload_id': upload_id, 'file_type': 4, 'file_name': name, 'srv_send_msg': False})
            file_info = uploaded.get('file_info')
            if not isinstance(file_info, str) or not file_info:
                raise QQApiError(200)
            await self.request('POST', target + '/messages',
                json={'msg_type': 7, 'media': {'file_info': file_info}})
        except httpx.RequestError:
            raise QQApiError(0) from None

    async def send_message(self, openid: str, msg_id: str, msg_seq: int, payload: dict) -> None:
        if payload.get('version', 1) != 1 or payload.get('msg_type') not in (0, 2, 7):
            raise ValueError('Unsupported QQ outbox payload')
        fields = {key: value for key, value in payload.items()
                  if key in {'msg_type', 'content', 'markdown', 'keyboard', 'media'}}
        await self.request('POST', f"/v2/users/{quote(openid, safe='')}/messages",
                           json={**fields, 'msg_id': msg_id, 'msg_seq': msg_seq})

    async def upload_image(self, png: bytes) -> dict:
        if not png.startswith(b'\x89PNG\r\n\x1a\n') or len(png) > 10 * 1024 * 1024:
            raise ValueError('Preview must be a PNG smaller than 10MB')
        digest = hashlib.md5(png).hexdigest()
        prepared = await self.request('POST', '/v2/media-images/prepare', json={
            'file_name': 'preview.png', 'file_size': len(png), 'content_type': 'image/png',
            'md5': digest, 'sha1': hashlib.sha1(png).hexdigest()})
        url = prepared.get('presigned_url', '')
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.myqcloud.com')
                or parsed.username or parsed.password or parsed.port not in (None,443)):
            raise ValueError('Unexpected QQ upload destination')
        uploaded = await self.client.put(url, content=png, headers={'Content-Type':'image/png'})
        if uploaded.is_error:
            raise QQApiError(uploaded.status_code)
        result = await self.request('POST', '/v2/media-images/complete', json={
            'upload_id': prepared['upload_id'], 'md5': uploaded.headers.get('etag', digest).strip('"')})
        if not isinstance(result.get('url'), str) or not result['url'].startswith('qqbot-media://image/v1/'):
            raise ValueError('QQ upload did not return an image reference')
        return result

    async def send_group_message(self, group_openid: str, msg_id: str, msg_seq: int, payload: dict) -> None:
        if payload.get('version', 1) != 1 or payload.get('msg_type') not in (0,2,7):
            raise ValueError('Unsupported QQ group payload')
        fields = deepcopy({key:value for key,value in payload.items()
                           if key in {'msg_type','content','markdown','keyboard','media'}})
        for row in fields.get('keyboard',{}).get('content',{}).get('rows',[]):
            for button in row.get('buttons',[]):
                button.get('action',{}).pop('enter',None)
        await self.request('POST',f"/v2/groups/{quote(group_openid,safe='')}/messages",
                           json={**fields,'msg_id':msg_id,'msg_seq':msg_seq})
