#!/usr/bin/env python3
"""Capture baseline metrics before SOUL repair."""
import asyncio
import asyncpg
import json
from datetime import datetime, timezone
from neo4j import GraphDatabase
from qdrant_client import QdrantClient

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
NEO4J_AUTH = ("neo4j", "seal2026soul")

async def capture():
    conn = await asyncpg.connect(DB_URL)
    qdrant = QdrantClient(host="localhost", port=6333)
    neo4j_driver = GraphDatabase.driver("bolt://localhost:7687", auth=NEO4J_AUTH)

    pg_total = await conn.fetchval("SELECT COUNT(*) FROM memories")
    pg_valid = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE metadata->>'invalidated' IS DISTINCT FROM 'true'")
    pg_by_agent = await conn.fetch("SELECT agent, COUNT(*) as c FROM memories GROUP BY agent ORDER BY c DESC")

    info = qdrant.get_collection("soul_memories")
    qdrant_total = info.points_count

    pg_ids = set(r['id'] for r in await conn.fetch("SELECT id FROM memories"))
    result = qdrant.scroll(collection_name="soul_memories", limit=2500, with_payload=False, with_vectors=False)
    qdrant_ids = set(p.id for p in result[0])
    orphan_pg = pg_ids - qdrant_ids
    orphan_qdrant = qdrant_ids - pg_ids

    with neo4j_driver.session() as s:
        neo4j_nodes = s.run("MATCH (n) RETURN COUNT(n)").single()[0]
        neo4j_edges = s.run("MATCH ()-[r]->() RETURN COUNT(r)").single()[0]
        neo4j_orphans = s.run("MATCH (n) WHERE NOT (n)--() RETURN COUNT(n)").single()[0]

    baseline = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pg_total": pg_total,
        "pg_valid": pg_valid,
        "pg_by_agent": {r['agent']: r['c'] for r in pg_by_agent},
        "qdrant_total": qdrant_total,
        "orphan_pg_count": len(orphan_pg),
        "orphan_qdrant_count": len(orphan_qdrant),
        "neo4j_nodes": neo4j_nodes,
        "neo4j_edges": neo4j_edges,
        "neo4j_orphans": neo4j_orphans,
    }

    print(json.dumps(baseline, indent=2))
    with open("/home/dadito/IA/proyecto-seal/memory/docs/plans/repair_baseline.json", "w") as f:
        json.dump(baseline, f, indent=2)

    await conn.close()
    neo4j_driver.close()
    print("\nBaseline saved.")

asyncio.run(capture())
