"""QQ WebSocket ingress with heartbeat, resume and durable checkpoints."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import logging
import time

from websockets.asyncio.client import connect

from raccoon.interfaces.qq.api import QQApi
from raccoon.interfaces.qq.state import QQState

log = logging.getLogger(__name__)


class QQGateway:
    def __init__(self, api: QQApi, state: QQState) -> None:
        self.api, self.state = api, state
        self.online = asyncio.Event()

    def admit(self, packet: dict) -> None:
        self.state.admit(packet)

    async def run(self) -> None:
        delay = 1
        while True:
            try:
                await self.connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Exception text can contain a vendor response, URL or credential.
                log.warning("QQ gateway disconnected (%s); reconnecting", type(error).__name__)
                delay = min(delay * 2, 30)
            else:
                delay = 1
            finally:
                self.online.clear()
                self.state.set("online", False)
            await asyncio.sleep(delay)

    async def abort(self, ws) -> None:
        """End a half-open socket: a dead peer never raises on send, only the ACK proves life."""
        transport = getattr(ws, "transport", None)
        if transport is not None:
            with suppress(Exception):
                transport.abort()
        with suppress(Exception):
            await asyncio.wait_for(ws.close(), 5)

    async def connect_once(self) -> None:
        url = await self.api.gateway_url()
        async with connect(url, ping_interval=None, open_timeout=20, max_size=4 * 1024 * 1024) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 20))
            if hello.get("op") != 10:
                raise ValueError("QQ gateway did not send Hello")
            interval = max(1, float(hello["d"]["heartbeat_interval"]) / 1000)
            session_id = self.state.get("gateway_session")
            token = "QQBot " + await self.api.token()
            if session_id:
                auth = {"op": 6, "d": {"token": token, "session_id": session_id,
                                         "seq": self.state.get("gateway_seq", 0)}}
            else:
                auth = {"op": 2, "d": {"token": token, "intents": 1 << 25, "shard": [0, 1]}}
            await ws.send(json.dumps(auth))
            acknowledged = True
            last_packet = time.monotonic()

            async def heartbeat():
                nonlocal acknowledged
                while True:
                    await asyncio.sleep(interval)
                    # A proxied connection can look open forever while nothing arrives.
                    if not acknowledged or time.monotonic() - last_packet > interval * 2.5:
                        log.warning("QQ gateway silent; reconnecting")
                        await self.abort(ws)
                        return
                    acknowledged = False
                    try:
                        await ws.send(json.dumps({"op": 1, "d": self.state.get("gateway_seq")}))
                    except Exception:
                        await self.abort(ws)
                        return

            pulse = asyncio.create_task(heartbeat())
            try:
                async for message in ws:
                    last_packet = time.monotonic()
                    packet = json.loads(message)
                    op = packet.get("op")
                    if op == 11:
                        acknowledged = True
                    elif op == 1:
                        await ws.send(json.dumps({"op": 1, "d": self.state.get("gateway_seq")}))
                    elif op == 9:
                        self.state.set("gateway_session", None)
                        self.state.set("gateway_seq", None)
                        return
                    elif op == 7:
                        return
                    elif op == 0:
                        self.admit(packet)
                        if packet.get("t") in ("READY", "RESUMED"):
                            self.online.set()
                            self.state.set("online", True)
                            log.info("QQ gateway online; waiting for private and group mentions")
            finally:
                pulse.cancel()
                with suppress(asyncio.CancelledError):
                    await pulse
