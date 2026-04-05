"""Database connection pool for SEAL Memory."""
from __future__ import annotations

import asyncio
import asyncpg

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None or _pool._closed:
        _pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=3)
    return _pool


async def close_pool():
    global _pool
    if _pool and not _pool._closed:
        try:
            await asyncio.wait_for(_pool.close(), timeout=10)
        except asyncio.TimeoutError:
            pass
        _pool = None
