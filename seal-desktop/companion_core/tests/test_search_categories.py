"""Tests for unified search and memory categories."""
import pytest
from httpx import AsyncClient, ASGITransport
from companion_core.main import app


# ── Unified search ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unified_search_finds_memory():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/memories", json={
            "content": "quantum_computing_unique_term", "category": "tech"
        })
        r = await client.get("/api/search?q=quantum_computing_unique_term")
    assert r.status_code == 200
    results = r.json()["results"]
    mem_results = [x for x in results if x["type"] == "memory"]
    assert len(mem_results) >= 1
    assert any("quantum_computing_unique_term" in m["content"] for m in mem_results)


@pytest.mark.asyncio
async def test_unified_search_finds_conversation():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/chat", json={
            "thread_id": "search-conv", "content": "blockchain_unique_search_term"
        })
        r = await client.get("/api/search?q=blockchain_unique_search_term")
    assert r.status_code == 200
    results = r.json()["results"]
    conv_results = [x for x in results if x["type"] == "conversation"]
    assert len(conv_results) >= 1
    assert any("blockchain_unique_search_term" in c["content"] for c in conv_results)


@pytest.mark.asyncio
async def test_unified_search_returns_both_types():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        term = "dualmatch_xyz_term"
        await client.post("/api/memories", json={"content": f"memory with {term}"})
        await client.post("/api/chat", json={
            "thread_id": "dual-thread", "content": f"chat with {term}"
        })
        r = await client.get(f"/api/search?q={term}")
    results = r.json()["results"]
    types = {x["type"] for x in results}
    assert "memory" in types
    assert "conversation" in types


@pytest.mark.asyncio
async def test_unified_search_empty_q_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/search?q=")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_unified_search_no_results():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/search?q=zzznomatchever12345")
    assert r.status_code == 200
    assert r.json()["results"] == []
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_unified_search_respects_limit():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for i in range(5):
            await client.post("/api/memories", json={"content": f"limitterm_{i} matchword"})
        r = await client.get("/api/search?q=matchword&limit=3")
    assert len(r.json()["results"]) <= 3


# ── Memory categories ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_categories_empty():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/memories/categories")
    assert r.status_code == 200
    data = r.json()
    assert "categories" in data
    assert isinstance(data["categories"], list)


@pytest.mark.asyncio
async def test_categories_after_insert():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/memories", json={"content": "tech memo", "category": "tech"})
        await client.post("/api/memories", json={"content": "tech memo2", "category": "tech"})
        await client.post("/api/memories", json={"content": "health memo", "category": "health"})
        r = await client.get("/api/memories/categories")
    cats = {c["category"]: c["count"] for c in r.json()["categories"]}
    assert cats.get("tech", 0) >= 2
    assert cats.get("health", 0) >= 1


@pytest.mark.asyncio
async def test_categories_excludes_null():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/memories", json={"content": "no category"})
        r = await client.get("/api/memories/categories")
    categories = [c["category"] for c in r.json()["categories"]]
    assert None not in categories
    assert "None" not in categories
