import pytest
from httpx import AsyncClient, ASGITransport
from companion_core.main import app


@pytest.mark.asyncio
async def test_post_memory_and_get():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/memories", json={
            "agent": "USER",
            "category": "test",
            "content": "companion_core memory test",
            "importance": 7
        })
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        mem_id = data.get("id")
        assert mem_id is not None

        r2 = await client.get("/api/memories?limit=10&agent=USER")
        assert r2.status_code == 200
        mems = r2.json().get("memories", [])
        ids = [m["id"] for m in mems]
        assert mem_id in ids


@pytest.mark.asyncio
async def test_memory_fts_search():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/memories", json={
            "agent": "USER", "content": "gravitational waves detected by LIGO"
        })
        await client.post("/api/memories", json={
            "agent": "USER", "content": "neural network training loss converged"
        })
        r = await client.get("/api/memories?search=gravitational&agent=all")
        assert r.status_code == 200
        mems = r.json().get("memories", [])
        assert len(mems) >= 1
        assert any("gravitational" in m["content"] for m in mems)


@pytest.mark.asyncio
async def test_get_chat_threads_empty():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/chat/threads")
        assert r.status_code == 200
        assert "threads" in r.json()


@pytest.mark.asyncio
async def test_post_chat_persists_message():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/chat", json={
            "thread_id": "test-thread-1",
            "content": "hello companion",
            "model": "test"
        })
        assert r.status_code == 200
        data = r.json()
        assert "reply" in data or "error" in data  # may fail if no Ollama

        r2 = await client.get("/api/chat/threads/test-thread-1/messages")
        assert r2.status_code == 200
        msgs = r2.json().get("messages", [])
        assert any(m["content"] == "hello companion" for m in msgs)
