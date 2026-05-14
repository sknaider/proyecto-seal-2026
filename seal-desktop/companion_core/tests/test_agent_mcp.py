"""Tests for agent harness (Fase C) and MCP facility (Fase D)."""
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
from companion_core.main import app


# ── Agent / Chat ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_without_api_key_returns_stub_or_ollama():
    """No api_key + no Ollama → stub message."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("companion_core.agent.call_ollama", new=AsyncMock(return_value=None)):
            r = await client.post("/api/chat", json={
                "thread_id": "agent-test-1",
                "content": "hello"
            })
    assert r.status_code == 200
    data = r.json()
    assert "reply" in data
    assert "thread_id" in data


@pytest.mark.asyncio
async def test_chat_uses_anthropic_when_api_key_set():
    """With api_key in config, Claude is called."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Set api_key via PATCH config
        await client.patch("/api/companion/config", json={"api_key": "sk-test-key"})

        mock_content = MagicMock()
        mock_content.text = "Hello from Claude!"
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        with patch("companion_core.agent.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_client

            r = await client.post("/api/chat", json={
                "thread_id": "agent-test-2",
                "content": "test message"
            })

    assert r.status_code == 200
    assert r.json()["reply"] == "Hello from Claude!"


@pytest.mark.asyncio
async def test_chat_falls_back_to_ollama_when_claude_fails():
    """Claude fails → Ollama fallback is used."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch("/api/companion/config", json={"api_key": "sk-bad-key"})

        with patch("companion_core.agent.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(side_effect=Exception("auth error"))
            mock_cls.return_value = mock_client

            with patch("companion_core.agent.call_ollama", new=AsyncMock(return_value="Ollama reply")):
                r = await client.post("/api/chat", json={
                    "thread_id": "agent-test-3",
                    "content": "fallback test"
                })

    assert r.status_code == 200
    assert r.json()["reply"] == "Ollama reply"


@pytest.mark.asyncio
async def test_chat_passes_tools_to_get_reply_when_cache_populated():
    """When MCP tools are cached, they are passed to get_reply."""
    passed_tools: list = []

    async def fake_get_reply(history, content, api_key, model, user_name="",
                             tools=None, tool_executor=None, system=None):
        if tools:
            passed_tools.extend(tools)
        return "Done with tools"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "fs-cached", "name": "FS", "command": "npx", "args": []
        })
        from companion_core.db import get_db
        db = get_db()
        cache = json.dumps([{"name": "read_file", "description": "Read",
                             "inputSchema": {"type": "object", "properties": {}}}])
        await db.execute("UPDATE mcp_servers SET tools_cache=? WHERE id='fs-cached'", (cache,))
        await db.commit()

        with patch("companion_core.main.get_reply", new=fake_get_reply):
            r = await client.post("/api/chat", json={
                "thread_id": "tool-thread2", "content": "read my file"
            })

    assert r.status_code == 200
    assert r.json()["reply"] == "Done with tools"
    assert any(t["name"] == "read_file" for t in passed_tools)


@pytest.mark.asyncio
async def test_chat_tool_executor_uses_mcp_client():
    """_tool_executor in /api/chat spins up McpClient for the right server."""
    captured_executor: list = []

    async def fake_get_reply(history, content, api_key, model, user_name="",
                             tools=None, tool_executor=None, system=None):
        captured_executor.append(tool_executor)
        return "ok"

    mock_mcp = AsyncMock()
    mock_mcp.call_tool = AsyncMock(return_value=[{"type": "text", "text": "result"}])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "fs-exec", "name": "FS", "command": "npx", "args": []
        })
        from companion_core.db import get_db
        db = get_db()
        cache = json.dumps([{"name": "list_dir", "description": "List",
                             "inputSchema": {"type": "object", "properties": {}}}])
        await db.execute("UPDATE mcp_servers SET tools_cache=? WHERE id='fs-exec'", (cache,))
        await db.commit()

        with patch("companion_core.main.get_reply", new=fake_get_reply):
            with patch("companion_core.main.McpClient", return_value=mock_mcp):
                r = await client.post("/api/chat", json={
                    "thread_id": "exec-thread", "content": "list /tmp"
                })
                # Call the executor to verify it uses McpClient
                if captured_executor and captured_executor[0]:
                    await captured_executor[0]("list_dir", {"path": "/tmp"})

    assert r.status_code == 200
    mock_mcp.call_tool.assert_called_once_with("list_dir", {"path": "/tmp"}, timeout=30)


@pytest.mark.asyncio
async def test_chat_includes_thread_history():
    """Second message in thread includes prior context in history."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        captured_messages: list = []

        async def fake_get_reply(history, content, api_key, model, user_name="",
                                 tools=None, tool_executor=None, system=None):
            captured_messages.extend(history)
            return "reply"

        with patch("companion_core.main.get_reply", new=fake_get_reply):
            await client.post("/api/chat", json={
                "thread_id": "history-thread",
                "content": "first message"
            })
            await client.post("/api/chat", json={
                "thread_id": "history-thread",
                "content": "second message"
            })

    # After second call, history should include the first user+assistant turn
    assert any(m["role"] == "user" and "first message" in m["content"] for m in captured_messages)


# ── MCP Servers ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_mcp_servers_empty():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/mcp/servers")
    assert r.status_code == 200
    assert r.json()["servers"] == []


@pytest.mark.asyncio
async def test_add_mcp_server():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/mcp/servers", json={
            "id": "filesystem",
            "name": "Filesystem MCP",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["id"] == "filesystem"


@pytest.mark.asyncio
async def test_list_mcp_servers_after_add():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "fs2", "name": "FS2", "command": "node", "args": ["server.js"]
        })
        r = await client.get("/api/mcp/servers")
    assert r.status_code == 200
    servers = r.json()["servers"]
    assert len(servers) == 1
    assert servers[0]["id"] == "fs2"
    assert servers[0]["args"] == ["server.js"]
    assert servers[0]["enabled"] is True


@pytest.mark.asyncio
async def test_add_mcp_server_duplicate_returns_409():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"id": "dup", "name": "Dup", "command": "python3"}
        await client.post("/api/mcp/servers", json=payload)
        r = await client.post("/api/mcp/servers", json=payload)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_add_mcp_server_missing_command_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/mcp/servers", json={
            "id": "bad", "name": "Bad", "command": ""
        })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_mcp_server():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "to-delete", "name": "Del", "command": "echo"
        })
        r = await client.delete("/api/mcp/servers/to-delete")
        assert r.status_code == 200
        assert r.json()["ok"] is True

        r2 = await client.get("/api/mcp/servers")
        assert r2.json()["servers"] == []


@pytest.mark.asyncio
async def test_delete_mcp_server_not_found_returns_404():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/mcp/servers/nonexistent")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_toggle_mcp_server():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "toggle-me", "name": "Toggle", "command": "echo"
        })
        r = await client.patch("/api/mcp/servers/toggle-me", params={"enabled": "false"})
        assert r.status_code == 200
        assert r.json()["ok"] is True

        r2 = await client.get("/api/mcp/servers")
        srv = r2.json()["servers"][0]
        assert srv["enabled"] is False
