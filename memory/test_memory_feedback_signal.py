import json

import pytest

import mcp_server_v4 as server


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, row):
        self.row = row
        self.executions = []
        self.fetchvals = []

    def transaction(self):
        return _AsyncContext(self)

    async def fetchrow(self, query, *args):
        assert "FOR UPDATE" in query
        assert args == (41,)
        return self.row

    async def execute(self, query, *args):
        self.executions.append((query, args))
        return "UPDATE 1"

    async def fetchval(self, query, *args):
        self.fetchvals.append((query, args))
        assert "INSERT INTO soul_v3.soul_feedback_signal" in query
        return 73


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _AsyncContext(self.conn)


@pytest.mark.asyncio
async def test_memory_feedback_updates_memory_and_records_owner_signal_atomically(monkeypatch):
    conn = _FakeConnection({
        "id": 41,
        "agent": "ADA",
        "importance": 7,
        "confidence_score": 0.6,
        "query_count": 2,
    })

    async def fake_get_pool():
        return _FakePool(conn)

    async def no_qdrant():
        raise RuntimeError("retired")

    monkeypatch.setattr(server, "get_pool", fake_get_pool)
    monkeypatch.setattr(server, "get_qdrant", no_qdrant)

    result = json.loads(await server.memory_feedback(
        memory_id=41,
        outcome="verified by effect",
        success=True,
        agent="NEXUS",
    ))

    assert result["feedback_signal_id"] == 73
    assert result["signal"] == "positive"
    assert len(conn.executions) == 1
    assert len(conn.fetchvals) == 1
    _, signal_args = conn.fetchvals[0]
    assert signal_args == ("ADA", "positive", "memory:41", "verified by effect", 0.1)


@pytest.mark.asyncio
async def test_memory_feedback_missing_memory_does_not_record_signal(monkeypatch):
    conn = _FakeConnection(None)

    async def fake_get_pool():
        return _FakePool(conn)

    monkeypatch.setattr(server, "get_pool", fake_get_pool)

    result = json.loads(await server.memory_feedback(
        memory_id=41,
        outcome="must not be recorded",
        success=False,
        agent="ADA",
    ))

    assert result == {"error": "Memory 41 not found"}
    assert conn.executions == []
    assert conn.fetchvals == []
