"""Tests for MCP Registry endpoints."""
import pytest
from httpx import AsyncClient, ASGITransport
from companion_core.main import app, _MCP_REGISTRY


@pytest.mark.asyncio
async def test_list_registry_returns_all():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/mcp/registry")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == len(_MCP_REGISTRY)
    assert len(data["servers"]) == len(_MCP_REGISTRY)
    # Every entry has required fields
    for s in data["servers"]:
        assert "id" in s and "name" in s and "command" in s
        assert "category" in s and "requires_env" in s


@pytest.mark.asyncio
async def test_list_registry_filter_by_category():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/mcp/registry?category=web")
    assert r.status_code == 200
    servers = r.json()["servers"]
    assert len(servers) >= 1
    assert all(s["category"] == "web" for s in servers)


@pytest.mark.asyncio
async def test_list_registry_unknown_category_returns_empty():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/mcp/registry?category=nonexistent")
    assert r.status_code == 200
    assert r.json()["servers"] == []
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_install_from_registry():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/mcp/registry/filesystem/install")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["id"] == "filesystem"
    assert data["name"] == "Filesystem"


@pytest.mark.asyncio
async def test_install_from_registry_then_appears_in_servers():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/registry/fetch/install")
        r = await client.get("/api/mcp/servers")
    servers = r.json()["servers"]
    ids = [s["id"] for s in servers]
    assert "fetch" in ids


@pytest.mark.asyncio
async def test_install_from_registry_duplicate_returns_409():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/registry/github/install")
        r = await client.post("/api/mcp/registry/github/install")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_install_unknown_registry_entry_returns_404():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/mcp/registry/nonexistent-server/install")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_registry_preserves_env_template():
    """GitHub entry should have GITHUB_PERSONAL_ACCESS_TOKEN in env and requires_env."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/mcp/registry")
    github = next(s for s in r.json()["servers"] if s["id"] == "github")
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in github["requires_env"]
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in github["env"]
