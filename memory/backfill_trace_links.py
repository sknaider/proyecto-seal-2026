#!/usr/bin/env python3
"""Backfill: link orphan reasoning_traces to memories by semantic similarity.

Finds traces without linked_memory_ids, embeds their task+conclusion,
searches Qdrant for similar memories, and creates INFORMED edges in Neo4j.

Usage:
    python3 backfill_trace_links.py [--dry-run] [--limit N] [--threshold 0.65]
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from embeddings import get_embedding

VENV_PYTHON = "/home/dadito/IA/seal-spark/.venv/bin/python3"
from config import settings

DB_URL = settings.pg_dsn
NEO4J_URI = settings.neo4j_uri
NEO4J_AUTH = settings.neo4j_auth
COLLECTION = "soul_memories"


async def main():
    dry_run = "--dry-run" in sys.argv
    limit = 500
    threshold = 0.65
    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
        if arg == "--threshold" and i + 1 < len(sys.argv):
            threshold = float(sys.argv[i + 1])

    import asyncpg
    from neo4j import AsyncGraphDatabase
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=3)
    qdrant = AsyncQdrantClient(host="localhost", port=6333)
    neo_driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)

    # Get orphan traces
    async with pool.acquire() as conn:
        traces = await conn.fetch(
            """SELECT id, agent, task, conclusion, premises, created_at
               FROM reasoning_traces
               WHERE linked_memory_ids IS NULL OR array_length(linked_memory_ids, 1) IS NULL
               ORDER BY created_at DESC LIMIT $1""",
            limit,
        )

    print(f"{'[DRY-RUN] ' if dry_run else ''}Found {len(traces)} orphan traces to backfill")
    total_linked = 0
    total_edges = 0

    for t in traces:
        search_text = f"{t['task']} {t['conclusion']}"
        try:
            vec = await get_embedding(search_text)
        except Exception as e:
            print(f"  ⚠ Trace #{t['id']}: embedding error: {e}")
            continue

        must_filters = [FieldCondition(key="agent", match=MatchValue(value=t["agent"]))]
        must_not = [FieldCondition(key="invalid", match=MatchValue(value=True))]
        hits = await qdrant.query_points(
            collection_name=COLLECTION,
            query=vec,
            query_filter=Filter(must=must_filters, must_not=must_not),
            limit=5,
            score_threshold=threshold,
            with_payload=True,
        )

        mem_ids = [p.id for p in hits.points]
        if not mem_ids:
            continue

        total_linked += 1
        total_edges += len(mem_ids)

        if not dry_run:
            # Update PG
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE reasoning_traces SET linked_memory_ids = $1 WHERE id = $2",
                    mem_ids, t["id"],
                )
            # Create Neo4j edges
            async with neo_driver.session() as session:
                # Ensure Trace node exists
                await session.run(
                    "MERGE (t:Trace {trace_id: $tid}) "
                    "SET t.agent = $agent, t.task = $task",
                    tid=t["id"], agent=t["agent"], task=t["task"][:200],
                )
                for mid in mem_ids:
                    await session.run(
                        "MATCH (t:Trace {trace_id: $tid}), (m:Memory {memory_id: $mid}) "
                        "MERGE (m)-[:INFORMED]->(t)",
                        tid=t["id"], mid=mid,
                    )

        scores = [f"{p.score:.3f}" for p in hits.points]
        print(f"  ✅ Trace #{t['id']} ({t['agent']}): {len(mem_ids)} memories linked (scores: {', '.join(scores)})")

    print(f"\n{'[DRY-RUN] ' if dry_run else ''}Summary: {total_linked}/{len(traces)} traces linked, {total_edges} INFORMED edges {'would be ' if dry_run else ''}created")

    await neo_driver.close()
    await qdrant.close()
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
