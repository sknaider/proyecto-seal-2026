#!/usr/bin/env python3
"""Durably reconcile canonical PostgreSQL memories into Neo4j.

The worker is idempotent (MERGE by memory_id), never deletes graph or PG data,
and keeps failed rows with bounded retry metadata for operator inspection.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

import asyncpg
from neo4j import AsyncGraphDatabase

from config import settings

LOG = logging.getLogger("seal-graph-outbox")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

ROOT = Path(__file__).resolve().parents[1]
CRED_PATH = Path(os.environ.get("SEAL_GRAPH_SYNC_DB_CRED", ROOT / ".seal_graph_sync_runtime_cred"))


def _dsn() -> str:
    try:
        value = CRED_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"graph-sync credential unavailable: {CRED_PATH}") from exc
    if not value:
        raise RuntimeError(f"graph-sync credential empty: {CRED_PATH}")
    return value


async def _claim(conn: asyncpg.Connection):
    async with conn.transaction():
        row = await conn.fetchrow(
            """SELECT id, memory_id, payload, attempts
               FROM soul_v3.memory_graph_outbox
               WHERE status IN ('pending','error') AND next_attempt_at <= NOW()
               ORDER BY id
               FOR UPDATE SKIP LOCKED LIMIT 1"""
        )
        if row is None:
            return None
        await conn.execute(
            """UPDATE soul_v3.memory_graph_outbox
               SET status='processing', attempts=attempts+1, updated_at=NOW()
               WHERE id=$1""",
            row["id"],
        )
        return row


async def _mark_applied(conn: asyncpg.Connection, outbox_id: int) -> None:
    await conn.execute(
        """UPDATE soul_v3.memory_graph_outbox
           SET status='applied', processed_at=NOW(), updated_at=NOW(), last_error=NULL
           WHERE id=$1""",
        outbox_id,
    )


async def _mark_error(conn: asyncpg.Connection, outbox_id: int, error: Exception) -> None:
    await conn.execute(
        """UPDATE soul_v3.memory_graph_outbox
           SET status='error', updated_at=NOW(), last_error=$2,
               next_attempt_at=NOW() +
                 (LEAST(3600, 30 * power(2, LEAST(attempts, 7))) * INTERVAL '1 second')
           WHERE id=$1""",
        outbox_id,
        str(error)[:500],
    )


async def reconcile(batch: int = 200) -> dict[str, int]:
    conn = await asyncpg.connect(_dsn(), server_settings={"search_path": "soul_v3"})
    driver = AsyncGraphDatabase.driver(settings.neo4j_uri, auth=settings.neo4j_auth)
    counts = {"claimed": 0, "applied": 0, "errors": 0}
    try:
        for _ in range(max(1, min(batch, 2000))):
            row = await _claim(conn)
            if row is None:
                break
            counts["claimed"] += 1
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            try:
                async with driver.session() as session:
                    await session.run(
                        "MERGE (m:Memory {memory_id:$mid}) "
                        "SET m.agent=$agent, m.category=$category, "
                        "m.content=$content, m.importance=$importance",
                        mid=int(row["memory_id"]),
                        agent=str(payload.get("agent", "")),
                        category=str(payload.get("category", "")),
                        content=str(payload.get("content", ""))[:500],
                        importance=int(payload.get("importance", 5)),
                    )
                await _mark_applied(conn, int(row["id"]))
                counts["applied"] += 1
            except Exception as exc:
                await _mark_error(conn, int(row["id"]), exc)
                counts["errors"] += 1
                LOG.warning("graph outbox retry retained id=%s memory=%s: %s",
                            row["id"], row["memory_id"], str(exc)[:180])
    finally:
        await driver.close()
        await conn.close()
    return counts


async def backfill_missing() -> dict[str, int]:
    """Queue valid PG memories absent from Neo4j; does not delete either side."""
    conn = await asyncpg.connect(_dsn(), server_settings={"search_path": "soul_v3"})
    driver = AsyncGraphDatabase.driver(settings.neo4j_uri, auth=settings.neo4j_auth)
    try:
        rows = await conn.fetch(
            """SELECT id, agent, category, content, importance
               FROM soul_v3.memories WHERE invalid_at IS NULL ORDER BY id"""
        )
        async with driver.session() as session:
            result = await session.run("MATCH (m:Memory) WHERE m.memory_id IS NOT NULL RETURN m.memory_id AS id")
            graph_ids = {int(record["id"]) async for record in result if record["id"] is not None}
        missing = [row for row in rows if int(row["id"]) not in graph_ids]
        if missing:
            await conn.executemany(
                """INSERT INTO soul_v3.memory_graph_outbox
                     (memory_id, operation, payload, status, attempts, next_attempt_at, updated_at)
                   VALUES ($1, 'upsert_memory', $2::jsonb, 'pending', 0, NOW(), NOW())
                   ON CONFLICT (memory_id) DO NOTHING""",
                [
                    (
                        int(row["id"]),
                        json.dumps({
                            "agent": row["agent"], "category": row["category"],
                            "content": (row["content"] or "")[:500],
                            "importance": int(row["importance"] or 5),
                        }),
                    )
                    for row in missing
                ],
            )
        return {"valid_pg": len(rows), "graph_ids": len(graph_ids), "queued_missing": len(missing)}
    finally:
        await driver.close()
        await conn.close()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=200)
    parser.add_argument("--backfill-missing", action="store_true")
    args = parser.parse_args()
    if args.backfill_missing:
        print(json.dumps(await backfill_missing(), sort_keys=True))
    print(json.dumps(await reconcile(args.batch), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

