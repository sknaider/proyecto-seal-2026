"""SPECTRE hippocampus — D2 high-level episodic retrieval.

Higher-level wrapper over episodic_api. Adds:
  - Memory content fetching (JOIN with soul_v3.memories)
  - retrieve_by_date: scan by date range without knowing context_hash
  - episode_exists: fast existence check
  - sync wrappers for use from synchronous code

Ref: spec_spectre_contract_v2.md D2, SUMMARY_JARVIS.md §D2
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import asyncpg

from episodic_api import (
    episodic_index,
    episodic_lookup,
    episodic_stats,
    extract_keywords,
    compute_context_hash,
)

_DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
_SCHEMA = "soul_v3"


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _get_conn() -> asyncpg.Connection:
    return await asyncpg.connect(_DB_URL)


async def _fetch_memory_content(conn: asyncpg.Connection, memory_ids: list[int]) -> dict[int, dict]:
    """Fetch content for a list of memory IDs from soul_v3.memories."""
    if not memory_ids:
        return {}
    rows = await conn.fetch(
        f"""
        SELECT id, content, agent, importance, created_at
        FROM {_SCHEMA}.memories
        WHERE id = ANY($1::bigint[])
        """,
        memory_ids,
    )
    return {r["id"]: dict(r) for r in rows}


# ── Public async API ──────────────────────────────────────────────────────────

async def store_episode(
    agent: str,
    memory_id: int,
    context: str,
    participants: list[str] | None = None,
) -> int:
    """
    Index a memory into the episodic hash table.

    Wraps episodic_api.episodic_index with auto keyword extraction.
    Returns: inserted row id.
    """
    return await episodic_index(
        agent=agent,
        memory_id=memory_id,
        context=context,
        participants=participants or [agent],
    )


async def retrieve_by_hash(
    agent: str,
    context_hash: str,
    days_back: int = 7,
    with_content: bool = False,
) -> list[dict[str, Any]]:
    """
    Fast retrieval by agent + context_hash.

    Returns list of dicts: {memory_id, context_hash, [content, agent, importance, created_at]}.
    with_content=True fetches full memory content from soul_v3.memories (adds one DB round-trip).
    """
    memory_ids = await episodic_lookup(agent, context_hash, days_back)
    if not memory_ids:
        return []

    results = [{"memory_id": mid, "context_hash": context_hash} for mid in memory_ids]

    if with_content and memory_ids:
        conn = await _get_conn()
        try:
            content_map = await _fetch_memory_content(conn, memory_ids)
            for r in results:
                r.update(content_map.get(r["memory_id"], {}))
        finally:
            await conn.close()

    return results


async def retrieve_by_date(
    agent: str,
    date_from: date,
    date_to: date | None = None,
    with_content: bool = False,
) -> list[dict[str, Any]]:
    """
    Retrieve all episodic entries for an agent in a date range.

    date_to defaults to today. Returns list ordered by timestamp_bucket DESC.
    with_content=True fetches memory content (extra DB round-trip).
    """
    date_to = date_to or date.today()
    conn = await _get_conn()
    try:
        rows = await conn.fetch(
            f"""
            SELECT id, agent, timestamp_bucket, context_hash, memory_id
            FROM {_SCHEMA}.episodic_index
            WHERE agent = $1
              AND timestamp_bucket BETWEEN $2 AND $3
            ORDER BY timestamp_bucket DESC, id DESC
            """,
            agent, date_from, date_to,
        )
        results = [dict(r) for r in rows]

        if with_content and results:
            memory_ids = [r["memory_id"] for r in results]
            content_map = await _fetch_memory_content(conn, memory_ids)
            for r in results:
                r.update(content_map.get(r["memory_id"], {}))

        return results
    finally:
        await conn.close()


async def episode_exists(
    agent: str,
    context_hash: str,
    days_back: int = 7,
) -> bool:
    """Fast check: does agent have any episodic entry matching this context_hash recently?"""
    ids = await episodic_lookup(agent, context_hash, days_back)
    return len(ids) > 0


async def hash_from_context(
    participants: list[str],
    context: str,
    hour_bucket: int | None = None,
) -> str:
    """Derive context_hash from participants + free text context (auto keyword extraction)."""
    kw = extract_keywords(context)
    return compute_context_hash(participants, kw, hour_bucket)


# ── Sync wrappers (for use from synchronous code / tests) ─────────────────────

def sync_store_episode(agent: str, memory_id: int, context: str,
                       participants: list[str] | None = None) -> int:
    return asyncio.run(store_episode(agent, memory_id, context, participants))


def sync_retrieve_by_hash(agent: str, context_hash: str,
                           days_back: int = 7, with_content: bool = False) -> list[dict]:
    return asyncio.run(retrieve_by_hash(agent, context_hash, days_back, with_content))


def sync_retrieve_by_date(agent: str, date_from: date,
                           date_to: date | None = None, with_content: bool = False) -> list[dict]:
    return asyncio.run(retrieve_by_date(agent, date_from, date_to, with_content))


def sync_episode_exists(agent: str, context_hash: str, days_back: int = 7) -> bool:
    return asyncio.run(episode_exists(agent, context_hash, days_back))
