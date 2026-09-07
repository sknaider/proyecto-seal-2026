"""PostgreSQL backend — production storage for SOUL Framework.

Requires: pip install soul-framework[postgres]
"""

from __future__ import annotations

from typing import Any

try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore[assignment]

# PostgreSQL schema — uses SERIAL, TIMESTAMPTZ, JSONB, and pgvector-ready BYTEA
POSTGRES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id SERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'fact',
    content TEXT NOT NULL,
    embedding BYTEA,
    importance INTEGER NOT NULL DEFAULT 5,
    valence REAL DEFAULT 0.0,
    arousal REAL DEFAULT 0.0,
    dominance REAL DEFAULT 0.0,
    source TEXT DEFAULT 'conversation',
    scope TEXT DEFAULT 'private',
    confidence_score REAL DEFAULT 1.0,
    utility_score REAL DEFAULT 0.5,
    event_time TEXT,
    episode_context TEXT,
    metadata JSONB DEFAULT '{}',
    valid_from TIMESTAMPTZ NOT NULL,
    invalid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_agent ON memories(agent);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(agent, category);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(agent, importance DESC);

CREATE TABLE IF NOT EXISTS identity (
    agent TEXT PRIMARY KEY,
    personality TEXT DEFAULT '',
    boot_context TEXT DEFAULT '',
    philosophy TEXT DEFAULT '',
    ocean_scores JSONB DEFAULT '{}',
    ocean_baseline JSONB DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS relationships (
    id SERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    person TEXT NOT NULL,
    trust_level REAL DEFAULT 0.5,
    style TEXT DEFAULT 'default',
    dynamic TEXT DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE(agent, person)
);

CREATE TABLE IF NOT EXISTS rules (
    id SERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    rule_key TEXT NOT NULL,
    content TEXT NOT NULL,
    set_by TEXT DEFAULT 'system',
    priority TEXT DEFAULT 'normal',
    active INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(agent, rule_key)
);

CREATE TABLE IF NOT EXISTS inner_monologue (
    id SERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    session_id TEXT DEFAULT '',
    turn_number INTEGER DEFAULT 0,
    thought TEXT NOT NULL,
    emotional_state TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_monologue_agent ON inner_monologue(agent, created_at DESC);

CREATE TABLE IF NOT EXISTS diary (
    id SERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    mood TEXT DEFAULT '',
    content TEXT NOT NULL,
    session_date TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS instincts (
    id SERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    trigger_pattern TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    activation_count INTEGER DEFAULT 0,
    last_activated TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_instincts_agent ON instincts(agent);

CREATE TABLE IF NOT EXISTS working_state (
    agent TEXT PRIMARY KEY,
    state JSONB DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL,
    turn_count INTEGER DEFAULT 0
);
"""


class PostgresBackend:
    """PostgreSQL backend using asyncpg. Production-grade.

    Usage:
        backend = PostgresBackend("postgresql://user:pass@localhost:5432/soul")
        await backend.initialize()
    """

    def __init__(self, url: str) -> None:
        if asyncpg is None:
            raise ImportError(
                "asyncpg is required for PostgresBackend. "
                "Install with: pip install soul-framework[postgres]"
            )
        self._url = url
        self._pool: asyncpg.Pool | None = None

    async def initialize(self) -> None:
        """Create connection pool and ensure tables exist."""
        self._pool = await asyncpg.create_pool(self._url, min_size=2, max_size=10)
        async with self._pool.acquire() as conn:
            await conn.execute(POSTGRES_SCHEMA_SQL)

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Backend not initialized. Call initialize() first.")
        return self._pool

    async def execute(self, sql: str, *params: Any) -> None:
        """Execute a write query."""
        async with self._get_pool().acquire() as conn:
            await conn.execute(sql, *params)

    async def fetchone(self, sql: str, *params: Any) -> dict[str, Any] | None:
        """Fetch a single row as dict."""
        async with self._get_pool().acquire() as conn:
            row = await conn.fetchrow(sql, *params)
            if row is None:
                return None
            return dict(row)

    async def fetchall(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        """Fetch all rows as list of dicts."""
        async with self._get_pool().acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [dict(r) for r in rows]

    async def fetchval(self, sql: str, *params: Any) -> Any:
        """Fetch a single scalar value."""
        async with self._get_pool().acquire() as conn:
            return await conn.fetchval(sql, *params)

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
