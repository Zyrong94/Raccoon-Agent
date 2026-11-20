"""Only an authenticated loopback client may dispatch a main-host request."""

import asyncio
import json
from types import SimpleNamespace

from raccoon.interfaces.local.main_host import LocalMainEndpoint, call_main_host


def test_main_endpoint_rejects_an_invalid_capability_before_dispatch(tmp_path):
    async def scenario():
        requests = []

        async def dispatch(operation, body):
            requests.append((operation, body))
            return {"session_id": "main"}

        endpoint = LocalMainEndpoint(tmp_path)
        await endpoint.start(SimpleNamespace(generation="active", dispatch=dispatch))
        writer = None
        try:
            state = await call_main_host(tmp_path, "snapshot")
            assert state == {"session_id": "main"}

            port = endpoint.server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b'{"token":"wrong","operation":"snapshot"}\n')
            await writer.drain()
            result = json.loads(await asyncio.wait_for(reader.readline(), timeout=5))
            assert result["code"] == 403
            assert endpoint.token not in json.dumps(result)
            assert requests == [("snapshot", None)]
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            await endpoint.stop()
            endpoint.cleanup(generation="active", handoff=False)

    asyncio.run(scenario())
