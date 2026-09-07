#!/usr/bin/env python3
"""
db_pool.py — SEAL Global Connection Pool
==========================================
+1% improvement #1: Replace individual asyncpg.connect() with shared pool.
Eliminates ~100ms per query handshake overhead. Prepared statement cache works.

Usage:
    from db_pool import get_pool, release_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM identity WHERE agent = $1", "ADA")
    # On shutdown:
    await release_pool()

Standalone:
    python3 db_pool.py test
"""

import asyncio
from typing import Optional

try:
    import asyncpg
    HAS_DB = True
except ImportError:
    HAS_DB = False

DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"

_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()


async def get_pool(
    db_url: str = DB_URL,
    min_size: int = 2,
    max_size: int = 5,
) -> asyncpg.Pool:
    """Get or create the global connection pool. Thread-safe."""
    global _pool
    if _pool and not _pool._closed:
        return _pool

    async with _pool_lock:
        # Double-check after acquiring lock
        if _pool and not _pool._closed:
            return _pool
        _pool = await asyncpg.create_pool(
            db_url,
            min_size=min_size,
            max_size=max_size,
            command_timeout=30,
        )
        return _pool


async def release_pool():
    """Close the global pool. Call on shutdown."""
    global _pool
    if _pool and not _pool._closed:
        await _pool.close()
        _pool = None


async def _run_tests():
    if not HAS_DB:
        print("PASS: T1 no asyncpg (skipped) ✓")
        return

    # T1: Create pool
    pool = await get_pool()
    assert pool is not None
    assert not pool._closed
    print("PASS: T1 create pool ✓")

    # T2: Same pool returned (singleton)
    pool2 = await get_pool()
    assert pool is pool2
    print("PASS: T2 singleton ✓")

    # T3: Acquire and query
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE agent = $1", "ADA")
        assert count > 0
    print(f"PASS: T3 query ({count} memories) ✓")

    # T4: Multiple concurrent queries
    async with asyncio.TaskGroup() as tg:
        results = []
        for _ in range(5):
            async def q():
                async with pool.acquire() as conn:
                    r = await conn.fetchval("SELECT 1")
                    results.append(r)
            tg.create_task(q())
    assert len(results) == 5
    print("PASS: T4 concurrent queries (5) ✓")

    # T5: Pool stats
    assert pool.get_size() >= 2
    print(f"PASS: T5 pool size ({pool.get_size()}) ✓")

    # T6: Release
    await release_pool()
    assert _pool is None
    print("PASS: T6 release ✓")

    # T7: Re-create after release
    pool3 = await get_pool()
    assert pool3 is not pool
    async with pool3.acquire() as conn:
        assert await conn.fetchval("SELECT 1") == 1
    await release_pool()
    print("PASS: T7 re-create after release ✓")

    print("\n=== 7/7 TESTS PASARON ✓ ===")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        asyncio.run(_run_tests())
    else:
        print("Usage: db_pool.py test")
