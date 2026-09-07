#!/usr/bin/env python3
"""
MEMORY SCENES — MemScene clustering para SOUL (inspirado en EverMemOS arXiv:2601.02163)

Agrupa memorias relacionadas en "escenas" temáticas en lugar de solo aristas individuales.
Mejora el connectome: en lugar de 174K aristas sueltas, grupos contextuales de 5-15 memorias.

Uso:
    python3 memscenes.py              # construye/actualiza escenas para ADA
    python3 memscenes.py --agent JARVIS
    python3 memscenes.py --dry-run    # muestra clusters sin guardar
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Optional

import asyncpg
import httpx
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from db import get_pool, close_pool

LOG = logging.getLogger("memscenes")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

AGENT = os.environ.get("SEAL_AGENT", "ADA")
DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
LLAMA_URL = "http://localhost:8899/v1/chat/completions"
MODEL = "gemma4-31b"

# Parámetros de clustering
SIM_THRESHOLD = 0.72      # similaridad mínima para agrupar
MIN_SCENE_SIZE = 4        # mínimo memorias por escena
MAX_SCENE_SIZE = 15       # máximo memorias por escena
MAX_SCENES_PER_RUN = 8    # máximo escenas a crear por ejecución


async def get_embeddings_from_qdrant(agent: str, memory_ids: list[int]) -> dict[int, list[float]]:
    """Obtiene embeddings de Qdrant para las memorias dadas."""
    from qdrant_client import AsyncQdrantClient
    qdrant = AsyncQdrantClient(url="http://localhost:6333")

    results = await qdrant.retrieve(
        collection_name="soul_memories",
        ids=memory_ids,
        with_vectors=True,
    )
    return {int(r.id): r.vector for r in results if r.vector}


def cosine_similarity(a: list[float], b: list[float]) -> float:
    a, b = np.array(a), np.array(b)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / norm) if norm > 0 else 0.0


def cluster_by_similarity(
    embeddings: dict[int, list[float]],
    threshold: float = SIM_THRESHOLD,
) -> list[list[int]]:
    """Agrupa IDs por similaridad usando greedy clustering."""
    ids = list(embeddings.keys())
    visited = set()
    clusters = []

    for i, id_a in enumerate(ids):
        if id_a in visited:
            continue
        cluster = [id_a]
        visited.add(id_a)
        vec_a = embeddings[id_a]

        for id_b in ids[i + 1:]:
            if id_b in visited:
                continue
            sim = cosine_similarity(vec_a, embeddings[id_b])
            if sim >= threshold:
                cluster.append(id_b)
                visited.add(id_b)
                if len(cluster) >= MAX_SCENE_SIZE:
                    break

        if len(cluster) >= MIN_SCENE_SIZE:
            clusters.append(cluster)

    return clusters


async def generate_scene_theme(memories: list[dict]) -> tuple[str, str]:
    """Genera un tema y resumen para un cluster de memorias usando qwen2.5."""
    contents = "\n".join(
        f"- [{m['category']}] {m['content'][:150]}"
        for m in memories[:8]
    )
    prompt = f"""Analiza estas memorias de un agente IA y genera:
1. Un TEMA corto (3-6 palabras) que las agrupe temáticamente
2. Un RESUMEN de 1-2 oraciones que capture la esencia del grupo

Memorias:
{contents}

Responde en formato JSON exacto:
{{"theme": "...", "summary": "..."}}

Solo el JSON, sin markdown."""

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                LLAMA_URL,
                json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 200, "stream": False},
            )
            text = resp.json()["choices"][0]["message"]["content"].strip()
            # Extraer JSON
            import re
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                data = json.loads(match.group())
                return data.get("theme", "Sin tema"), data.get("summary", "Sin resumen")
    except Exception as e:
        LOG.warning(f"LLM falló al generar tema: {e}")

    # Fallback: tema basado en categoría más frecuente
    cats = [m.get("category", "general") for m in memories]
    most_common = max(set(cats), key=cats.count)
    return f"Cluster {most_common}", f"Grupo de {len(memories)} memorias relacionadas ({most_common})"


async def build_scenes(agent: str, dry_run: bool = False) -> dict:
    """Construye/actualiza MemScenes para un agente."""
    pool = await asyncpg.create_pool(DB_URL)

    async with pool.acquire() as conn:
        # Obtener memorias válidas, excluyendo las ya en escenas recientes
        existing_scene_ids = await conn.fetch(
            "SELECT memory_ids FROM memory_scenes WHERE agent = $1", agent
        )
        already_in_scene = set()
        for row in existing_scene_ids:
            already_in_scene.update(row["memory_ids"])

        memories = await conn.fetch(
            """SELECT id, category, content, importance
               FROM memories
               WHERE agent = $1 AND invalid_at IS NULL
               AND importance >= 4
               ORDER BY created_at DESC
               LIMIT 400""",
            agent,
        )

    # Filtrar las ya en escena (actualización incremental)
    new_memories = [m for m in memories if m["id"] not in already_in_scene]
    LOG.info(f"Memorias a clusterizar: {len(new_memories)} (de {len(memories)} total, {len(already_in_scene)} ya en escenas)")

    if len(new_memories) < MIN_SCENE_SIZE * 2:
        LOG.info("Pocas memorias nuevas — nada que clusterizar")
        await pool.close()
        return {"scenes_created": 0, "memories_processed": len(new_memories)}

    # Obtener embeddings
    mem_ids = [m["id"] for m in new_memories]
    LOG.info(f"Obteniendo embeddings de {len(mem_ids)} memorias...")
    embeddings = await get_embeddings_from_qdrant(agent, mem_ids)
    LOG.info(f"Embeddings obtenidos: {len(embeddings)}")

    if len(embeddings) < MIN_SCENE_SIZE:
        LOG.info("Pocos embeddings disponibles")
        await pool.close()
        return {"scenes_created": 0}

    # Clustering
    clusters = cluster_by_similarity(embeddings, SIM_THRESHOLD)
    LOG.info(f"Clusters encontrados: {len(clusters)}")

    if not clusters:
        await pool.close()
        return {"scenes_created": 0}

    # Limitar a MAX_SCENES_PER_RUN
    clusters = sorted(clusters, key=len, reverse=True)[:MAX_SCENES_PER_RUN]

    # Generar temas y guardar
    mem_map = {m["id"]: dict(m) for m in new_memories}
    scenes_created = 0

    for cluster_ids in clusters:
        cluster_mems = [mem_map[i] for i in cluster_ids if i in mem_map]
        if len(cluster_mems) < MIN_SCENE_SIZE:
            continue

        theme, summary = await generate_scene_theme(cluster_mems)
        avg_imp = sum(m["importance"] for m in cluster_mems) / len(cluster_mems)

        LOG.info(f"Escena: '{theme}' — {len(cluster_mems)} memorias, imp_avg={avg_imp:.1f}")

        if dry_run:
            print(f"\n{'='*50}")
            print(f"ESCENA: {theme}")
            print(f"RESUMEN: {summary}")
            print(f"MEMORIAS ({len(cluster_mems)}):")
            for m in cluster_mems[:5]:
                print(f"  [{m['category']}] {m['content'][:100]}")
            continue

        async with pool.acquire() as conn:
            # Upsert: si ya existe tema similar, actualizar
            await conn.execute(
                """INSERT INTO memory_scenes (agent, theme, summary, memory_ids, importance)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (agent, theme) DO UPDATE SET
                       summary = EXCLUDED.summary,
                       memory_ids = array(
                           SELECT DISTINCT unnest(memory_scenes.memory_ids || EXCLUDED.memory_ids)
                       ),
                       updated_at = NOW()""",
                agent, theme, summary, cluster_ids, avg_imp,
            )
        scenes_created += 1

    await pool.close()

    result = {
        "scenes_created": scenes_created,
        "clusters_found": len(clusters),
        "memories_processed": len(embeddings),
    }
    LOG.info(f"MemScenes completado: {result}")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    AGENT = args.agent
    result = asyncio.run(build_scenes(args.agent, args.dry_run))
    print(f"\nResultado: {result}")
