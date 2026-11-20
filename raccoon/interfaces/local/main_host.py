"""Authenticated loopback transport; capability never enters a browser snapshot."""
import asyncio
from contextlib import suppress
import json
from pathlib import Path
import secrets
import time
from raccoon.modules.events import EventError
from raccoon.application.conversations.main_host import MainHostConflict, MainHostUnavailable

class LocalMainEndpoint:
    def __init__(self, data_root, *, descriptor_name='main_host.json'):
        self.descriptor = Path(data_root) / 'channels' / descriptor_name
        self.token = secrets.token_urlsafe(32)
        self.server = None
        self.host = None

    async def start(self, host):
        self.host = host
        self.descriptor.parent.mkdir(parents=True, exist_ok=True)
        self.server = await asyncio.start_server(self._connection, '127.0.0.1', 0, limit=2_000_000)
        port = self.server.sockets[0].getsockname()[1]
        temporary = self.descriptor.with_suffix('.tmp')
        temporary.write_text(json.dumps({'port': port, 'token': self.token, 'generation': host.generation}), encoding='utf-8')
        temporary.replace(self.descriptor)
        if self.descriptor.name == 'main_host.json':
            (self.descriptor.parent / 'main_handoff.json').unlink(missing_ok=True)

    def reserve_handoff(self):
        (self.descriptor.parent / 'main_handoff.json').write_text(json.dumps({'expires': time.time() + 10}), encoding='utf-8')

    async def stop(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    def cleanup(self, *, generation, handoff):
        with suppress(OSError, ValueError):
            if json.loads(self.descriptor.read_text(encoding='utf-8')).get('generation') == generation:
                self.descriptor.unlink()
            if handoff:
                self.reserve_handoff()

    async def _connection(self, reader, writer):
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5)
            request = json.loads(raw)
            if not secrets.compare_digest(str(request.get('token', '')), self.token):
                result = {'error': '本地宿主认证失败', 'code': 403}
            else:
                try:
                    value = await self.host.dispatch(request['operation'], request.get('body'))
                    result = {'value': value}
                except MainHostUnavailable as error:
                    result = {'error': str(error), 'code': 503}
                except MainHostConflict as error:
                    result = {'error': str(error), 'code': 409}
                except (ValueError, KeyError, EventError) as error:
                    result = {'error': str(error), 'code': 400}
            writer.write(json.dumps(result, ensure_ascii=False).encode('utf-8') + b'\n')
            await writer.drain()
        except (OSError, ValueError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

async def call_main_host(data_root, operation, body=None, *, descriptor_name='main_host.json'):
    descriptor = Path(data_root) / 'channels' / descriptor_name
    writer = None
    try:
        info = json.loads(descriptor.read_text(encoding='utf-8'))
        port, token = info['port'], info['token']
        if not isinstance(port, int) or not 1 <= port <= 65535 or not isinstance(token, str):
            raise ValueError('invalid host descriptor')
        reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', port, limit=20_000_000), 3)
        writer.write(json.dumps({'token': token, 'operation': operation, 'body': body}, ensure_ascii=False).encode('utf-8') + b'\n')
        await writer.drain()
        result = json.loads(await asyncio.wait_for(reader.readline(), 30))
        if 'error' in result:
            if result.get('code') == 409:
                raise MainHostConflict(result['error'])
            if result.get('code') == 503:
                raise MainHostUnavailable(result['error'])
            raise ValueError(result['error'])
        return result['value']
    except (OSError, KeyError, json.JSONDecodeError, asyncio.TimeoutError) as error:
        raise MainHostUnavailable('主会话宿主暂不可用，请重新连接。提交结果未知时请先检查历史，勿重复发送。') from error
    finally:
        if writer is not None:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
