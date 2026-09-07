import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from soul_lite_adapter import PgVectorAdapter


class _Tx:
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False


class _Conn:
    def __init__(self):
        self.fetch_calls = 0
        self.commands = []

    def transaction(self): return _Tx()

    async def execute(self, sql):
        self.commands.append(sql)

    async def fetch(self, _sql, *_params):
        self.last_sql = _sql
        self.fetch_calls += 1
        count = 1 if self.fetch_calls == 1 else 3
        return [
            {
                "id": i, "content": f"memory {i}", "agent": "ADA",
                "category": "fact", "importance": 8, "source": "test",
                "created_at": None, "valence": None, "arousal": None,
                "dominance": None, "scope": "private", "confidence_score": 1.0,
                "metadata": {}, "score": 0.9,
            }
            for i in range(count)
        ]


class _Acquire:
    def __init__(self, conn): self.conn = conn
    async def __aenter__(self): return self.conn
    async def __aexit__(self, *_): return False


class _Pool:
    def __init__(self, conn): self.conn = conn
    def acquire(self): return _Acquire(self.conn)


@pytest.mark.asyncio
async def test_filtered_ann_starvation_uses_exact_fallback():
    conn = _Conn()
    adapter = PgVectorAdapter(lambda: _async_value(_Pool(conn)))
    query_filter = type("Filter", (), {"must": [], "must_not": [], "should": []})()
    response = await adapter.query_points("memories", [0.0, 1.0], limit=3, query_filter=query_filter)
    assert len(response.points) == 3
    assert conn.fetch_calls == 2
    assert any("hnsw.iterative_scan" in command for command in conn.commands)
    assert any("enable_indexscan = off" in command for command in conn.commands)
    assert "memory_poisoning_feedback" in conn.last_sql
    assert "quarantine_candidate" in conn.last_sql
    assert "content_hash_sha256" in conn.last_sql


async def _async_value(value):
    return value
