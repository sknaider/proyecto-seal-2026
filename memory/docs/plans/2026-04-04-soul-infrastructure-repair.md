# SOUL Infrastructure Repair — Connectome + Qdrant Sync

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Repair the two integrity issues found in deep audit: 1,894 orphan nodes in Neo4j (47.7%) and 334 memories missing vectors in Qdrant.

**Architecture:** Use existing `sync_connectome.py --full` for Neo4j rebuild (handles all 7 MAGMA edge types). Write a targeted re-embedding script for the 334 missing Qdrant vectors using the existing `embeddings.py` module (multilingual-e5-base, 768 dims).

**Tech Stack:** Python 3.12, asyncpg, neo4j driver, qdrant_client, SentenceTransformers (multilingual-e5-base)

---

## Pre-flight: Baseline Metrics

Before any repair, capture exact counts for post-repair verification.

### Task 1: Capture Baseline Snapshot

**Files:**
- Create: `/home/dadito/IA/proyecto-seal/memory/scripts/repair_baseline.py`

**Step 1: Write baseline capture script**

```python
#!/usr/bin/env python3
"""Capture baseline metrics before SOUL repair."""
import asyncio
import asyncpg
import json
from datetime import datetime, timezone
from neo4j import GraphDatabase
from qdrant_client import QdrantClient

DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"
NEO4J_AUTH = ("neo4j", "seal2026soul")
QDRANT_HOST = "localhost"

async def capture():
    conn = await asyncpg.connect(DB_URL)
    qdrant = QdrantClient(host=QDRANT_HOST, port=6333)
    neo4j_driver = GraphDatabase.driver("bolt://localhost:7687", auth=NEO4J_AUTH)

    # PG counts
    pg_total = await conn.fetchval("SELECT COUNT(*) FROM memories")
    pg_valid = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE metadata->>'invalidated' IS DISTINCT FROM 'true'")
    pg_by_agent = await conn.fetch("SELECT agent, COUNT(*) as c FROM memories GROUP BY agent ORDER BY c DESC")

    # Qdrant counts
    info = qdrant.get_collection("soul_memories")
    qdrant_total = info.points_count

    # Orphan IDs (PG without Qdrant)
    pg_ids = set(r['id'] for r in await conn.fetch("SELECT id FROM memories"))
    result = qdrant.scroll(collection_name="soul_memories", limit=2500, with_payload=False, with_vectors=False)
    qdrant_ids = set(p.id for p in result[0])
    orphan_pg = pg_ids - qdrant_ids

    # Neo4j counts
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
        "orphan_pg_ids": sorted(list(orphan_pg))[:50],  # sample
        "neo4j_nodes": neo4j_nodes,
        "neo4j_edges": neo4j_edges,
        "neo4j_orphans": neo4j_orphans,
    }

    print(json.dumps(baseline, indent=2))

    # Save to file
    with open("/home/dadito/IA/proyecto-seal/memory/docs/plans/repair_baseline.json", "w") as f:
        json.dump(baseline, f, indent=2)

    await conn.close()
    neo4j_driver.close()
    print("\nBaseline saved to repair_baseline.json")

asyncio.run(capture())
```

**Step 2: Run baseline capture**

Run: `/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/scripts/repair_baseline.py`
Expected: JSON output with all counts, saved to repair_baseline.json

---

## Phase 1: Qdrant Re-embedding (334 missing vectors)

### Task 2: Re-embed Missing Memories to Qdrant

**Files:**
- Create: `/home/dadito/IA/proyecto-seal/memory/scripts/reembed_missing.py`
- Reference: `/home/dadito/IA/proyecto-seal/memory/embeddings.py` (get_embedding function)

**Step 1: Write re-embedding script**

```python
#!/usr/bin/env python3
"""Re-embed memories that exist in PG but not in Qdrant."""
import asyncio
import asyncpg
import sys
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")
from embeddings import get_embedding
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"
COLLECTION = "soul_memories"

async def find_missing():
    conn = await asyncpg.connect(DB_URL)
    qdrant = QdrantClient(host="localhost", port=6333)

    pg_rows = await conn.fetch("SELECT id, agent, category, content, importance, source, created_at, valence, arousal, dominance FROM memories")
    pg_map = {r['id']: r for r in pg_rows}
    pg_ids = set(pg_map.keys())

    result = qdrant.scroll(collection_name=COLLECTION, limit=2500, with_payload=False, with_vectors=False)
    qdrant_ids = set(p.id for p in result[0])

    missing_ids = sorted(pg_ids - qdrant_ids)
    print(f"PG: {len(pg_ids)} | Qdrant: {len(qdrant_ids)} | Missing: {len(missing_ids)}")
    return conn, qdrant, pg_map, missing_ids

async def reembed(dry_run=False):
    conn, qdrant, pg_map, missing_ids = await find_missing()

    if not missing_ids:
        print("Nothing to re-embed!")
        await conn.close()
        return

    success = 0
    failed = 0
    batch = []
    BATCH_SIZE = 50

    for i, mid in enumerate(missing_ids):
        row = pg_map[mid]
        content = row['content']
        if not content or len(content.strip()) < 5:
            print(f"  SKIP {mid}: empty content")
            failed += 1
            continue

        try:
            embedding = await asyncio.wait_for(get_embedding(content), timeout=30.0)
            point = PointStruct(
                id=mid,
                vector=embedding,
                payload={
                    "pg_id": mid,
                    "agent": row['agent'],
                    "category": row['category'],
                    "content": content,
                    "importance": row['importance'],
                    "source": row['source'] or "unknown",
                    "created_at": row['created_at'].isoformat() if row['created_at'] else "",
                    "valence": float(row['valence']) if row['valence'] is not None else 0.0,
                    "arousal": float(row['arousal']) if row['arousal'] is not None else 0.0,
                    "dominance": float(row['dominance']) if row['dominance'] is not None else 0.5,
                },
            )
            batch.append(point)
            success += 1

            if len(batch) >= BATCH_SIZE:
                if not dry_run:
                    qdrant.upsert(collection_name=COLLECTION, points=batch)
                print(f"  Batch upserted: {len(batch)} points ({i+1}/{len(missing_ids)})")
                batch = []

        except Exception as e:
            print(f"  FAIL {mid}: {e}")
            failed += 1

        if (i + 1) % 10 == 0:
            print(f"  Progress: {i+1}/{len(missing_ids)} (ok={success}, fail={failed})")

    # Final batch
    if batch and not dry_run:
        qdrant.upsert(collection_name=COLLECTION, points=batch)
        print(f"  Final batch: {len(batch)} points")

    # Verify
    result = qdrant.scroll(collection_name=COLLECTION, limit=2500, with_payload=False, with_vectors=False)
    final_count = len(result[0])

    print(f"\nDONE: {success} embedded, {failed} failed")
    print(f"Qdrant final count: {final_count}")
    await conn.close()

if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    if dry:
        print("=== DRY RUN ===")
    asyncio.run(reembed(dry_run=dry))
```

**Step 2: Dry run first**

Run: `/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/scripts/reembed_missing.py --dry-run`
Expected: Shows count of missing, processes without upserting

**Step 3: Execute real re-embedding**

Run: `/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/scripts/reembed_missing.py`
Expected: 334 memories embedded, Qdrant count rises to ~2065

**Step 4: Verify sync**

Run quick check: PG count == Qdrant count, 0 orphans both directions.

---

## Phase 2: Neo4j Connectome Rebuild

### Task 3: Full Connectome Rebuild via sync_connectome.py

**Files:**
- Reference: `/home/dadito/IA/proyecto-seal/memory/sync_connectome.py`

**Step 1: Check current sync_connectome stats**

Run: `/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/sync_connectome.py --stats`
Expected: Shows PG vs Neo4j edge comparison

**Step 2: Run full rebuild**

Run: `/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/sync_connectome.py --full`
Expected: All 7 edge types synced from PG to Neo4j, orphan count drops significantly

**Step 3: Run MCP connectome_build to add similarity-based edges**

After sync_connectome handles the PG-backed edges, use the MCP tool `connectome_build` to add Qdrant-similarity-based EXCITES/INHIBITS edges for the newly embedded memories.

**Step 4: Verify orphan count**

```python
from neo4j import GraphDatabase
d = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "seal2026soul"))
with d.session() as s:
    print("Orphans:", s.run("MATCH (n) WHERE NOT (n)--() RETURN COUNT(n)").single()[0])
    print("Total nodes:", s.run("MATCH (n) RETURN COUNT(n)").single()[0])
    print("Total edges:", s.run("MATCH ()-[r]->() RETURN COUNT(r)").single()[0])
d.close()
```

---

## Phase 3: Post-Repair Verification

### Task 4: Full Integrity Check

**Files:**
- Create: `/home/dadito/IA/proyecto-seal/memory/scripts/repair_verify.py`

**Step 1: Write verification script**

```python
#!/usr/bin/env python3
"""Verify SOUL integrity after repair."""
import asyncio
import asyncpg
import json
from neo4j import GraphDatabase
from qdrant_client import QdrantClient

DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"

async def verify():
    conn = await asyncpg.connect(DB_URL)
    qdrant = QdrantClient(host="localhost", port=6333)
    neo4j = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "seal2026soul"))

    # Load baseline
    with open("/home/dadito/IA/proyecto-seal/memory/docs/plans/repair_baseline.json") as f:
        baseline = json.load(f)

    checks = []

    # Check 1: PG↔Qdrant sync
    pg_ids = set(r['id'] for r in await conn.fetch("SELECT id FROM memories"))
    result = qdrant.scroll(collection_name="soul_memories", limit=2500, with_payload=False, with_vectors=False)
    qdrant_ids = set(p.id for p in result[0])
    orphans_q = qdrant_ids - pg_ids
    orphans_p = pg_ids - qdrant_ids
    sync_ok = len(orphans_q) == 0 and len(orphans_p) == 0
    checks.append(("PG↔Qdrant sync", sync_ok, f"orphans_q={len(orphans_q)}, orphans_p={len(orphans_p)}"))

    # Check 2: Neo4j orphans reduced
    with neo4j.session() as s:
        neo4j_orphans = s.run("MATCH (n) WHERE NOT (n)--() RETURN COUNT(n)").single()[0]
        neo4j_nodes = s.run("MATCH (n) RETURN COUNT(n)").single()[0]
        neo4j_edges = s.run("MATCH ()-[r]->() RETURN COUNT(r)").single()[0]
    orphan_pct = (neo4j_orphans / neo4j_nodes * 100) if neo4j_nodes > 0 else 0
    orphan_ok = orphan_pct < 10  # target: less than 10% orphans
    checks.append(("Neo4j orphans <10%", orphan_ok, f"{neo4j_orphans}/{neo4j_nodes} ({orphan_pct:.1f}%)"))

    # Check 3: Qdrant count matches PG
    count_ok = len(qdrant_ids) >= len(pg_ids) * 0.95  # 95% threshold
    checks.append(("Qdrant coverage >95%", count_ok, f"{len(qdrant_ids)}/{len(pg_ids)} ({len(qdrant_ids)/len(pg_ids)*100:.1f}%)"))

    # Check 4: Neo4j edges increased
    edges_up = neo4j_edges >= baseline["neo4j_edges"]
    checks.append(("Neo4j edges >= baseline", edges_up, f"{neo4j_edges} vs {baseline['neo4j_edges']}"))

    # Check 5: Spreading activation works
    # (functional test via MCP would go here)

    print("=" * 60)
    print("SOUL REPAIR VERIFICATION")
    print("=" * 60)
    passed = 0
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        symbol = "+" if ok else "!"
        print(f"  [{symbol}] {name}: {status} — {detail}")
        if ok:
            passed += 1

    print(f"\n  {passed}/{len(checks)} checks passed")
    print("=" * 60)

    await conn.close()
    neo4j.close()

asyncio.run(verify())
```

**Step 2: Run verification**

Run: `/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/scripts/repair_verify.py`
Expected: All checks PASS

---

## Execution Order Summary

1. **Task 1:** Capture baseline (2 min)
2. **Task 2:** Re-embed 334 missing to Qdrant (5-10 min, CPU embedding)
3. **Task 3:** Full connectome rebuild (3-5 min)
4. **Task 4:** Verify all repairs (1 min)

Total estimated: ~15-20 min
