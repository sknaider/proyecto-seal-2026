#!/usr/bin/env python3
"""Fix 16 memories with NULL embeddings — regenerate and store in PG + Qdrant."""
import asyncio
import json
import os
import sys

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")

import asyncpg
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct
from embeddings import get_embedding

PG_DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
QDRANT_URL = "http://localhost:6333"
COLLECTION = "soul_memories"


async def main():
    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=2)
    qdrant = AsyncQdrantClient(url=QDRANT_URL)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, agent, category, content, importance, source,
                      created_at, valence, arousal, dominance, scope
               FROM memories
               WHERE embedding IS NULL AND invalid_at IS NULL
               ORDER BY id"""
        )

    print(f"Found {len(rows)} memories without embeddings")

    ok = 0
    for row in rows:
        mem_id = row["id"]
        content = row["content"] or ""
        try:
            embedding = await get_embedding(content)
            emb_str = json.dumps(embedding)

            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE memories SET embedding = $1::vector WHERE id = $2",
                    emb_str, mem_id
                )

            await qdrant.upsert(
                collection_name=COLLECTION,
                points=[PointStruct(
                    id=mem_id,
                    vector=embedding,
                    payload={
                        "pg_id": mem_id,
                        "agent": row["agent"],
                        "category": row["category"] or "general",
                        "content": content,
                        "importance": row["importance"] or 5,
                        "source": row["source"] or "system",
                        "created_at": row["created_at"].isoformat(),
                        "valence": float(row["valence"] or 0),
                        "arousal": float(row["arousal"] or 0),
                        "dominance": float(row["dominance"] or 0),
                        "scope": row["scope"] or "agent",
                        "utility": 0.5,
                        "confidence": 1.0,
                    },
                )]
            )
            ok += 1
            print(f"  [{ok}/{len(rows)}] ID={mem_id} agent={row['agent']} — OK")
        except Exception as e:
            print(f"  FAIL ID={mem_id}: {e}")

    await pool.close()
    await qdrant.close()
    print(f"\nDone: {ok}/{len(rows)} embeddings regenerated.")


if __name__ == "__main__":
    asyncio.run(main())
