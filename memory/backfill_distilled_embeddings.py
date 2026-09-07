#!/usr/bin/env python3
"""Backfill pgvector embeddings for soul_v3.distilled_exchanges.

Default mode is dry-run. Use --apply to write embeddings.
Only updates distilled_exchanges.embedding for rows where it is NULL.
"""
from __future__ import annotations
from seal_secrets import pg_dsn

import argparse
import asyncio
import json

import asyncpg

from embeddings import get_embedding


DB_URL = pg_dsn(required=True)


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute("""
        ALTER TABLE soul_v3.distilled_exchanges
        ADD COLUMN IF NOT EXISTS embedding vector(768)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_distilled_exchanges_embedding_hnsw
        ON soul_v3.distilled_exchanges
        USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
    """)


async def main_async(agent: str | None, batch_size: int, max_rows: int | None, apply: bool) -> None:
    conn = await asyncpg.connect(DB_URL)
    try:
        await ensure_schema(conn)

        scope_rows = await conn.fetch("""
            SELECT agent, count(*) AS missing
            FROM soul_v3.distilled_exchanges
            WHERE embedding IS NULL
              AND COALESCE(exchange_core, summary, specific_context) IS NOT NULL
              AND ($1::text IS NULL OR agent = $1)
            GROUP BY agent
            ORDER BY agent
        """, agent)
        total = sum(int(row["missing"]) for row in scope_rows)

        print(f"backfill_distilled_embeddings mode={'APPLY' if apply else 'DRY RUN'} agent={agent or 'ALL'}")
        print(f"scope.missing={total}")
        for row in scope_rows:
            print(f"scope.{row['agent']}={row['missing']}")

        if not apply:
            print("No changes made. Re-run with --apply to update embeddings.")
            return

        updated = 0
        while True:
            limit = batch_size
            if max_rows is not None:
                remaining = max_rows - updated
                if remaining <= 0:
                    break
                limit = min(limit, remaining)

            rows = await conn.fetch("""
                SELECT id, agent,
                       trim(COALESCE(exchange_core, '') || E'\n' || COALESCE(specific_context, '') || E'\n' || COALESCE(summary, '')) AS text
                FROM soul_v3.distilled_exchanges
                WHERE embedding IS NULL
                  AND COALESCE(exchange_core, summary, specific_context) IS NOT NULL
                  AND ($1::text IS NULL OR agent = $1)
                ORDER BY id
                LIMIT $2
            """, agent, limit)
            if not rows:
                break

            for row in rows:
                emb = await get_embedding(row["text"])
                await conn.execute(
                    "UPDATE soul_v3.distilled_exchanges SET embedding = $1::vector WHERE id = $2",
                    json.dumps(emb),
                    row["id"],
                )
                updated += 1

            print(f"updated.progress={updated}")

        final_missing = await conn.fetchval("""
            SELECT count(*)
            FROM soul_v3.distilled_exchanges
            WHERE embedding IS NULL
              AND COALESCE(exchange_core, summary, specific_context) IS NOT NULL
              AND ($1::text IS NULL OR agent = $1)
        """, agent)
        print(f"updated.total={updated}")
        print(f"remaining.missing={final_missing}")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", help="Optional agent filter")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    agent = args.agent.upper() if args.agent else None
    asyncio.run(main_async(agent, args.batch_size, args.max_rows, args.apply))


if __name__ == "__main__":
    main()
