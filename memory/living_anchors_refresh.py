#!/usr/bin/env python3
"""Identity Continuity v2 Phase 2 — Living Anchors refresh.

The daemon keeps soul_v3.recovery_anchors synchronized with recent high-value
team/shared memories. It is additive: it inserts candidate/approved/retired
anchor rows and never mutates memory content or deletes chat/history.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from embeddings import get_embedding  # noqa: E402
from seal_secrets import pg_dsn  # noqa: E402


CANDIDATE_QUERY = """
    SELECT id, agent, scope, category, importance, content, created_at
    FROM soul_v3.memories
    WHERE invalid_at IS NULL
      AND importance >= 9
      AND scope IN ('team', 'shared')
      AND category IN ('core', 'decision', 'milestone', 'correction')
      AND created_at >= now() - interval '7 days'
    ORDER BY importance DESC, created_at DESC
"""

TOKEN_RE = re.compile(r"[a-z0-9áéíóúñü]+", re.IGNORECASE)
STOPWORDS = {
    "que", "una", "uno", "con", "para", "por", "del", "las", "los", "como",
    "esta", "este", "esto", "esa", "ese", "eso", "the", "and", "with", "from",
    "william", "matrix",
}


@dataclass
class RefreshStats:
    run_id: str
    candidates_seen: int = 0
    candidates_inserted: int = 0
    approved: int = 0
    retired: int = 0
    self_test_ok: bool = False


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in TOKEN_RE.findall(text or "") if len(t) > 2 and t.lower() not in STOPWORDS}


def _overlap_score(a: str, b: str) -> float:
    left = _tokens(a)
    right = _tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, min(len(left), len(right)))


async def _log_evaluation_run(conn: asyncpg.Connection, stats: RefreshStats, passed: bool, notes: str) -> None:
    try:
        await conn.execute(
            """
            INSERT INTO soul_v3.evaluation_runs
                (suite_name, score, passed, evidence, details, agent, run_at, notes)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, now(), $7)
            """,
            "identity_continuity_v2_phase2_living_anchors",
            100 if passed else 0,
            passed,
            f"run_id={stats.run_id} candidates_inserted={stats.candidates_inserted} approved={stats.approved} retired={stats.retired}",
            json.dumps(stats.__dict__),
            "ADA",
            notes,
        )
    except Exception:
        # Observability must not make the daemon fail in older schemas.
        pass


async def insert_candidates(conn: asyncpg.Connection) -> int:
    rows = await conn.fetch(CANDIDATE_QUERY)
    inserted = 0
    for row in rows:
        result = await conn.execute(
            """
            INSERT INTO soul_v3.recovery_anchors
                (memory_id, source_event, state, reason, metadata)
            VALUES ($1, 'living_anchors_refresh', 'candidate', $2, $3::jsonb)
            ON CONFLICT DO NOTHING
            """,
            int(row["id"]),
            "recent_high_value_team_memory",
            json.dumps({
                "agent": row["agent"],
                "scope": row["scope"],
                "category": row["category"],
                "importance": row["importance"],
            }),
        )
        if result.endswith(" 1"):
            inserted += 1
    return inserted


async def _best_william_overlap(
    conn: asyncpg.Connection,
    content: str,
    created_at: datetime,
) -> tuple[float, int | None]:
    rows = await conn.fetch(
        """
        SELECT id, content
        FROM soul_v3.chat_messages
        WHERE sender_name = 'William'
          AND created_at BETWEEN ($1::timestamptz - interval '1 hour')
                             AND ($1::timestamptz + interval '1 hour')
        ORDER BY created_at DESC
        LIMIT 80
        """,
        created_at,
    )
    best_score = 0.0
    best_id: int | None = None
    for row in rows:
        score = _overlap_score(content, row["content"] or "")
        if score > best_score:
            best_score = score
            best_id = int(row["id"])
    return best_score, best_id


async def approve_candidates(conn: asyncpg.Connection, *, audit_window_hours: int = 24) -> int:
    rows = await conn.fetch(
        """
        SELECT a.id AS anchor_id, a.memory_id, a.created_at AS anchor_created_at,
               m.content, m.created_at AS memory_created_at
        FROM soul_v3.recovery_anchors a
        JOIN soul_v3.memories m ON m.id = a.memory_id
        WHERE a.state = 'candidate'
          AND a.source_event = 'living_anchors_refresh'
          AND a.created_at <= now() - ($1::text || ' hours')::interval
          AND m.invalid_at IS NULL
        ORDER BY a.created_at
        """,
        str(audit_window_hours),
    )
    approved = 0
    for row in rows:
        score, chat_id = await _best_william_overlap(conn, row["content"] or "", row["memory_created_at"])
        if score <= 0.30:
            continue
        await conn.execute(
            """
            UPDATE soul_v3.recovery_anchors
            SET state = 'approved',
                promoted_at = now(),
                reason = $2,
                metadata = metadata || $3::jsonb
            WHERE id = $1 AND state = 'candidate'
            """,
            int(row["anchor_id"]),
            "auto_approved_william_overlap",
            json.dumps({"william_chat_id": chat_id, "bm25_overlap_score": round(score, 4)}),
        )
        approved += 1
    return approved


async def retire_invalidated(conn: asyncpg.Connection) -> int:
    result = await conn.execute(
        """
        UPDATE soul_v3.recovery_anchors a
        SET state = 'retired',
            retired_at = now(),
            reason = COALESCE(a.reason, 'source_memory_invalidated')
        FROM soul_v3.memories m
        WHERE a.memory_id = m.id
          AND a.state IN ('candidate', 'approved')
          AND (m.invalid_at IS NOT NULL OR a.metadata->>'outdated' = 'true')
        """
    )
    return int(result.split()[-1])


async def refresh(conn: asyncpg.Connection, *, audit_window_hours: int = 24) -> RefreshStats:
    stats = RefreshStats(run_id=str(uuid.uuid4()))
    stats.candidates_seen = await conn.fetchval(f"SELECT COUNT(*) FROM ({CANDIDATE_QUERY}) q")
    stats.candidates_inserted = await insert_candidates(conn)
    stats.approved = await approve_candidates(conn, audit_window_hours=audit_window_hours)
    stats.retired = await retire_invalidated(conn)
    await _log_evaluation_run(conn, stats, True, "living anchors refresh completed")
    return stats


async def self_test(conn: asyncpg.Connection) -> RefreshStats:
    stats = RefreshStats(run_id="self-test-" + str(uuid.uuid4()))
    phrase = (
        "que nombre tecnico podriamos llamarlo para los academicos "
        "identity continuity living anchors synthetic"
    )
    async with conn.transaction():
        emb = await get_embedding(phrase)
        memory_id = await conn.fetchval(
            """
            INSERT INTO soul_v3.memories
                (agent, scope, category, content, importance, source, embedding, metadata)
            VALUES ('ADA', 'team', 'milestone', $1, 9, 'living_anchors_self_test', $2::vector, $3::jsonb)
            RETURNING id
            """,
            phrase,
            json.dumps(emb),
            json.dumps({"synthetic": True, "run_id": stats.run_id}),
        )
        await conn.execute(
            """
            INSERT INTO soul_v3.chat_messages
                (sender_type, sender_name, channel, message_type, content, metadata)
            VALUES ('agent', 'William', 'web_chat', 'synthetic_test', $1, $2::jsonb)
            """,
            phrase,
            json.dumps({"synthetic": True, "run_id": stats.run_id}),
        )
        await conn.execute(
            """
            INSERT INTO soul_v3.recovery_anchors
                (memory_id, source_event, state, reason, created_at, metadata)
            VALUES ($1, 'living_anchors_refresh', 'candidate', 'self_test_candidate',
                    now() - interval '25 hours', $2::jsonb)
            """,
            memory_id,
            json.dumps({"synthetic": True, "run_id": stats.run_id}),
        )
        stats.approved = await approve_candidates(conn, audit_window_hours=24)
        approved_state = await conn.fetchval(
            "SELECT state FROM soul_v3.recovery_anchors WHERE memory_id = $1",
            memory_id,
        )
        if approved_state != "approved" or stats.approved < 1:
            raise RuntimeError(f"self-test failed: state={approved_state} approved={stats.approved}")
        stats.self_test_ok = True
        raise asyncpg.PostgresError("rollback_self_test")


async def run_self_test(conn: asyncpg.Connection) -> RefreshStats:
    try:
        return await self_test(conn)
    except asyncpg.PostgresError as exc:
        if str(exc) == "rollback_self_test":
            stats = RefreshStats(run_id="self-test-rolled-back", self_test_ok=True, approved=1)
            await _log_evaluation_run(conn, stats, True, "living anchors self-test passed and rolled back")
            return stats
        raise


def _print_stats(stats: RefreshStats) -> None:
    print(
        "living_anchors_refresh=ok "
        f"run_id={stats.run_id} "
        f"candidates_seen={stats.candidates_seen} "
        f"candidates_inserted={stats.candidates_inserted} "
        f"approved={stats.approved} "
        f"retired={stats.retired} "
        f"self_test_ok={stats.self_test_ok}"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh SOUL living recovery anchors")
    parser.add_argument("--apply", action="store_true", help="write candidates/promotions")
    parser.add_argument("--self-test", action="store_true", help="run synthetic candidate to approved test in rollback transaction")
    parser.add_argument("--audit-window-hours", type=int, default=24)
    args = parser.parse_args()

    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        if args.self_test:
            stats = await run_self_test(conn)
        elif args.apply:
            stats = await refresh(conn, audit_window_hours=args.audit_window_hours)
        else:
            seen = await conn.fetchval(f"SELECT COUNT(*) FROM ({CANDIDATE_QUERY}) q")
            stats = RefreshStats(run_id="dry-run", candidates_seen=int(seen or 0))
    finally:
        await conn.close()

    _print_stats(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
