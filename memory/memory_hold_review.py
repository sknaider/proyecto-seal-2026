#!/usr/bin/env python3
"""Read-only review for high-importance invalidated memories.

This tool does not update, archive, or delete anything. It exists to separate
safe cold-archive candidates from memories that need recovery/canonization.
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


PROTECTED_CATEGORIES = {
    "correction",
    "decision",
    "milestone",
    "operational_anchor",
    "technical_fact",
    "trust",
}


def parse_ids(value: str) -> list[int]:
    if not value.strip():
        return []
    parsed: list[int] = []
    for part in value.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            memory_id = int(item)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid id {item!r}") from exc
        if memory_id <= 0:
            raise argparse.ArgumentTypeError(f"invalid id {item!r}")
        parsed.append(memory_id)
    return sorted(set(parsed))


def _json_default(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _preview(content: str | None, *, show_sensitive: bool) -> str:
    if not show_sensitive:
        return "[redacted; use --show-content for local inspection]"
    return " ".join((content or "").split())[:220]


def _recommendation(row: asyncpg.Record, exact_active_count: int, superseded_active: bool) -> str:
    if exact_active_count > 0 or superseded_active:
        return "CANONICAL_COPY_EXISTS_REVIEW_THEN_ARCHIVE"
    if int(row["importance"] or 0) >= 7:
        return "NEEDS_RECOVERY_OR_OWNER_HOLD"
    return "REVIEW"


async def fetch_hold_rows(
    conn: asyncpg.Connection,
    *,
    agent: str | None,
    ids: list[int],
    min_age_days: int,
    min_importance: int,
    limit: int,
) -> tuple[list[asyncpg.Record], list[int]]:
    if ids:
        rows = await conn.fetch(
            """
            SELECT id, agent, category, content, importance, scope, source,
                   invalid_at, superseded_by, content_hash_sha256,
                   created_at, updated_at, metadata
            FROM soul_v3.memories
            WHERE id = ANY($1::bigint[])
              AND invalid_at IS NOT NULL
              AND invalid_at < now() - ($2::int * interval '1 day')
              AND importance >= $3
              AND ($4::text IS NULL OR agent = $4)
            ORDER BY agent, importance DESC, invalid_at ASC, id ASC
            """,
            ids,
            min_age_days,
            min_importance,
            agent,
        )
        found = {int(row["id"]) for row in rows}
        return list(rows), [item for item in ids if item not in found]

    rows = await conn.fetch(
        """
        SELECT id, agent, category, content, importance, scope, source,
               invalid_at, superseded_by, content_hash_sha256,
               created_at, updated_at, metadata
        FROM soul_v3.memories
        WHERE invalid_at IS NOT NULL
          AND invalid_at < now() - ($1::int * interval '1 day')
          AND importance >= $2
          AND ($3::text IS NULL OR agent = $3)
        ORDER BY agent, importance DESC, invalid_at ASC, id ASC
        LIMIT $4
        """,
        min_age_days,
        min_importance,
        agent,
        limit,
    )
    return list(rows), []


async def review_row(conn: asyncpg.Connection, row: asyncpg.Record, *, show_sensitive: bool) -> dict[str, Any]:
    content_hash = row["content_hash_sha256"]
    exact_matches = []
    if content_hash:
        exact_rows = await conn.fetch(
            """
            SELECT id, agent, category, importance, scope, created_at
            FROM soul_v3.memories
            WHERE invalid_at IS NULL
              AND content_hash_sha256 = $1
              AND (agent = $2 OR scope IN ('team','shared'))
            ORDER BY importance DESC, created_at DESC
            LIMIT 5
            """,
            content_hash,
            row["agent"],
        )
        exact_matches = [
            {
                "id": int(item["id"]),
                "agent": item["agent"],
                "category": item["category"],
                "importance": int(item["importance"] or 0),
                "scope": item["scope"],
                "created_at": item["created_at"],
            }
            for item in exact_rows
        ]

    superseded_target = None
    if row["superseded_by"]:
        target = await conn.fetchrow(
            """
            SELECT id, agent, category, importance, scope, invalid_at, created_at
            FROM soul_v3.memories
            WHERE id = $1
            """,
            int(row["superseded_by"]),
        )
        if target:
            superseded_target = {
                "id": int(target["id"]),
                "agent": target["agent"],
                "category": target["category"],
                "importance": int(target["importance"] or 0),
                "scope": target["scope"],
                "active": target["invalid_at"] is None,
                "created_at": target["created_at"],
            }

    protected_reason = []
    if int(row["importance"] or 0) >= 7:
        protected_reason.append("importance>=7")
    if row["category"] in PROTECTED_CATEGORIES:
        protected_reason.append(f"category={row['category']}")
    if row["scope"] in {"team", "shared", "william"}:
        protected_reason.append(f"scope={row['scope']}")

    superseded_active = bool(superseded_target and superseded_target["active"])
    return {
        "id": int(row["id"]),
        "agent": row["agent"],
        "category": row["category"],
        "importance": int(row["importance"] or 0),
        "scope": row["scope"],
        "source": row["source"],
        "invalid_at": row["invalid_at"],
        "superseded_by": int(row["superseded_by"]) if row["superseded_by"] else None,
        "protected_reason": protected_reason,
        "exact_active_matches": exact_matches,
        "superseded_target": superseded_target,
        "recommendation": _recommendation(row, len(exact_matches), superseded_active),
        "proposed_recovery": {
            "category": row["category"],
            "importance": int(row["importance"] or 0),
            "scope": row["scope"],
            "metadata": {"recovered_from_invalidated_id": int(row["id"]), "hold_review": True},
        },
        "preview": _preview(row["content"], show_sensitive=show_sensitive),
    }


async def main_async(args: argparse.Namespace) -> int:
    ids = parse_ids(args.ids)
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        rows, missing = await fetch_hold_rows(
            conn,
            agent=args.agent,
            ids=ids,
            min_age_days=args.min_age_days,
            min_importance=args.min_importance,
            limit=args.limit,
        )
        reviewed = [await review_row(conn, row, show_sensitive=args.show_content) for row in rows]
        by_recommendation: dict[str, int] = {}
        for item in reviewed:
            rec = str(item["recommendation"])
            by_recommendation[rec] = by_recommendation.get(rec, 0) + 1
        report = {
            "mode": "read_only",
            "agent": args.agent,
            "ids": ids,
            "missing_or_ineligible_ids": missing,
            "min_age_days": args.min_age_days,
            "min_importance": args.min_importance,
            "count": len(reviewed),
            "by_recommendation": by_recommendation,
            "items": reviewed,
            "next_step": "Do not archive HOLD rows until owner audit and William OK; create active recovered canonical memories only with explicit plan.",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
        return 0
    finally:
        await conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default=None)
    parser.add_argument("--ids", default="", help="Comma-separated memory ids to review")
    parser.add_argument("--min-age-days", type=int, default=7)
    parser.add_argument("--min-importance", type=int, default=7)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--show-content", action="store_true", help="Show content previews; omit for sensitive-safe output")
    return parser


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
