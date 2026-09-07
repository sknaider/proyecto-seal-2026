"""Session history full-text search (FTS) — PostgreSQL tsvector/tsquery.

Searches sessions.summary and distilled_exchanges (summary + exchange_core +
specific_context) using plainto_tsquery with ts_rank ranking and ts_headline
snippets.

Using 'simple' FTS config for multilingual content (Spanish + English).

Usage:
    import asyncio
    from tools.memory.session_fts import SessionFTS

    async def main():
        fts = await SessionFTS.create()
        results = await fts.search("OCEAN calibration", agent="ADA", limit=10)
        for r in results:
            print(r.rank, r.headline)

    asyncio.run(main())
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

try:
    import asyncpg
    _HAS_ASYNCPG = True
except ImportError:
    _HAS_ASYNCPG = False

_DEFAULT_DSN = (
    f"postgresql://"
    f"{os.environ.get('SEAL_PG_USER','seal')}:"
    f"{os.environ.get('SEAL_PG_PASSWORD','seal_memory_2026')}@"
    f"{os.environ.get('SEAL_PG_HOST','localhost')}:"
    f"{os.environ.get('SEAL_PG_PORT','5433')}/"
    f"{os.environ.get('SEAL_PG_DB','seal_memory')}"
)

_FTS_LANG = "simple"

_EXCHANGES_SQL = f"""
SELECT
    'exchange'::text          AS source,
    id,
    agent,
    exchange_time             AS created_at,
    summary,
    ts_rank(
        to_tsvector('{_FTS_LANG}',
            coalesce(summary,'') || ' ' ||
            coalesce(exchange_core,'') || ' ' ||
            coalesce(specific_context,'')),
        plainto_tsquery('{_FTS_LANG}', $1)
    )                         AS rank,
    ts_headline(
        '{_FTS_LANG}',
        coalesce(exchange_core, summary, ''),
        plainto_tsquery('{_FTS_LANG}', $1),
        'StartSel=<<, StopSel=>>, MaxWords=60, MinWords=20'
    )                         AS headline
FROM distilled_exchanges
WHERE
    to_tsvector('{_FTS_LANG}',
        coalesce(summary,'') || ' ' ||
        coalesce(exchange_core,'') || ' ' ||
        coalesce(specific_context,''))
    @@ plainto_tsquery('{_FTS_LANG}', $1)
    AND ($2::text IS NULL OR agent = $2)
ORDER BY rank DESC
LIMIT $3
"""

_SESSIONS_SQL = f"""
SELECT
    'session'::text           AS source,
    id,
    agent,
    started_at                AS created_at,
    summary,
    ts_rank(
        to_tsvector('{_FTS_LANG}', coalesce(summary,'')),
        plainto_tsquery('{_FTS_LANG}', $1)
    )                         AS rank,
    ts_headline(
        '{_FTS_LANG}',
        coalesce(summary, ''),
        plainto_tsquery('{_FTS_LANG}', $1),
        'StartSel=<<, StopSel=>>, MaxWords=60, MinWords=20'
    )                         AS headline
FROM sessions
WHERE
    to_tsvector('{_FTS_LANG}', coalesce(summary,''))
    @@ plainto_tsquery('{_FTS_LANG}', $1)
    AND ($2::text IS NULL OR agent = $2)
ORDER BY rank DESC
LIMIT $3
"""


@dataclass
class FTSResult:
    source: str
    id: int
    agent: str
    rank: float
    headline: str
    created_at: Optional[datetime] = None
    summary: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "id": self.id,
            "agent": self.agent,
            "rank": round(self.rank, 6),
            "headline": self.headline,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "summary": self.summary,
        }


class SessionFTS:
    """Full-text search over SEAL session history (sessions + distilled_exchanges)."""

    def __init__(self, pool) -> None:
        self._pool = pool

    @classmethod
    async def create(cls, dsn: Optional[str] = None) -> "SessionFTS":
        """Create SessionFTS with a connection pool."""
        if not _HAS_ASYNCPG:
            raise RuntimeError("asyncpg required: pip install asyncpg")
        pool = await asyncpg.create_pool(dsn or _DEFAULT_DSN, min_size=1, max_size=5)
        return cls(pool)

    async def search(
        self,
        query: str,
        agent: Optional[str] = None,
        limit: int = 20,
        source: str = "both",
    ) -> List[FTSResult]:
        """Full-text search over session history.

        Args:
            query:  Natural language query (passed to plainto_tsquery).
            agent:  Filter to one agent (None = all agents).
            limit:  Max results to return.
            source: "sessions" | "exchanges" | "both" (default).

        Returns:
            List of FTSResult sorted by descending rank.
        """
        if not query or not query.strip():
            return []

        results: List[FTSResult] = []
        async with self._pool.acquire() as conn:
            if source in ("exchanges", "both"):
                rows = await conn.fetch(_EXCHANGES_SQL, query, agent, limit)
                results.extend(_row_to_result(r) for r in rows)

            if source in ("sessions", "both"):
                rows = await conn.fetch(_SESSIONS_SQL, query, agent, limit)
                results.extend(_row_to_result(r) for r in rows)

        results.sort(key=lambda r: r.rank, reverse=True)
        return results[:limit]

    async def setup_indexes(self) -> None:
        """Create GIN indexes for fast FTS. Safe to call multiple times (IF NOT EXISTS)."""
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_distilled_exchanges_fts
                ON distilled_exchanges
                USING GIN (
                    to_tsvector('simple',
                        coalesce(summary,'') || ' ' ||
                        coalesce(exchange_core,'') || ' ' ||
                        coalesce(specific_context,''))
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_fts
                ON sessions
                USING GIN (
                    to_tsvector('simple', coalesce(summary,''))
                )
            """)

    async def close(self) -> None:
        await self._pool.close()


def _row_to_result(row) -> FTSResult:
    return FTSResult(
        source=row["source"],
        id=row["id"],
        agent=row["agent"] or "",
        rank=float(row["rank"]),
        headline=row["headline"] or "",
        created_at=row["created_at"],
        summary=row["summary"],
    )
