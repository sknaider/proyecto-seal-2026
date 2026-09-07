#!/usr/bin/env python3
"""Backfill GAM events from soul_v3.agent_tasks.

Default mode is dry-run. Use --apply to insert/update GAM rows.
This is non-destructive: it does not delete tasks or topics.
"""
from __future__ import annotations
from seal_secrets import pg_dsn

import argparse
import asyncio
import json
import os
from datetime import datetime
from typing import Any

import asyncpg


DB_URL = os.environ.get(
    "SEAL_DB_URL",
    pg_dsn(required=True),
)
TOPIC = "TaskList / Agent Tasks"


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def ensure_topic(conn: asyncpg.Connection, agent: str, apply: bool) -> int | None:
    row = await conn.fetchrow(
        """
        SELECT id FROM soul_v3.gam_topics
        WHERE agent=$1 AND topic=$2
        ORDER BY id ASC
        LIMIT 1
        """,
        agent,
        TOPIC,
    )
    if row:
        return int(row["id"])
    if not apply:
        return None
    return int(await conn.fetchval(
        """
        INSERT INTO soul_v3.gam_topics (agent, topic, summary, relevance_score)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        agent,
        TOPIC,
        "Canonical GAM topic mirroring soul_v3.agent_tasks into actionable events.",
        0.8,
    ))


async def sync_task(conn: asyncpg.Connection, task: asyncpg.Record, topic_id: int, apply: bool) -> str:
    existing = await conn.fetchval(
        """
        SELECT id FROM soul_v3.gam_event_graph
        WHERE agent=$1
          AND metadata->>'source'='agent_task'
          AND (metadata->>'task_id')::bigint=$2
        LIMIT 1
        """,
        task["agent"],
        task["id"],
    )
    if not apply:
        return "would_update" if existing else "would_insert"

    metadata = {
        "source": "agent_task",
        "task_id": task["id"],
        "status": task["status"],
        "priority": task["priority"],
        "deadline": task["deadline"].isoformat() if task["deadline"] else None,
        "created_at": task["created_at"].isoformat() if task["created_at"] else None,
        "completed_at": task["completed_at"].isoformat() if task["completed_at"] else None,
    }
    if existing:
        await conn.execute(
            """
            UPDATE soul_v3.gam_event_graph
            SET topic_id=$1, event=$2, event_timestamp=COALESCE($3, event_timestamp), metadata=$4
            WHERE id=$5
            """,
            topic_id,
            task["title"],
            task["created_at"],
            json.dumps(metadata, default=_json_default),
            existing,
        )
        return "updated"

    await conn.execute(
        """
        INSERT INTO soul_v3.gam_event_graph
            (agent, topic_id, event, event_timestamp, related_event_ids, causal_direction, metadata)
        VALUES ($1, $2, $3, COALESCE($4, now()), '{}', 'related', $5)
        """,
        task["agent"],
        topic_id,
        task["title"],
        task["created_at"],
        json.dumps(metadata, default=_json_default),
    )
    return "inserted"


async def reconcile_event_counts(conn: asyncpg.Connection, apply: bool) -> int:
    mismatches = int(await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM soul_v3.gam_topics t
        LEFT JOIN (
            SELECT topic_id, COUNT(*)::int AS real_count
            FROM soul_v3.gam_event_graph
            GROUP BY topic_id
        ) e ON e.topic_id=t.id
        WHERE t.event_count IS DISTINCT FROM COALESCE(e.real_count, 0)
        """
    ))
    if apply:
        await conn.execute(
            """
            UPDATE soul_v3.gam_topics t
            SET event_count = COALESCE(e.real_count, 0),
                last_updated = CASE
                    WHEN COALESCE(e.real_count, 0) > 0 THEN now()
                    ELSE t.last_updated
                END
            FROM (
                SELECT t2.id, COALESCE(COUNT(g.id), 0)::int AS real_count
                FROM soul_v3.gam_topics t2
                LEFT JOIN soul_v3.gam_event_graph g ON g.topic_id=t2.id
                GROUP BY t2.id
            ) e
            WHERE t.id=e.id
              AND t.event_count IS DISTINCT FROM e.real_count
            """
        )
    return mismatches


async def main(apply: bool) -> dict:
    conn = await asyncpg.connect(DB_URL)
    try:
        tasks = await conn.fetch(
            """
            SELECT id, agent, title, description, status, priority, deadline, created_at, completed_at
            FROM soul_v3.agent_tasks
            ORDER BY agent, created_at, id
            """
        )
        by_agent: dict[str, int] = {}
        counts = {
            "tasks_seen": len(tasks),
            "would_insert": 0,
            "would_update": 0,
            "inserted": 0,
            "updated": 0,
            "topics_existing": 0,
            "topics_created_or_needed": 0,
        }
        topic_ids: dict[str, int | None] = {}

        for task in tasks:
            agent = task["agent"]
            by_agent[agent] = by_agent.get(agent, 0) + 1
            if agent not in topic_ids:
                topic_id = await ensure_topic(conn, agent, apply)
                topic_ids[agent] = topic_id
                if topic_id is None:
                    counts["topics_created_or_needed"] += 1
                else:
                    counts["topics_existing"] += 1
            topic_id = topic_ids[agent]
            if topic_id is None and not apply:
                topic_id = -1
            if topic_id is None:
                continue
            result = await sync_task(conn, task, topic_id, apply)
            counts[result] += 1

        counts["event_count_mismatches"] = await reconcile_event_counts(conn, apply)

        after = await conn.fetchrow(
            """
            SELECT
              (SELECT COUNT(*) FROM soul_v3.gam_topics) AS topics,
              (SELECT COUNT(*) FROM soul_v3.gam_event_graph) AS events,
              (SELECT COUNT(*) FROM soul_v3.gam_event_graph WHERE metadata->>'source'='agent_task') AS task_events
            """
        )
        return {"apply": apply, "by_agent": by_agent, "counts": counts, "after": dict(after)}
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="insert/update GAM rows")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(main(args.apply)), indent=2, default=_json_default))
