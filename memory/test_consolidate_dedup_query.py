import asyncio

import consolidate


class _Connection:
    def __init__(self):
        self.sql = ""
        self.args = ()

    async def fetch(self, sql, *args):
        self.sql = sql
        self.args = args
        return []


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self):
        self.connection = _Connection()

    def acquire(self):
        return _Acquire(self.connection)


def test_dedup_query_uses_bounded_recent_hnsw_shortlist_not_quadratic_join():
    pool = _Pool()
    result = asyncio.run(consolidate.merge_redundant(pool, candidate_hours=24))

    assert result == []
    assert pool.connection.args == (24,)
    sql = " ".join(pool.connection.sql.split())
    assert "created_at > NOW() - ($1 * INTERVAL '1 hour')" in sql
    assert "JOIN LATERAL" in sql
    assert "ORDER BY b.embedding <=> a.embedding" in sql
    assert "LIMIT 64" in sql
    assert "candidate.agent = a.agent" in sql
    assert "candidate.category = a.category" in sql
    assert "LIMIT 1" in sql
    assert "JOIN memories b ON" not in sql


def test_dedup_query_rejects_unbounded_window():
    pool = _Pool()
    try:
        asyncio.run(consolidate.merge_redundant(pool, candidate_hours=0))
    except ValueError as exc:
        assert "candidate_hours" in str(exc)
    else:
        raise AssertionError("an unbounded dedup window must fail closed")
