#!/usr/bin/env python3
"""Backfill soul_v3.memories.embedding_bm25 safely, in batches.

Default mode is dry-run. Use --apply only after William confirms the scope.
"""

from __future__ import annotations
from seal_secrets import pg_dsn

import argparse
import asyncio
import os
from typing import Sequence

import asyncpg


DB_URL = os.environ.get(
    "SEAL_PG_DSN",
    pg_dsn(required=True),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill embedding_bm25 for active SOUL memories."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually update rows. Without this flag the script only reports scope.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Rows per UPDATE batch. Default: 1000.",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=0,
        help="Optional cap for this run. 0 means no cap.",
    )
    parser.add_argument(
        "--agent",
        action="append",
        default=[],
        help="Restrict to one agent. Repeat for multiple agents.",
    )
    return parser.parse_args()


def _agent_filter(agents: Sequence[str], start_param: int) -> tuple[str, list[str]]:
    if not agents:
        return "", []
    placeholders = ", ".join(f"${i}" for i in range(start_param, start_param + len(agents)))
    return f" AND agent IN ({placeholders})", [a.upper() for a in agents]


async def report_scope(conn: asyncpg.Connection, agents: Sequence[str]) -> int:
    agent_sql, agent_params = _agent_filter(agents, 1)
    total = await conn.fetchval(
        f"""
        SELECT COUNT(*)
        FROM soul_v3.memories
        WHERE invalid_at IS NULL
          AND embedding_bm25 IS NULL
          {agent_sql}
        """,
        *agent_params,
    )
    rows = await conn.fetch(
        f"""
        SELECT agent, COUNT(*) AS missing
        FROM soul_v3.memories
        WHERE invalid_at IS NULL
          AND embedding_bm25 IS NULL
          {agent_sql}
        GROUP BY agent
        ORDER BY missing DESC, agent
        """,
        *agent_params,
    )
    print(f"scope.active_missing={total}")
    for row in rows:
        print(f"scope.agent.{row['agent']}={row['missing']}")
    return int(total or 0)


async def apply_backfill(
    conn: asyncpg.Connection,
    agents: Sequence[str],
    batch_size: int,
    max_rows: int,
) -> int:
    updated = 0
    while True:
        remaining_cap = max_rows - updated if max_rows else batch_size
        if max_rows and remaining_cap <= 0:
            break
        this_batch = min(batch_size, remaining_cap) if max_rows else batch_size

        agent_sql, agent_params = _agent_filter(agents, 2)
        rows = await conn.fetch(
            f"""
            WITH batch AS (
                SELECT id
                FROM soul_v3.memories
                WHERE invalid_at IS NULL
                  AND embedding_bm25 IS NULL
                  {agent_sql}
                ORDER BY id
                LIMIT $1
            )
            UPDATE soul_v3.memories AS m
            SET embedding_bm25 = to_tsvector('simple', COALESCE(m.content, ''))
            FROM batch
            WHERE m.id = batch.id
            RETURNING m.id
            """,
            this_batch,
            *agent_params,
        )
        count = len(rows)
        updated += count
        print(f"batch.updated={count} total.updated={updated}")
        if count == 0 or count < this_batch:
            break
    return updated


async def main() -> int:
    args = parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be >= 1")
    if args.max_rows < 0:
        raise SystemExit("--max-rows must be >= 0")

    conn = await asyncpg.connect(DB_URL)
    try:
        missing = await report_scope(conn, args.agent)
        if not args.apply:
            print("mode=dry-run")
            print("next=rerun with --apply after explicit William confirmation")
            return 0
        if missing == 0:
            print("mode=apply no-op")
            return 0
        updated = await apply_backfill(conn, args.agent, args.batch_size, args.max_rows)
        print(f"mode=apply updated={updated}")
        await report_scope(conn, args.agent)
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
