"""Tests for Goals API and conversation export."""
import pytest
from httpx import AsyncClient, ASGITransport
from companion_core.main import app


# ── Goals ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_goal():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/goals", json={"title": "Learn Python", "priority": 8})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert isinstance(r.json()["id"], int)


@pytest.mark.asyncio
async def test_create_goal_empty_title_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/goals", json={"title": ""})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_list_goals_empty():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/goals")
    assert r.status_code == 200
    assert "goals" in r.json()
    assert isinstance(r.json()["goals"], list)


@pytest.mark.asyncio
async def test_list_goals_after_create():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/goals", json={"title": "Test Goal", "priority": 7})
        r = await client.get("/api/goals?status=active")
    goals = r.json()["goals"]
    assert len(goals) >= 1
    assert any(g["title"] == "Test Goal" for g in goals)


@pytest.mark.asyncio
async def test_list_goals_all_status():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/goals?status=all")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_list_goals_invalid_status_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/goals?status=invalid")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_get_goal_by_id():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/goals", json={
            "title": "Ship v1.0", "description": "Launch the product", "priority": 10
        })
        gid = r1.json()["id"]
        r2 = await client.get(f"/api/goals/{gid}")
    assert r2.status_code == 200
    data = r2.json()
    assert data["title"] == "Ship v1.0"
    assert data["description"] == "Launch the product"
    assert data["priority"] == 10
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_get_goal_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/goals/999999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_goal_status():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/goals", json={"title": "Complete me"})
        gid = r1.json()["id"]
        r2 = await client.patch(f"/api/goals/{gid}", json={"status": "completed"})
        assert r2.json()["ok"] is True
        r3 = await client.get(f"/api/goals/{gid}")
    assert r3.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_update_goal_invalid_status_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/goals", json={"title": "Bad status"})
        gid = r1.json()["id"]
        r = await client.patch(f"/api/goals/{gid}", json={"status": "flying"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_goal_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.patch("/api/goals/999999", json={"title": "ghost"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_goal():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/goals", json={"title": "Delete me"})
        gid = r1.json()["id"]
        r2 = await client.delete(f"/api/goals/{gid}")
        assert r2.json()["ok"] is True
        r3 = await client.get(f"/api/goals/{gid}")
    assert r3.status_code == 404


@pytest.mark.asyncio
async def test_delete_goal_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/goals/999999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_goals_filter_by_status():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/goals", json={"title": "Active goal"})
        gid = r1.json()["id"]
        await client.post("/api/goals", json={"title": "Another active"})
        await client.patch(f"/api/goals/{gid}", json={"status": "archived"})

        r_active = await client.get("/api/goals?status=active")
        r_archived = await client.get("/api/goals?status=archived")

    active_ids = [g["id"] for g in r_active.json()["goals"]]
    archived_ids = [g["id"] for g in r_archived.json()["goals"]]
    assert gid not in active_ids
    assert gid in archived_ids


# ── Export conversation ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_export_thread_markdown():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/chat", json={
            "thread_id": "export-thread", "content": "hello export"
        })
        r = await client.get("/api/chat/threads/export-thread/export")
    assert r.status_code == 200
    data = r.json()
    assert "markdown" in data
    assert "hello export" in data["markdown"]
    assert "**You**" in data["markdown"]
    assert data["message_count"] >= 1


@pytest.mark.asyncio
async def test_export_thread_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/chat/threads/ghost-thread/export")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_export_includes_assistant_label():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/chat", json={
            "thread_id": "export-asst", "content": "check assistant label"
        })
        r = await client.get("/api/chat/threads/export-asst/export")
    assert "**Assistant**" in r.json()["markdown"]
