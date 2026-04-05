#!/usr/bin/env python3
"""Re-embed memories that exist in PG but not in Qdrant."""
import asyncio
import asyncpg
import sys
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")
from embeddings import get_embedding
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
COLLECTION = "soul_memories"


async def find_missing():
    conn = await asyncpg.connect(DB_URL)
    qdrant = QdrantClient(host="localhost", port=6333)

    pg_rows = await conn.fetch(
        "SELECT id, agent, category, content, importance, source, "
        "created_at, valence, arousal, dominance FROM memories"
    )
    pg_map = {r['id']: r for r in pg_rows}
    pg_ids = set(pg_map.keys())

    result = qdrant.scroll(
        collection_name=COLLECTION, limit=2500,
        with_payload=False, with_vectors=False
    )
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

        if (i + 1) % 50 == 0:
            print(f"  Progress: {i+1}/{len(missing_ids)} (ok={success}, fail={failed})")

    if batch and not dry_run:
        qdrant.upsert(collection_name=COLLECTION, points=batch)
        print(f"  Final batch: {len(batch)} points")

    # Verify
    result = qdrant.scroll(
        collection_name=COLLECTION, limit=2500,
        with_payload=False, with_vectors=False
    )
    final_count = len(result[0])

    print(f"\nDONE: {success} embedded, {failed} failed")
    print(f"Qdrant final count: {final_count}")
    await conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    if dry:
        print("=== DRY RUN ===")
    asyncio.run(reembed(dry_run=dry))
