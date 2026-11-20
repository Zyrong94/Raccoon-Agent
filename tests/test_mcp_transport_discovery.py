import asyncio
from types import SimpleNamespace

import pytest

from raccoon.integrations.mcp.transport import _SdkMcpTransport


class PaginatedSession:
    def __init__(self, repeated_cursor=False):
        self.cursors = []
        self.repeated_cursor = repeated_cursor

    async def list_tools(self, *, params=None):
        cursor = params.cursor if params is not None else None
        self.cursors.append(cursor)
        name = "first" if cursor is None else "second"
        next_cursor = "page-2" if cursor is None or self.repeated_cursor else None
        return SimpleNamespace(
            tools=[SimpleNamespace(name=name, description=name, inputSchema={"type": "object"})],
            nextCursor=next_cursor,
        )


def test_discovery_reads_every_page_before_publishing_catalog():
    transport = _SdkMcpTransport()
    session = PaginatedSession()
    transport._session = session
    discovered = asyncio.run(transport.list_tools())
    assert [tool.name for tool in discovered] == ["first", "second"]
    assert session.cursors == [None, "page-2"]


def test_discovery_rejects_repeated_cursor_instead_of_looping_forever():
    transport = _SdkMcpTransport()
    session = PaginatedSession(repeated_cursor=True)
    transport._session = session
    with pytest.raises(RuntimeError, match="repeated cursor"):
        asyncio.run(transport.list_tools())
    assert session.cursors == [None, "page-2"]
