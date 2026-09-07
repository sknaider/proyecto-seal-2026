#!/usr/bin/env python3
"""Deterministic batch archiver for invalidated SOUL memories.

Default mode is read-only dry-run. Apply mode requires an exact confirmation
token printed by dry-run, because moving rows out of memories is destructive
without an explicit rollback step.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncpg
from seal_secrets import pg_dsn

ARCHIVE_LOCK_ID = 0x5EA1_ADA1


def confirm_token(agent: str, count: int, min_age_days: int, limit: int) -> str:
    return f"ARCHIVE_INVALIDATED_{agent.upper()}_{count}_OLDER_{min_age_days}D_LIMIT_{limit}"


def confirm_token_for_ids(agent: str, ids: list[int], min_age_days: int) -> str:
    joined = "_".join(str(item) for item in sorted(ids))
    return f"ARCHIVE_INVALIDATED_{agent.upper()}_IDS_{joined}_OLDER_{min_age_days}D"


def parse_ids(value: str) -> list[int]:
    if not value.strip():
        return []
    ids: list[int] = []
    for part in value.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            parsed = int(item)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid id {item!r}") from exc
        if parsed <= 0:
            raise argparse.ArgumentTypeError(f"invalid id {item!r}")
        ids.append(parsed)
    return sorted(set(ids))


async def fetch_candidates(
    conn: asyncpg.Connection,
    *,
    agent: str,
    min_age_days: int,
    limit: int,
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT id, agent, category, content, importance, source, scope,
               metadata, embedding, invalid_at, created_at, updated_at,
               valid_from, event_time, memory_type
        FROM soul_v3.memories
        WHERE agent = $1
          AND invalid_at IS NOT NULL
          AND invalid_at < now() - ($2::int * interval '1 day')
        ORDER BY invalid_at ASC, id ASC
        LIMIT $3
        """,
        agent,
        min_age_days,
        limit,
    )


async def fetch_candidates_by_ids(
    conn: asyncpg.Connection,
    *,
    agent: str,
    min_age_days: int,
    ids: list[int],
) -> tuple[list[asyncpg.Record], list[int]]:
    if not ids:
        return [], []
    rows = await conn.fetch(
        """
        SELECT id, agent, category, content, importance, source, scope,
               metadata, embedding, invalid_at, created_at, updated_at,
               valid_from, event_time, memory_type
        FROM soul_v3.memories
        WHERE agent = $1
          AND id = ANY($2::bigint[])
          AND invalid_at IS NOT NULL
          AND invalid_at < now() - ($3::int * interval '1 day')
        ORDER BY invalid_at ASC, id ASC
        """,
        agent,
        ids,
        min_age_days,
    )
    found = {int(row["id"]) for row in rows}
    missing = [item for item in ids if item not in found]
    return list(rows), missing


def _json_default(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _row_metadata(row: asyncpg.Record, *, reason: str) -> str:
    original = {
        "id": row["id"],
        "agent": row["agent"],
        "category": row["category"],
        "importance": row["importance"],
        "source": row["source"],
        "scope": row["scope"],
        "invalid_at": row["invalid_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "valid_from": row["valid_from"],
        "event_time": row["event_time"],
        "memory_type": row["memory_type"],
        "metadata": row["metadata"],
    }
    return json.dumps(
        {
            "migrated_by": "memory_archive_batch",
            "reason": reason,
            "rollback": "restore original row from this cold_archive record before deleting archive row",
            "original": original,
        },
        ensure_ascii=False,
        default=_json_default,
    )


async def archive_batch(
    conn: asyncpg.Connection,
    rows: list[asyncpg.Record],
    *,
    reason: str,
    ttl_days: int,
) -> dict[str, int]:
    archived = 0
    deleted_connections = 0
    deleted_memories = 0
    expires_expr = "now() + ($8::int * interval '1 day')" if ttl_days > 0 else "NULL"
    ids = [int(r["id"]) for r in rows]
    agent = rows[0]["agent"] if rows else "SYSTEM"

    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", ARCHIVE_LOCK_ID)
        await conn.execute(
            """
            INSERT INTO soul_v3.event_log (agent, event_type, content, metadata)
            VALUES ($1, 'memory_store', $2, $3::jsonb)
            """,
            agent,
            "memory_archive_batch apply starting",
            json.dumps(
                {
                    "reason": reason,
                    "candidate_count": len(rows),
                    "memory_ids": ids,
                    "ttl_days": ttl_days,
                },
                ensure_ascii=False,
            ),
        )
        for row in rows:
            await conn.execute(
                f"""
                INSERT INTO soul_v3.cold_archive
                    (agent, original_memory_ids, summary, embedding, source_count,
                     importance_max, category, archived_at, expires_at, metadata)
                VALUES ($1, ARRAY[$2::bigint], $3, $4, 1, $5, $6, now(),
                        {expires_expr}, $7::jsonb)
                """,
                row["agent"],
                row["id"],
                row["content"] or "",
                row["embedding"],
                row["importance"],
                row["category"],
                _row_metadata(row, reason=reason),
                ttl_days,
            )
            archived += 1

        del_conn = await conn.execute(
            """
            DELETE FROM soul_v3.memory_connections
            WHERE source_id = ANY($1::bigint[]) OR target_id = ANY($1::bigint[])
            """,
            ids,
        )
        deleted_connections = int(del_conn.split()[-1]) if del_conn else 0

        del_mem = await conn.execute(
            "DELETE FROM soul_v3.memories WHERE id = ANY($1::bigint[])",
            ids,
        )
        deleted_memories = int(del_mem.split()[-1]) if del_mem else 0

        await conn.execute(
            """
            INSERT INTO soul_v3.event_log (agent, event_type, content, metadata)
            VALUES ($1, 'memory_store', $2, $3::jsonb)
            """,
            agent,
            "memory_archive_batch applied",
            json.dumps(
                {
                    "reason": reason,
                    "archived": archived,
                    "deleted_memories": deleted_memories,
                    "deleted_connections": deleted_connections,
                    "memory_ids": ids,
                    "ttl_days": ttl_days,
                },
                ensure_ascii=False,
            ),
        )

    return {
        "archived": archived,
        "deleted_memories": deleted_memories,
        "deleted_connections": deleted_connections,
    }


async def main_async(args: argparse.Namespace) -> int:
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        explicit_ids = parse_ids(args.ids)
        missing_ids: list[int] = []
        if explicit_ids:
            rows, missing_ids = await fetch_candidates_by_ids(
                conn,
                agent=args.agent,
                min_age_days=args.min_age_days,
                ids=explicit_ids,
            )
        else:
            rows = await fetch_candidates(
                conn,
                agent=args.agent,
                min_age_days=args.min_age_days,
                limit=args.limit,
            )
        count = len(rows)
        token = (
            confirm_token_for_ids(args.agent, explicit_ids, args.min_age_days)
            if explicit_ids
            else confirm_token(args.agent, count, args.min_age_days, args.limit)
        )
        sample = [
            {
                "id": int(r["id"]),
                "category": r["category"],
                "importance": r["importance"],
                "invalid_at": r["invalid_at"],
                "preview": " ".join((r["content"] or "").split())[:160],
            }
            for r in rows[: args.sample_limit]
        ]
        report: dict[str, Any] = {
            "mode": "apply" if args.apply else "dry_run",
            "agent": args.agent,
            "min_age_days": args.min_age_days,
            "limit": args.limit,
            "ids": explicit_ids,
            "missing_or_ineligible_ids": missing_ids,
            "candidate_count_in_batch": count,
            "confirm_token_required": token,
            "sample": sample,
        }
        if not args.apply:
            print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
            return 0
        if args.confirm != token:
            report["error"] = "confirmation token mismatch"
            print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
            return 2
        if missing_ids:
            report["error"] = "some explicit ids were missing or ineligible"
            print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
            return 3
        if count == 0:
            report["result"] = {"archived": 0, "deleted_memories": 0, "deleted_connections": 0}
            print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
            return 0
        report["result"] = await archive_batch(
            conn,
            rows,
            reason=args.reason,
            ttl_days=args.ttl_days,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
        return 0
    finally:
        await conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, help="Agent to archive, one batch at a time")
    parser.add_argument("--min-age-days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--ids", default="", help="Comma-separated explicit memory ids to archive")
    parser.add_argument("--sample-limit", type=int, default=5)
    parser.add_argument("--ttl-days", type=int, default=365, help="0 means never expires")
    parser.add_argument("--reason", default="invalidated_older_than_retention")
    parser.add_argument("--apply", action="store_true", help="Move rows to cold_archive and delete hot rows")
    parser.add_argument("--confirm", default="", help="Exact token printed by dry-run")
    return parser


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
