"""Tests for extended CRUD: delete thread, search convos, memory get/update/delete."""
import pytest
from httpx import AsyncClient, ASGITransport
from companion_core.main import app


# ── Thread delete + search ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_thread():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create some messages
        await client.post("/api/chat", json={"thread_id": "del-thread", "content": "hello"})
        r = await client.delete("/api/chat/threads/del-thread")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_delete_thread_then_messages_gone():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/chat", json={"thread_id": "del-check", "content": "gone soon"})
        await client.delete("/api/chat/threads/del-check")
        r = await client.get("/api/chat/threads/del-check/messages")
    assert r.json()["messages"] == []


@pytest.mark.asyncio
async def test_delete_thread_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/chat/threads/nonexistent")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_search_conversations():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/chat", json={
            "thread_id": "search-thread", "content": "unique_search_keyword_xyz"
        })
        r = await client.get("/api/chat/search?q=unique_search_keyword_xyz")
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) >= 1
    assert any("unique_search_keyword_xyz" in res["content"] for res in results)


@pytest.mark.asyncio
async def test_search_conversations_empty_q_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/chat/search?q=")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_search_conversations_no_match():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/chat/search?q=zzz_no_match_ever_xyz")
    assert r.status_code == 200
    assert r.json()["results"] == []


# ── Memory CRUD ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_memory_by_id():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/memories", json={
            "content": "get-by-id test", "importance": 8
        })
        mem_id = r1.json()["id"]
        r2 = await client.get(f"/api/memories/{mem_id}")
    assert r2.status_code == 200
    data = r2.json()
    assert data["id"] == mem_id
    assert data["content"] == "get-by-id test"
    assert data["importance"] == 8


@pytest.mark.asyncio
async def test_get_memory_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/memories/999999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_memory_content():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/memories", json={"content": "original content"})
        mem_id = r1.json()["id"]
        await client.patch(f"/api/memories/{mem_id}", json={"content": "updated content"})
        r2 = await client.get(f"/api/memories/{mem_id}")
    assert r2.json()["content"] == "updated content"


@pytest.mark.asyncio
async def test_update_memory_importance():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/memories", json={"content": "importance test", "importance": 3})
        mem_id = r1.json()["id"]
        await client.patch(f"/api/memories/{mem_id}", json={"importance": 9})
        r2 = await client.get(f"/api/memories/{mem_id}")
    assert r2.json()["importance"] == 9


@pytest.mark.asyncio
async def test_update_memory_empty_content_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/memories", json={"content": "non-empty"})
        mem_id = r1.json()["id"]
        r2 = await client.patch(f"/api/memories/{mem_id}", json={"content": ""})
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_delete_memory():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/memories", json={"content": "to be deleted"})
        mem_id = r1.json()["id"]
        r2 = await client.delete(f"/api/memories/{mem_id}")
        assert r2.json()["ok"] is True
        r3 = await client.get(f"/api/memories/{mem_id}")
    assert r3.status_code == 404


@pytest.mark.asyncio
async def test_delete_memory_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/memories/999999")
    assert r.status_code == 404
