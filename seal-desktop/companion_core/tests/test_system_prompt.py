"""Tests for system prompt config and enhanced health endpoint."""
import pytest
from httpx import AsyncClient, ASGITransport
from companion_core.main import app, _DEFAULT_SYSTEM


@pytest.mark.asyncio
async def test_health_includes_stats():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "stats" in data
    assert "memories" in data["stats"]
    assert "messages" in data["stats"]
    assert "mcp_servers" in data["stats"]


@pytest.mark.asyncio
async def test_health_stats_reflect_data():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/memories", json={"content": "health test memory"})
        await client.post("/api/mcp/servers", json={"id": "h-srv", "name": "H", "command": "echo"})
        r = await client.get("/api/health")
    stats = r.json()["stats"]
    assert stats["memories"] >= 1
    assert stats["mcp_servers"] >= 1


@pytest.mark.asyncio
async def test_get_system_prompt_default():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/companion/system-prompt")
    assert r.status_code == 200
    data = r.json()
    assert "template" in data
    assert "default" in data
    assert data["default"] == _DEFAULT_SYSTEM
    assert "variables" in data
    assert "user_name" in data["variables"]


@pytest.mark.asyncio
async def test_set_system_prompt():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.patch("/api/companion/system-prompt", json={
            "template": "You are {user_name}'s assistant. Today is {today_date}."
        })
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_set_then_get_system_prompt():
    custom = "Custom system prompt for {user_name}."
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch("/api/companion/system-prompt", json={"template": custom})
        r = await client.get("/api/companion/system-prompt")
    assert r.json()["template"] == custom


@pytest.mark.asyncio
async def test_set_empty_system_prompt_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.patch("/api/companion/system-prompt", json={"template": ""})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_system_prompt_variables_list():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/companion/system-prompt")
    variables = r.json()["variables"]
    assert "user_name" in variables
    assert "today_date" in variables
    assert "user_goal" in variables
