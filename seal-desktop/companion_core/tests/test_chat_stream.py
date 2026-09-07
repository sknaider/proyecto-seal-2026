"""Tests for SSE streaming chat endpoint."""
import json
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from companion_core.main import app


def _parse_sse(content: bytes) -> list[dict]:
    """Parse SSE response into list of data payloads."""
    results = []
    for line in content.decode().splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            results.append(json.loads(line[6:]))
    return results


@pytest.mark.asyncio
async def test_stream_without_api_key_returns_stub():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/chat/stream", json={
            "thread_id": "stream-stub",
            "content": "hello"
        })
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    events = _parse_sse(r.content)
    assert len(events) >= 1
    assert "text" in events[0]


@pytest.mark.asyncio
async def test_stream_persists_user_message():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/chat/stream", json={
            "thread_id": "stream-persist",
            "content": "streaming test message"
        })
        r2 = await client.get("/api/chat/threads/stream-persist/messages")
    msgs = r2.json()["messages"]
    assert any(m["content"] == "streaming test message" and m["role"] == "user" for m in msgs)


@pytest.mark.asyncio
async def test_stream_with_api_key_uses_stream_claude():
    """With API key, stream_claude is called and chunks arrive."""
    async def fake_stream(messages, api_key, model, system, max_tokens=2048):
        for chunk in ["Hello", " ", "world", "!"]:
            yield chunk

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch("/api/companion/config", json={"api_key": "sk-stream-test"})
        with patch("companion_core.main.stream_claude", new=fake_stream):
            r = await client.post("/api/chat/stream", json={
                "thread_id": "stream-claude-test",
                "content": "test stream"
            })

    assert r.status_code == 200
    events = _parse_sse(r.content)
    texts = [e["text"] for e in events if "text" in e]
    assert "".join(texts) == "Hello world!"


@pytest.mark.asyncio
async def test_stream_persists_assistant_reply_after_completion():
    """Full streamed reply is saved to DB after streaming finishes."""
    async def fake_stream(messages, api_key, model, system, max_tokens=2048):
        yield "Streamed reply"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch("/api/companion/config", json={"api_key": "sk-persist-test"})
        with patch("companion_core.main.stream_claude", new=fake_stream):
            await client.post("/api/chat/stream", json={
                "thread_id": "stream-save",
                "content": "save this reply"
            })
        r = await client.get("/api/chat/threads/stream-save/messages")

    msgs = r.json()["messages"]
    assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant_msgs) >= 1
    assert "Streamed reply" in assistant_msgs[0]["content"]


@pytest.mark.asyncio
async def test_stream_includes_done_sentinel():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/chat/stream", json={
            "thread_id": "stream-done",
            "content": "check done"
        })
    assert b"data: [DONE]" in r.content
