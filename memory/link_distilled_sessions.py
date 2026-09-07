#!/usr/bin/env python3
"""Link orphan distilled_exchanges rows to sessions by agent/time window.

Default mode is dry-run. Use --apply to update session_id.
This script only updates distilled_exchanges.session_id; it does not delete,
rewrite, or invalidate memory content.
"""
from __future__ import annotations
from seal_secrets import pg_dsn

import argparse
import asyncio

import asyncpg


DB_URL = pg_dsn(required=True)


SCOPE_SQL = """
WITH candidates AS (
    SELECT
        de.id,
        de.agent,
        s.id AS session_id,
        row_number() OVER (
            PARTITION BY de.id
            ORDER BY s.started_at DESC, s.id DESC
        ) AS rn
    FROM soul_v3.distilled_exchanges de
    JOIN soul_v3.sessions s
      ON s.agent = de.agent
     AND de.exchange_time BETWEEN s.started_at AND COALESCE(s.ended_at, now())
    WHERE de.session_id IS NULL
      AND ($1::text IS NULL OR de.agent = $1)
)
SELECT agent, count(*) FILTER (WHERE rn = 1) AS linkable
FROM candidates
GROUP BY agent
ORDER BY agent
"""


UPDATE_SQL = """
WITH candidates AS (
    SELECT
        de.id AS distill_id,
        s.id AS session_id,
        row_number() OVER (
            PARTITION BY de.id
            ORDER BY s.started_at DESC, s.id DESC
        ) AS rn
    FROM soul_v3.distilled_exchanges de
    JOIN soul_v3.sessions s
      ON s.agent = de.agent
     AND de.exchange_time BETWEEN s.started_at AND COALESCE(s.ended_at, now())
    WHERE de.session_id IS NULL
      AND ($1::text IS NULL OR de.agent = $1)
),
chosen AS (
    SELECT distill_id, session_id
    FROM candidates
    WHERE rn = 1
)
UPDATE soul_v3.distilled_exchanges de
SET session_id = chosen.session_id
FROM chosen
WHERE de.id = chosen.distill_id
RETURNING de.agent, de.id, de.session_id
"""


async def main_async(agent: str | None, apply: bool) -> None:
    conn = await asyncpg.connect(DB_URL)
    try:
        scope_rows = await conn.fetch(SCOPE_SQL, agent)
        total = sum(int(row["linkable"]) for row in scope_rows)

        mode = "APPLY" if apply else "DRY RUN"
        print(f"link_distilled_sessions mode={mode} agent={agent or 'ALL'}")
        print(f"scope.total_linkable={total}")
        for row in scope_rows:
            print(f"scope.{row['agent']}={row['linkable']}")

        if not apply:
            print("No changes made. Re-run with --apply to update session_id.")
            return

        rows = await conn.fetch(UPDATE_SQL, agent)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["agent"]] = counts.get(row["agent"], 0) + 1

        print(f"updated.total={len(rows)}")
        for agent_name in sorted(counts):
            print(f"updated.{agent_name}={counts[agent_name]}")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", help="Optional agent filter, e.g. ADA or JARVIS")
    parser.add_argument("--apply", action="store_true", help="Update matching session_id values")
    args = parser.parse_args()

    agent = args.agent.upper() if args.agent else None
    asyncio.run(main_async(agent, args.apply))


if __name__ == "__main__":
    main()
