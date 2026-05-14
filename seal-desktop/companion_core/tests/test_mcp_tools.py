"""Tests for MCP client and /api/mcp/servers/{id}/tools endpoint."""
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
from companion_core.main import app
from companion_core.mcp_client import McpClient


# ── McpClient unit tests ─────────────────────────────────────────────────────

def _make_proc(responses: list[dict]):
    """Build a fake subprocess with queued JSON-RPC responses."""
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.close = MagicMock()

    # Each readline() returns the next response in the queue
    encoded = [json.dumps(r).encode() + b"\n" for r in responses]
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(side_effect=encoded)
    proc.stdin.drain = AsyncMock()
    proc.wait = AsyncMock(return_value=0)
    return proc


@pytest.mark.asyncio
async def test_mcp_client_list_tools():
    init_resp = {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
    tools_resp = {"jsonrpc": "2.0", "id": 2, "result": {
        "tools": [{"name": "read_file", "description": "Read a file", "inputSchema": {}}]
    }}
    fake_proc = _make_proc([init_resp, tools_resp])

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
        client = McpClient("npx", ["-y", "@mcp/filesystem", "/tmp"])
        await client.start(timeout=5)
        tools = await client.list_tools(timeout=5)
        await client.close()

    assert len(tools) == 1
    assert tools[0]["name"] == "read_file"


@pytest.mark.asyncio
async def test_mcp_client_call_tool():
    init_resp = {"jsonrpc": "2.0", "id": 1, "result": {}}
    call_resp = {"jsonrpc": "2.0", "id": 2, "result": {
        "content": [{"type": "text", "text": "file contents here"}]
    }}
    fake_proc = _make_proc([init_resp, call_resp])

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
        client = McpClient("npx", [])
        await client.start(timeout=5)
        result = await client.call_tool("read_file", {"path": "/tmp/test.txt"}, timeout=5)
        await client.close()

    assert result[0]["text"] == "file contents here"


@pytest.mark.asyncio
async def test_mcp_client_rpc_error_raises():
    init_resp = {"jsonrpc": "2.0", "id": 1, "result": {}}
    err_resp = {"jsonrpc": "2.0", "id": 2, "error": {"code": -32601, "message": "Method not found"}}
    fake_proc = _make_proc([init_resp, err_resp])

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
        client = McpClient("npx", [])
        await client.start(timeout=5)
        with pytest.raises(RuntimeError, match="Method not found"):
            await client.list_tools(timeout=5)


@pytest.mark.asyncio
async def test_mcp_client_not_started_raises():
    client = McpClient("npx", [])
    with pytest.raises(RuntimeError, match="not started"):
        await client._rpc("tools/list", {})


# ── call_claude_with_tools unit test ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_call_claude_with_tools_single_round():
    """Claude returns tool_use → executor called → Claude called again → text response."""
    from companion_core.agent import call_claude_with_tools

    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.id = "tu_001"
    tool_use_block.name = "read_file"
    tool_use_block.input = {"path": "/tmp/test.txt"}

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "The file contains: hello world"

    tool_resp = MagicMock()
    tool_resp.stop_reason = "tool_use"
    tool_resp.content = [tool_use_block]

    final_resp = MagicMock()
    final_resp.stop_reason = "end_turn"
    final_resp.content = [text_block]

    called_tools: list = []

    async def executor(name, inp):
        called_tools.append(name)
        return "hello world"

    with patch("companion_core.agent.AsyncAnthropic") as MockAC:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[tool_resp, final_resp])
        MockAC.return_value = mock_client

        result = await call_claude_with_tools(
            messages=[{"role": "user", "content": "read the file"}],
            api_key="sk-test",
            model="claude-haiku-4-5-20251001",
            system="",
            tools=[{"name": "read_file", "description": "", "input_schema": {}}],
            tool_executor=executor,
        )

    assert result == "The file contains: hello world"
    assert called_tools == ["read_file"]
    assert mock_client.messages.create.call_count == 2


# ── /api/mcp/servers/{id}/tools endpoint ────────────────────────────────────

@pytest.mark.asyncio
async def test_get_tools_for_registered_server():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "fs-tools", "name": "FS", "command": "npx", "args": []
        })

        mock_tools = [{"name": "read_file", "description": "Read", "inputSchema": {}}]
        with patch("companion_core.main.McpClient") as MockCls:
            mock_instance = AsyncMock()
            mock_instance.list_tools = AsyncMock(return_value=mock_tools)
            MockCls.return_value = mock_instance

            r = await client.get("/api/mcp/servers/fs-tools/tools")

    assert r.status_code == 200
    assert r.json()["tools"] == mock_tools
    assert r.json()["server_id"] == "fs-tools"


@pytest.mark.asyncio
async def test_get_tools_server_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/mcp/servers/nonexistent/tools")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_tools_disabled_server_returns_409():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "disabled-srv", "name": "Dis", "command": "echo", "enabled": False
        })
        r = await client.get("/api/mcp/servers/disabled-srv/tools")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_get_tools_server_error_returns_502():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "err-srv", "name": "Err", "command": "false"
        })

        with patch("companion_core.main.McpClient") as MockCls:
            mock_instance = AsyncMock()
            mock_instance.start = AsyncMock(side_effect=Exception("connection refused"))
            MockCls.return_value = mock_instance

            r = await client.get("/api/mcp/servers/err-srv/tools")

    assert r.status_code == 502
    assert "connection refused" in r.json()["detail"]
