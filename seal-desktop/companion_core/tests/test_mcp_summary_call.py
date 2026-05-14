"""Tests for MCP summary dashboard and direct tool call (Live Debugger)."""
import json
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from companion_core.main import app


# ── /api/mcp/summary ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_summary_empty():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/mcp/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["total_servers"] == 0
    assert data["enabled_servers"] == 0
    assert data["total_cached_tools"] == 0
    assert data["servers"] == []


@pytest.mark.asyncio
async def test_summary_counts_correctly():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "s1", "name": "S1", "command": "npx", "args": [], "enabled": True
        })
        await client.post("/api/mcp/servers", json={
            "id": "s2", "name": "S2", "command": "npx", "args": [], "enabled": False
        })
        # Set tools_cache for s1
        from companion_core.db import get_db
        db = get_db()
        cache = json.dumps([{"name": "t1"}, {"name": "t2"}, {"name": "t3"}])
        await db.execute("UPDATE mcp_servers SET tools_cache=? WHERE id='s1'", (cache,))
        await db.commit()

        r = await client.get("/api/mcp/summary")

    data = r.json()
    assert data["total_servers"] == 2
    assert data["enabled_servers"] == 1
    assert data["total_cached_tools"] == 3
    s1 = next(s for s in data["servers"] if s["id"] == "s1")
    assert s1["tool_count"] == 3
    s2 = next(s for s in data["servers"] if s["id"] == "s2")
    assert s2["tool_count"] == 0
    assert s2["enabled"] is False


# ── /api/mcp/servers/{id}/call ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_direct_call_tool():
    mock_mcp = AsyncMock()
    mock_mcp.call_tool = AsyncMock(return_value=[{"type": "text", "text": "result here"}])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "call-srv", "name": "CallSrv", "command": "npx", "args": []
        })
        with patch("companion_core.main.McpClient", return_value=mock_mcp):
            r = await client.post("/api/mcp/servers/call-srv/call", json={
                "tool_name": "read_file",
                "arguments": {"path": "/tmp/test.txt"}
            })

    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["tool"] == "read_file"
    assert data["result"][0]["text"] == "result here"
    mock_mcp.call_tool.assert_called_once_with(
        "read_file", {"path": "/tmp/test.txt"}, timeout=30
    )


@pytest.mark.asyncio
async def test_direct_call_tool_not_found_server():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/mcp/servers/ghost/call", json={
            "tool_name": "read_file", "arguments": {}
        })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_direct_call_disabled_server_returns_409():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "disabled-call", "name": "Dis", "command": "echo", "enabled": False
        })
        r = await client.post("/api/mcp/servers/disabled-call/call", json={
            "tool_name": "hello", "arguments": {}
        })
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_direct_call_empty_tool_name_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "bad-tool", "name": "Bad", "command": "echo"
        })
        r = await client.post("/api/mcp/servers/bad-tool/call", json={
            "tool_name": "", "arguments": {}
        })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_direct_call_server_error_returns_502():
    mock_mcp = AsyncMock()
    mock_mcp.start = AsyncMock(side_effect=Exception("connection refused"))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "crash-srv", "name": "Crash", "command": "npx", "args": []
        })
        with patch("companion_core.main.McpClient", return_value=mock_mcp):
            r = await client.post("/api/mcp/servers/crash-srv/call", json={
                "tool_name": "read_file", "arguments": {}
            })

    assert r.status_code == 502
    assert "connection refused" in r.json()["detail"]
