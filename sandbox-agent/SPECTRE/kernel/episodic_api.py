"""SPECTRE episodic_api — Nivel 3 D2 hippocampus interface.

API para soul_v3.episodic_index (migration 015).
context_hash: blake2b(participants_sorted + top3_keywords_sorted + hour_bucket, digest_size=4)

Funciones:
  episodic_index(agent, memory_id, context) — escribe al guardar memoria
  episodic_lookup(agent, date_range, context_hash) → [memory_id]
  episodic_purge(retention_days) — housekeeping nocturno (dream_consolidator)

Ref: spec_spectre_contract_v2_1_addendum.md F2
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Any

import asyncpg

_DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
_SCHEMA = "soul_v3"

STOPWORDS = {
    "a", "de", "el", "la", "los", "las", "en", "con", "que", "es",
    "se", "no", "por", "del", "una", "un", "al", "lo", "su", "si",
    "the", "is", "in", "of", "to", "and", "for", "are", "was", "it",
}


# ── Context Hash ──────────────────────────────────────────────────────────────

def compute_context_hash(
    participants: list[str],
    keywords: list[str],
    hour_bucket: int | None = None,
) -> str:
    """
    blake2b(participants_sorted + top3_keywords_sorted + hour_bucket) → 8-char hex.

    Participants: sorted agent/user names involved.
    Keywords: top 3, sorted alphabetically.
    Hour_bucket: UTC hour (0-23). Defaults to current UTC hour.
    """
    if hour_bucket is None:
        hour_bucket = datetime.now(timezone.utc).hour

    key = tuple(sorted(p.lower() for p in participants)) + \
          tuple(sorted(k.lower() for k in keywords[:3])) + \
          (hour_bucket,)
    raw = "\x00".join(str(x) for x in key).encode()
    return hashlib.blake2b(raw, digest_size=4).hexdigest()


def extract_keywords(text: str, n: int = 3) -> list[str]:
    """Extract top n keywords from text, stripping stopwords and punctuation."""
    words = [w.strip(".,!?¿¡:;\"'()[]") for w in text.lower().split() if len(w) > 3]
    filtered = [w for w in words if w not in STOPWORDS]
    seen: dict[str, int] = {}
    for w in filtered:
        seen[w] = seen.get(w, 0) + 1
    return sorted(seen, key=lambda w: -seen[w])[:n]


# ── DB Helpers ────────────────────────────────────────────────────────────────

async def _get_conn() -> asyncpg.Connection:
    return await asyncpg.connect(_DB_URL)


# ── Public API ────────────────────────────────────────────────────────────────

async def episodic_index(
    agent: str,
    memory_id: int,
    context: str,
    participants: list[str] | None = None,
    timestamp_bucket: date | None = None,
) -> int:
    """
    Write entry to episodic_index when a memory is created.

    Returns: inserted row id.
    """
    participants = participants or [agent]
    keywords = extract_keywords(context)
    ctx_hash = compute_context_hash(participants, keywords)
    bucket = timestamp_bucket or date.today()

    conn = await _get_conn()
    try:
        row = await conn.fetchrow(
            f"""
            INSERT INTO {_SCHEMA}.episodic_index
                (agent, timestamp_bucket, context_hash, memory_id)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            agent, bucket, ctx_hash, memory_id,
        )
        return row["id"]
    finally:
        await conn.close()


async def episodic_lookup(
    agent: str,
    context_hash: str,
    days_back: int = 7,
) -> list[int]:
    """
    Fast retrieval: return memory_ids matching agent + context_hash in date range.

    days_back: how many days to look back (default 7).
    """
    since = date.today() - timedelta(days=days_back)
    conn = await _get_conn()
    try:
        rows = await conn.fetch(
            f"""
            SELECT memory_id FROM {_SCHEMA}.episodic_index
            WHERE agent = $1
              AND context_hash = $2
              AND timestamp_bucket >= $3
            ORDER BY timestamp_bucket DESC
            """,
            agent, context_hash, since,
        )
        return [r["memory_id"] for r in rows]
    finally:
        await conn.close()


async def episodic_purge(retention_days: int = 90) -> int:
    """
    Delete entries older than retention_days. Returns rows deleted.
    Intended for dream_consolidator nightly run.
    """
    cutoff = date.today() - timedelta(days=retention_days)
    conn = await _get_conn()
    try:
        result = await conn.execute(
            f"DELETE FROM {_SCHEMA}.episodic_index WHERE timestamp_bucket < $1",
            cutoff,
        )
        # result is like "DELETE 42"
        count = int(result.split()[-1]) if result else 0
        return count
    finally:
        await conn.close()


async def episodic_stats(agent: str) -> dict[str, Any]:
    """Return basic stats for an agent's episodic index."""
    conn = await _get_conn()
    try:
        row = await conn.fetchrow(
            f"""
            SELECT
                COUNT(*) AS total_entries,
                COUNT(DISTINCT context_hash) AS unique_contexts,
                MIN(timestamp_bucket) AS oldest_bucket,
                MAX(timestamp_bucket) AS newest_bucket
            FROM {_SCHEMA}.episodic_index
            WHERE agent = $1
            """,
            agent,
        )
        return dict(row) if row else {}
    finally:
        await conn.close()
