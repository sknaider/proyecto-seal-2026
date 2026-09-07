"""Tests for /api/companion/stats and /api/companion/briefing."""
import pytest
from httpx import AsyncClient, ASGITransport
from companion_core.main import app


@pytest.mark.asyncio
async def test_stats_empty():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/companion/stats")
    assert r.status_code == 200
    data = r.json()
    assert "memories" in data
    assert "messages" in data
    assert "threads" in data
    assert "goals" in data
    assert "active" in data["goals"]
    assert "completed" in data["goals"]
    assert "mcp_servers_enabled" in data
    assert "generated_at" in data


@pytest.mark.asyncio
async def test_stats_reflect_data():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/memories", json={"content": "stats test memory"})
        await client.post("/api/goals", json={"title": "Stats goal"})
        await client.post("/api/mcp/servers", json={
            "id": "stats-srv", "name": "S", "command": "echo", "enabled": True
        })
        r = await client.get("/api/companion/stats")
    data = r.json()
    assert data["memories"] >= 1
    assert data["goals"]["active"] >= 1
    assert data["mcp_servers_enabled"] >= 1


@pytest.mark.asyncio
async def test_briefing_returns_greeting():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch("/api/companion/config", json={"name": "William"})
        r = await client.post("/api/companion/briefing")
    assert r.status_code == 200
    data = r.json()
    assert "briefing" in data
    assert "William" in data["briefing"]
    assert "name" in data
    assert "date" in data


@pytest.mark.asyncio
async def test_briefing_includes_goals():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/goals", json={"title": "Ship SEAL Companion", "priority": 10})
        r = await client.post("/api/companion/briefing")
    data = r.json()
    assert data["goal_count"] >= 1
    assert "Ship SEAL Companion" in data["briefing"]


@pytest.mark.asyncio
async def test_briefing_no_name_uses_there():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/companion/briefing")
    data = r.json()
    assert "there" in data["briefing"] or data["name"] in data["briefing"]


@pytest.mark.asyncio
async def test_briefing_includes_mcp_count():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/mcp/servers", json={
            "id": "brief-srv", "name": "B", "command": "echo"
        })
        r = await client.post("/api/companion/briefing")
    assert "tool" in r.json()["briefing"].lower()


@pytest.mark.asyncio
async def test_briefing_ends_with_question():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/companion/briefing")
    briefing = r.json()["briefing"]
    assert briefing.endswith("today?")


@pytest.mark.asyncio
async def test_briefing_returns_agent_name():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/companion/briefing")
    data = r.json()
    assert "agent_name" in data
    assert "emotional_state" in data


# ── Context endpoint ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_context_returns_all_fields():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/companion/context")
    assert r.status_code == 200
    data = r.json()
    assert "user_name" in data
    assert "api_key_set" in data
    assert "default_model" in data
    assert "first_run_complete" in data
    assert "agent" in data
    assert "counts" in data
    assert "version" in data
    assert "generated_at" in data


@pytest.mark.asyncio
async def test_context_agent_has_ocean():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/companion/context")
    agent = r.json()["agent"]
    assert "name" in agent
    assert "emotional_state" in agent
    assert "ocean" in agent


@pytest.mark.asyncio
async def test_context_counts_reflect_data():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/memories", json={"content": "context test memory"})
        await client.post("/api/goals", json={"title": "context test goal"})
        await client.post("/api/skills", json={"name": "CtxSkill", "prompt_template": "do it"})
        r = await client.get("/api/companion/context")
    counts = r.json()["counts"]
    assert counts["memories"] >= 1
    assert counts["active_goals"] >= 1
    assert counts["skills"] >= 1


@pytest.mark.asyncio
async def test_context_api_key_set_reflects_config():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch("/api/companion/config", json={"api_key": "sk-test-key"})
        r = await client.get("/api/companion/context")
    assert r.json()["api_key_set"] is True
