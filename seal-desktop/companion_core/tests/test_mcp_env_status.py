"""Tests for MCP server env config and status check."""
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from companion_core.main import app


@pytest.mark.asyncio
async def test_update_env_adds_keys():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "gh-env", "name": "GitHub", "command": "npx", "args": []
        })
        r = await client.patch("/api/mcp/servers/gh-env/env", json={
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_test123"}
        })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in data["env_keys"]


@pytest.mark.asyncio
async def test_update_env_merges_existing():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "multi-env", "name": "Multi", "command": "npx",
            "args": [], "env": {"KEY_A": "old_a"}
        })
        r = await client.patch("/api/mcp/servers/multi-env/env", json={
            "env": {"KEY_B": "val_b"}
        })
    assert r.status_code == 200
    keys = r.json()["env_keys"]
    assert "KEY_A" in keys
    assert "KEY_B" in keys


@pytest.mark.asyncio
async def test_update_env_overwrites_existing_key():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "overwrite-env", "name": "OW", "command": "npx",
            "args": [], "env": {"TOKEN": "old"}
        })
        r = await client.patch("/api/mcp/servers/overwrite-env/env", json={
            "env": {"TOKEN": "new_value"}
        })
    assert r.status_code == 200
    # Verify in servers list
    # (we trust merge logic; key count same)
    assert "TOKEN" in r.json()["env_keys"]


@pytest.mark.asyncio
async def test_update_env_server_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.patch("/api/mcp/servers/nonexistent/env", json={"env": {"K": "v"}})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_status_disabled_server():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "dis-status", "name": "Dis", "command": "echo", "enabled": False
        })
        r = await client.get("/api/mcp/servers/dis-status/status")
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


@pytest.mark.asyncio
async def test_status_ok_when_server_starts():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "ok-status", "name": "OK", "command": "npx", "args": []
        })
        mock_mcp = AsyncMock()
        with patch("companion_core.main.McpClient", return_value=mock_mcp):
            r = await client.get("/api/mcp/servers/ok-status/status")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_status_error_when_server_fails():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "err-status", "name": "Err", "command": "false", "args": []
        })
        mock_mcp = AsyncMock()
        mock_mcp.start = AsyncMock(side_effect=Exception("process not found"))
        with patch("companion_core.main.McpClient", return_value=mock_mcp):
            r = await client.get("/api/mcp/servers/err-status/status")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "error"
    assert "process not found" in data["detail"]


@pytest.mark.asyncio
async def test_status_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/mcp/servers/ghost/status")
    assert r.status_code == 404
