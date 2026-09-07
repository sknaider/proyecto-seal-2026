#!/usr/bin/env python3
"""SEAL Native Incremental Memory Indexer v1.1 (OpenHuman lifecycle states).

Delta-tracks soul_v3.memories (importance >= 7, active) and maintains
soul_v3.memory_search_idx with up-to-date embeddings.

Only re-embeds rows whose content hash changed since the last run.
Uses lifecycle states (indexing→indexed/failed, dropped tombstones) for
full traceability of what is being processed and what failed.

Usage:
    python3 memory_indexer.py              # run index
    python3 memory_indexer.py --dry-run    # show stats without writing
    python3 memory_indexer.py --history    # show last 5 runs
    python3 memory_indexer.py --full       # ignore hashes, re-embed all
    python3 memory_indexer.py --status     # lifecycle breakdown
    python3 memory_indexer.py --fix-stuck  # reset stuck 'indexing' records
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncpg
from config import settings
from db import get_pool
from embeddings import get_embeddings_batch

ADVISORY_LOCK_KEY = 12345
MIN_IMPORTANCE = 7


@dataclass
class MemoryRow:
    id: int
    agent: str
    memory_type: str
    content: str
    importance: int


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


async def _fetch_source(conn: asyncpg.Connection, agent: str | None = None) -> list[MemoryRow]:
    rows = await conn.fetch("""
        SELECT id, agent, memory_type, content, importance
        FROM soul_v3.memories
        WHERE invalid_at IS NULL AND importance >= $1
          AND ($2::text IS NULL OR agent = $2)
        ORDER BY id
    """, MIN_IMPORTANCE, agent)
    return [MemoryRow(id=r["id"], agent=r["agent"], memory_type=r["memory_type"],
                      content=r["content"], importance=r["importance"]) for r in rows]


async def _fetch_index_state(
    conn: asyncpg.Connection, agent: str | None = None
) -> dict[int, str]:
    rows = await conn.fetch(
        """SELECT s.memory_id, s.content_hash
           FROM soul_v3.index_state s
           JOIN soul_v3.memories m ON m.id = s.memory_id
           WHERE s.lifecycle_status != 'dropped'
             AND ($1::text IS NULL OR m.agent = $1)""",
        agent,
    )
    return {r["memory_id"]: r["content_hash"] for r in rows}


async def _mark_indexing(conn: asyncpg.Connection, rows: list[MemoryRow], run_id: int) -> None:
    """Mark a batch as 'indexing' before embedding starts — detects stuck runs."""
    now = datetime.now(timezone.utc)
    await conn.executemany("""
        INSERT INTO soul_v3.index_state (memory_id, content_hash, indexed_at, run_id, lifecycle_status)
        VALUES ($1, '', $2, $3, 'indexing')
        ON CONFLICT (memory_id) DO UPDATE SET
            lifecycle_status = 'indexing',
            run_id = EXCLUDED.run_id,
            indexed_at = EXCLUDED.indexed_at
    """, [(r.id, now, run_id) for r in rows])


async def _mark_failed(conn: asyncpg.Connection, rows: list[MemoryRow]) -> None:
    """Mark a batch as 'failed' after embedding error — keeps record for debugging."""
    ids = [r.id for r in rows]
    await conn.execute("""
        UPDATE soul_v3.index_state SET lifecycle_status = 'failed'
        WHERE memory_id = ANY($1)
    """, ids)


async def _upsert_indexed(conn: asyncpg.Connection, rows: list[MemoryRow],
                          embeddings: list[list[float]], run_id: int) -> None:
    now = datetime.now(timezone.utc)
    async with conn.transaction():
        await conn.executemany("""
            INSERT INTO soul_v3.memory_search_idx
                (memory_id, agent, memory_type, importance, content, embedding, indexed_at)
            VALUES ($1, $2, $3, $4, $5, $6::vector, $7)
            ON CONFLICT (memory_id) DO UPDATE SET
                agent = EXCLUDED.agent,
                memory_type = EXCLUDED.memory_type,
                importance = EXCLUDED.importance,
                content = EXCLUDED.content,
                embedding = EXCLUDED.embedding,
                indexed_at = EXCLUDED.indexed_at
        """, [(r.id, r.agent, r.memory_type, r.importance, r.content,
               json.dumps(embeddings[i]), now)
              for i, r in enumerate(rows)])

        await conn.executemany("""
            INSERT INTO soul_v3.index_state (memory_id, content_hash, indexed_at, run_id, lifecycle_status)
            VALUES ($1, $2, $3, $4, 'indexed')
            ON CONFLICT (memory_id) DO UPDATE SET
                content_hash = EXCLUDED.content_hash,
                indexed_at = EXCLUDED.indexed_at,
                run_id = EXCLUDED.run_id,
                lifecycle_status = 'indexed'
        """, [(r.id, _hash(r.content), now, run_id) for r in rows])


async def _tombstone_deleted(conn: asyncpg.Connection, memory_ids: set[int]) -> None:
    """Tombstone deleted memories: remove from search index, keep audit record as 'dropped'."""
    if not memory_ids:
        return
    ids_list = list(memory_ids)
    async with conn.transaction():
        await conn.execute(
            "DELETE FROM soul_v3.memory_search_idx WHERE memory_id = ANY($1)", ids_list)
        await conn.execute("""
            UPDATE soul_v3.index_state SET lifecycle_status = 'dropped', indexed_at = NOW()
            WHERE memory_id = ANY($1)
        """, ids_list)


async def _create_run(conn: asyncpg.Connection, triggered_by: str) -> int:
    run_id = await conn.fetchval("""
        INSERT INTO soul_v3.index_runs (triggered_by)
        VALUES ($1) RETURNING id
    """, triggered_by)
    return run_id


async def _complete_run(conn: asyncpg.Connection, run_id: int, *,
                        rows_total: int, rows_indexed: int, rows_unchanged: int,
                        rows_deleted: int, elapsed_ms: int, status: str = "completed",
                        error_msg: str | None = None) -> None:
    await conn.execute("""
        UPDATE soul_v3.index_runs
        SET completed_at = NOW(), status = $2,
            rows_total = $3, rows_indexed = $4, rows_unchanged = $5,
            rows_deleted = $6, elapsed_ms = $7, error_msg = $8
        WHERE id = $1
    """, run_id, status, rows_total, rows_indexed, rows_unchanged,
        rows_deleted, elapsed_ms, error_msg)


async def _log_smg(conn: asyncpg.Connection, status: str, latency_ms: int) -> None:
    try:
        await conn.execute("""
            INSERT INTO soul_v3.smg_audit_log (agent, path, status, latency_ms, backend)
            VALUES ('SYSTEM', 'memory_indexer/run', $1, $2, 'local_sentence_transformers')
        """, status, latency_ms)
    except Exception:
        pass


async def run_index(*, dry_run: bool = False, full_reindex: bool = False,
                    triggered_by: str = "manual", agent: str | None = None,
                    confirm_delete_count: int | None = None) -> dict:
    t0 = time.monotonic()

    pool = await get_pool()
    # Keep the advisory-lock connection acquired for the full run.  Under MCP
    # this is the same caller-scoped least-privilege facade as every other query.
    async with pool.acquire() as lock_conn:
        acquired = await lock_conn.fetchval(
            "SELECT pg_try_advisory_lock($1)", ADVISORY_LOCK_KEY)
        if not acquired:
            return {"status": "skipped", "reason": "another run in progress"}
        try:
            return await _do_index(pool, dry_run=dry_run,
                                   full_reindex=full_reindex,
                                   triggered_by=triggered_by, agent=agent,
                                   confirm_delete_count=confirm_delete_count, t0=t0)
        finally:
            try:
                await lock_conn.execute(
                    "SELECT pg_advisory_unlock($1)", ADVISORY_LOCK_KEY)
            except Exception:
                pass


async def _do_index(pool: asyncpg.Pool, *, dry_run: bool, full_reindex: bool,
                    triggered_by: str, agent: str | None,
                    confirm_delete_count: int | None, t0: float) -> dict:
    async with pool.acquire() as conn:
        source_rows = await _fetch_source(conn, agent)
        existing = {} if full_reindex else await _fetch_index_state(conn, agent)

    source_ids = {r.id for r in source_rows}
    to_embed = [r for r in source_rows if existing.get(r.id) != _hash(r.content)]
    unchanged = [r for r in source_rows if existing.get(r.id) == _hash(r.content)]
    deleted_ids = set(existing.keys()) - source_ids

    if dry_run:
        elapsed = int((time.monotonic() - t0) * 1000)
        return {
            "status": "dry_run",
            "rows_total": len(source_rows),
            "to_embed": len(to_embed),
            "unchanged": len(unchanged),
            "to_delete": len(deleted_ids),
            "elapsed_ms": elapsed,
        }

    if deleted_ids and confirm_delete_count != len(deleted_ids):
        return {
            "status": "blocked",
            "reason": "destructive tombstone count requires exact operator confirmation",
            "to_delete": len(deleted_ids),
            "required_confirm_delete_count": len(deleted_ids),
        }

    async with pool.acquire() as conn:
        run_id = await _create_run(conn, triggered_by)

    try:
        if to_embed:
            async with pool.acquire() as conn:
                await _mark_indexing(conn, to_embed, run_id)
            try:
                texts = [r.content for r in to_embed]
                embeddings = await get_embeddings_batch(texts)
                async with pool.acquire() as conn:
                    await _upsert_indexed(conn, to_embed, embeddings, run_id)
            except Exception:
                async with pool.acquire() as conn:
                    await _mark_failed(conn, to_embed)
                raise

        if deleted_ids:
            async with pool.acquire() as conn:
                await _tombstone_deleted(conn, deleted_ids)

        elapsed = int((time.monotonic() - t0) * 1000)
        async with pool.acquire() as conn:
            await _complete_run(
                conn, run_id,
                rows_total=len(source_rows),
                rows_indexed=len(to_embed),
                rows_unchanged=len(unchanged),
                rows_deleted=len(deleted_ids),
                elapsed_ms=elapsed,
            )
            await _log_smg(conn, "ok", elapsed)

        return {
            "status": "completed",
            "run_id": run_id,
            "rows_total": len(source_rows),
            "rows_indexed": len(to_embed),
            "rows_unchanged": len(unchanged),
            "rows_deleted": len(deleted_ids),
            "elapsed_ms": elapsed,
        }

    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        async with pool.acquire() as conn:
            await _complete_run(conn, run_id, rows_total=len(source_rows),
                                rows_indexed=0, rows_unchanged=0, rows_deleted=0,
                                elapsed_ms=elapsed, status="failed", error_msg=str(exc))
            await _log_smg(conn, "error", elapsed)
        raise


async def get_history(n: int = 5, agent: str | None = None) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
                SELECT id, started_at, status, rows_total, rows_indexed,
                       rows_unchanged, rows_deleted, elapsed_ms, triggered_by
                FROM soul_v3.index_runs
                WHERE ($2::text IS NULL OR triggered_by = 'mcp_tool:' || $2)
                ORDER BY id DESC LIMIT $1
            """, n, agent)
    return [dict(r) for r in rows]


async def get_status(agent: str | None = None) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        last = await conn.fetchrow("""
                SELECT id, started_at, status, rows_indexed, elapsed_ms
                FROM soul_v3.index_runs
                WHERE ($1::text IS NULL OR triggered_by = 'mcp_tool:' || $1)
                ORDER BY id DESC LIMIT 1
            """, agent)
        indexed_count = await conn.fetchval(
            """SELECT COUNT(*)
               FROM soul_v3.memory_search_idx i
               JOIN soul_v3.memories m ON m.id = i.memory_id
               WHERE m.invalid_at IS NULL AND m.importance >= $1
                 AND ($2::text IS NULL OR m.agent = $2)""",
            MIN_IMPORTANCE,
            agent,
        )
        total_eligible = await conn.fetchval("""
                SELECT COUNT(*) FROM soul_v3.memories
                WHERE invalid_at IS NULL AND importance >= $1
                  AND ($2::text IS NULL OR agent = $2)
            """, MIN_IMPORTANCE, agent)
        lifecycle = await conn.fetch("""
                SELECT s.lifecycle_status, COUNT(*) as cnt
                FROM soul_v3.index_state s
                JOIN soul_v3.memories m ON m.id = s.memory_id
                WHERE m.invalid_at IS NULL AND m.importance >= $1
                  AND ($2::text IS NULL OR m.agent = $2)
                GROUP BY s.lifecycle_status ORDER BY s.lifecycle_status
            """, MIN_IMPORTANCE, agent)
    return {
        "agent": agent,
        "last_run": dict(last) if last else None,
        "indexed_count": indexed_count,
        "total_eligible": total_eligible,
        "coverage_pct": round(indexed_count / total_eligible * 100, 1)
                        if total_eligible else 0,
        "lifecycle": {r["lifecycle_status"]: r["cnt"] for r in lifecycle},
    }


async def fix_stuck() -> dict:
    """Reset records stuck in 'indexing' state from crashed runs."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        stuck = await conn.fetchval(
            "SELECT COUNT(*) FROM soul_v3.index_state WHERE lifecycle_status = 'indexing'")
        if stuck:
            await conn.execute("""
                DELETE FROM soul_v3.index_state WHERE lifecycle_status = 'indexing'
            """)
    return {"fixed": stuck, "action": "deleted stuck 'indexing' records (will re-embed on next run)"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEAL Native Memory Indexer")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--full", action="store_true", help="Re-embed all rows")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--status", action="store_true", help="Show lifecycle breakdown")
    parser.add_argument("--fix-stuck", action="store_true", help="Reset stuck 'indexing' records")
    parser.add_argument(
        "--confirm-delete-count",
        type=int,
        default=None,
        help="exact dry-run to_delete count required before tombstoning stale index rows",
    )
    args = parser.parse_args()

    if args.history:
        history = asyncio.run(get_history())
        for r in history:
            print(f"run_id={r['id']} status={r['status']} indexed={r['rows_indexed']} "
                  f"elapsed={r['elapsed_ms']}ms triggered_by={r['triggered_by']}")
        sys.exit(0)

    if args.status:
        result = asyncio.run(get_status())
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0)

    if args.fix_stuck:
        result = asyncio.run(fix_stuck())
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0)

    result = asyncio.run(
        run_index(
            dry_run=args.dry_run,
            full_reindex=args.full,
            confirm_delete_count=args.confirm_delete_count,
        )
    )
    print(json.dumps(result, indent=2, default=str))
