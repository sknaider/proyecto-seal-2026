"""The recovered runtime uses MCP 2.x; preserve HTTP headers and cleanup."""
import asyncio
from contextlib import asynccontextmanager

import mcp_gateway


def test_http_transport_preserves_headers_and_closes(monkeypatch):
    if not hasattr(mcp_gateway, "streamable_http_client"):
        # 1.x uses the SDK's original transport directly.
        assert callable(mcp_gateway.streamablehttp_client)
        return
    events = []
    @asynccontextmanager
    async def client(*, headers):
        assert headers == {"Authorization": "Bearer synthetic-fixture"}
        events.append("client-open")
        try:
            yield "fake-http-client"
        finally:
            events.append("client-close")
    @asynccontextmanager
    async def transport(url, *, http_client):
        assert url == "http://127.0.0.1:1/mcp"
        assert http_client == "fake-http-client"
        events.append("transport-open")
        try:
            yield ("read", "write")
        finally:
            events.append("transport-close")
    monkeypatch.setattr(mcp_gateway, "create_mcp_http_client", client)
    monkeypatch.setattr(mcp_gateway, "streamable_http_client", transport)
    async def run():
        async with mcp_gateway.streamablehttp_client("http://127.0.0.1:1/mcp", headers={"Authorization": "Bearer synthetic-fixture"}) as streams:
            assert streams == ("read", "write")
    asyncio.run(run())
    assert events == ["client-open", "transport-open", "transport-close", "client-close"]
