#!/usr/bin/env python3
"""Dry-run report for SOUL memory hygiene.

This script is intentionally read-only. It prints counts and representative
samples for archival/dedup candidates so William can approve exact scope before
any destructive or large data movement operation.
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


async def run_report(agent: str | None = None, sample_limit: int = 10) -> dict[str, Any]:
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        agent_filter = "AND agent = $1" if agent else ""
        params = [agent] if agent else []

        summary = await conn.fetchrow(
            f"""
            SELECT
              count(*) AS total,
              count(*) FILTER (WHERE invalid_at IS NULL) AS active,
              count(*) FILTER (WHERE invalid_at IS NOT NULL) AS invalid,
              count(*) FILTER (
                WHERE invalid_at IS NOT NULL
                  AND invalid_at < now() - interval '7 days'
              ) AS invalid_older_7d,
              count(*) FILTER (
                WHERE invalid_at IS NULL
                  AND last_recalled_at IS NULL
                  AND created_at < now() - interval '30 days'
              ) AS active_never_recalled_older_30d,
              count(*) FILTER (
                WHERE invalid_at IS NULL
                  AND decay_score IS NOT NULL
                  AND decay_score < 0.1
                  AND coalesce(last_recalled_at, created_at) < now() - interval '30 days'
              ) AS low_decay_active_older_30d
            FROM soul_v3.memories
            WHERE true {agent_filter}
            """,
            *params,
        )

        by_agent = await conn.fetch(
            """
            SELECT agent,
                   count(*) AS total,
                   count(*) FILTER (WHERE invalid_at IS NULL) AS active,
                   count(*) FILTER (WHERE invalid_at IS NOT NULL) AS invalid,
                   count(*) FILTER (
                     WHERE invalid_at IS NOT NULL
                       AND invalid_at < now() - interval '7 days'
                   ) AS invalid_older_7d
            FROM soul_v3.memories
            GROUP BY agent
            ORDER BY invalid_older_7d DESC, total DESC
            """
        )

        dupes = await conn.fetch(
            f"""
            SELECT agent, content, count(*) AS copies, min(id) AS first_id, max(id) AS last_id
            FROM soul_v3.memories
            WHERE invalid_at IS NULL {agent_filter}
            GROUP BY agent, content
            HAVING count(*) > 1
            ORDER BY copies DESC
            LIMIT {sample_limit}
            """,
            *params,
        )

        invalid_samples = await conn.fetch(
            f"""
            SELECT id, agent, category, importance, invalid_at, left(content, 180) AS preview
            FROM soul_v3.memories
            WHERE invalid_at IS NOT NULL
              AND invalid_at < now() - interval '7 days'
              {agent_filter}
            ORDER BY invalid_at ASC
            LIMIT {sample_limit}
            """,
            *params,
        )
    finally:
        await conn.close()

    return {
        "mode": "dry_run_read_only",
        "agent_filter": agent,
        "summary": dict(summary),
        "by_agent": [dict(r) for r in by_agent],
        "duplicate_active_samples": [dict(r) for r in dupes],
        "invalid_older_7d_samples": [dict(r) for r in invalid_samples],
        "next_step_requires_explicit_approval": (
            "Any archive/move/delete must show exact COUNT and receive William OK."
        ),
    }


def _json_default(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


async def main_async(args: argparse.Namespace) -> int:
    report = await run_report(args.agent, args.sample_limit)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", help="Limit report to one agent")
    parser.add_argument("--sample-limit", type=int, default=10)
    return parser


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
