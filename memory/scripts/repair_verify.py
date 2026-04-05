#!/usr/bin/env python3
"""Verify SOUL integrity after repair."""
import asyncio
import asyncpg
import json
from neo4j import GraphDatabase
from qdrant_client import QdrantClient

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"

async def verify():
    conn = await asyncpg.connect(DB_URL)
    qdrant = QdrantClient(host="localhost", port=6333)
    neo4j = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "seal2026soul"))

    with open("/home/dadito/IA/proyecto-seal/memory/docs/plans/repair_baseline.json") as f:
        baseline = json.load(f)

    checks = []

    # Check 1: PG<>Qdrant sync
    pg_ids = set(r['id'] for r in await conn.fetch("SELECT id FROM memories"))
    result = qdrant.scroll(collection_name="soul_memories", limit=2500, with_payload=False, with_vectors=False)
    qdrant_ids = set(p.id for p in result[0])
    orphans_q = qdrant_ids - pg_ids
    orphans_p = pg_ids - qdrant_ids
    sync_ok = len(orphans_q) == 0 and len(orphans_p) == 0
    checks.append(("PG<>Qdrant sync", sync_ok, f"orphans_q={len(orphans_q)}, orphans_p={len(orphans_p)}"))

    # Check 2: Neo4j orphans reduced
    with neo4j.session() as s:
        neo4j_orphans = s.run("MATCH (n) WHERE NOT (n)--() RETURN COUNT(n)").single()[0]
        neo4j_nodes = s.run("MATCH (n) RETURN COUNT(n)").single()[0]
        neo4j_edges = s.run("MATCH ()-[r]->() RETURN COUNT(r)").single()[0]
    orphan_pct = (neo4j_orphans / neo4j_nodes * 100) if neo4j_nodes > 0 else 0
    orphan_ok = orphan_pct < 10
    checks.append(("Neo4j orphans <10%", orphan_ok, f"{neo4j_orphans}/{neo4j_nodes} ({orphan_pct:.1f}%) was {baseline['neo4j_orphans']}/{baseline['neo4j_nodes']}"))

    # Check 3: Qdrant coverage
    coverage = len(qdrant_ids) / len(pg_ids) * 100 if pg_ids else 0
    count_ok = coverage >= 95
    checks.append(("Qdrant coverage >95%", count_ok, f"{len(qdrant_ids)}/{len(pg_ids)} ({coverage:.1f}%)"))

    # Check 4: Neo4j edges >= baseline
    edges_up = neo4j_edges >= baseline["neo4j_edges"]
    checks.append(("Neo4j edges >= baseline", edges_up, f"{neo4j_edges} vs {baseline['neo4j_edges']} baseline"))

    # Check 5: No ghost nodes (Neo4j nodes without PG match)
    with neo4j.session() as s:
        r = s.run("MATCH (n:Memory) WHERE n.memory_id IS NULL RETURN COUNT(n)")
        null_mids = r.single()[0]
    ghost_ok = null_mids == 0
    checks.append(("No ghost nodes (NULL memory_id)", ghost_ok, f"{null_mids} remaining"))

    # Check 6: Orphan improvement
    improvement = baseline["neo4j_orphans"] - neo4j_orphans
    improved = improvement > 0
    checks.append(("Orphan count reduced", improved, f"reduced by {improvement} ({baseline['neo4j_orphans']} -> {neo4j_orphans})"))

    print("=" * 65)
    print("  SOUL REPAIR VERIFICATION")
    print("=" * 65)
    passed = 0
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        symbol = "+" if ok else "!"
        print(f"  [{symbol}] {name}: {status}")
        print(f"      {detail}")
        if ok:
            passed += 1

    print(f"\n  {passed}/{len(checks)} checks passed")
    print("=" * 65)

    await conn.close()
    neo4j.close()

asyncio.run(verify())
