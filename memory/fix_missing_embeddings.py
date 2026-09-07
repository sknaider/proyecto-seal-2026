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

import seal_secrets  # DSN desde la credencial privada, NUNCA literal

# 4-sep-2026: esta linea llevaba el DSN LITERAL del rol `seal`. La contrasena de ese rol
# fue cambiada en algun momento posterior al 22-jul, asi que el literal quedo muerto y este
# job venia fallando con InvalidPasswordError en cada corrida. Ademas reproducia en el codigo
# un secreto que ya vive en el historial de git (tarea 1594).
PG_DSN = seal_secrets.pg_dsn()
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
    espejados = 0
    for row in rows:
        mem_id = row["id"]
        content = row["content"] or ""
        antes_de_esta = ok
        try:
            embedding = await get_embedding(content)
            emb_str = json.dumps(embedding)

            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE memories SET embedding = $1::vector WHERE id = $2",
                    emb_str, mem_id
                )
            # El embedding YA esta en Postgres, que es la fuente de verdad del recall.
            # Se cuenta ACA: si el espejo de Qdrant falla, el trabajo util igual se hizo.
            ok += 1

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
            espejados += 1
            print(f"  [{ok}/{len(rows)}] ID={mem_id} agent={row['agent']} — OK (PG + Qdrant)")
        except Exception as e:
            # 4-sep-2026: este except tapaba DOS fallas muy distintas y reportaba la peor.
            # El UPDATE de Postgres ocurre ANTES del upsert a Qdrant, asi que un Qdrant
            # ausente hacia imprimir "0/10 regenerated" cuando en Postgres se habian
            # escrito los 10. Verificado por efecto: 0 memorias con embedding NULL.
            estado = "PG OK, espejo Qdrant fallo" if ok > antes_de_esta else "sin escribir"
            print(f"  FAIL ID={mem_id} ({estado}): {e}")

    await pool.close()
    await qdrant.close()
    print(f"\nDone: {ok}/{len(rows)} embeddings escritos en Postgres (fuente de verdad).")
    if espejados < ok:
        print(f"AVISO: solo {espejados}/{ok} se espejaron en Qdrant. "
              f"El recall por afinidad usa Postgres y NO depende de ese espejo.")


if __name__ == "__main__":
    asyncio.run(main())
