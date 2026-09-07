#!/usr/bin/env python3
"""
SOUL CONSOLIDATION — Compresión inteligente de memorias redundantes.

Inspirado en MemGPT (Stanford, 2023): cuando hay muchas memorias similares,
las agrupa en una "summary memory" más rica y precisa.

Corre automáticamente desde soul_awareness cada 2h.
También ejecutable standalone.

Lo que hace:
1. Busca clusters de memorias similares (sim > 0.85)
2. Genera un resumen consolidado de cada cluster
3. Invalida las memorias originales del cluster
4. Guarda el resumen como nueva memoria de alta importancia
5. Preserva la importancia más alta del cluster

Uso:
    python3 soul_consolidate.py              # consolida ahora
    python3 soul_consolidate.py --dry-run    # muestra qué haría sin ejecutar
    python3 soul_consolidate.py --agent ADA  # solo ADA
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from typing import Optional

import asyncpg
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct

sys.path.insert(0, os.path.dirname(__file__))
from db import get_pool, close_pool
from embeddings import get_embedding

LOG = logging.getLogger("soul-consolidate")

# Thresholds
SIMILARITY_THRESHOLD = 0.88   # dos memorias son "similares" si sim > esto
MIN_CLUSTER_SIZE = 3          # mínimo memorias para consolidar un cluster
MAX_CLUSTERS_PER_RUN = 5      # no consolidar más de N clusters por ejecución


async def find_similar_clusters(agent: str, pool) -> list[list[dict]]:
    """Encuentra clusters de memorias similares usando embeddings."""
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    qdrant = AsyncQdrantClient(url="http://localhost:6333")
    COLLECTION = "soul_memories"

    # Obtener todas las memorias del agente (no invalidadas)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, category, content, importance, created_at
               FROM memories
               WHERE agent = $1 AND invalid_at IS NULL
               AND importance < 9
               ORDER BY created_at ASC""",
            agent,
        )

    if len(rows) < MIN_CLUSTER_SIZE * 2:
        return []

    mem_ids = [r["id"] for r in rows]
    mem_map = {r["id"]: dict(r) for r in rows}

    # Para cada memoria, buscar similares via Qdrant
    clusters = []
    visited = set()

    for mem_id in mem_ids:
        if mem_id in visited:
            continue

        # Obtener vector de esta memoria
        points = await qdrant.retrieve(
            collection_name=COLLECTION,
            ids=[mem_id],
            with_vectors=True,
            with_payload=True,
        )
        if not points or not points[0].vector:
            continue

        vector = points[0].vector

        # Buscar similares
        similar_resp = await qdrant.query_points(
            collection_name=COLLECTION,
            query=vector,
            query_filter=Filter(
                must=[FieldCondition(key="agent", match=MatchValue(value=agent))]
            ),
            limit=10,
            score_threshold=SIMILARITY_THRESHOLD,
            with_payload=True,
        )
        similar_ids = [p.id for p in similar_resp.points if p.id != mem_id]

        # Solo incluir los que están en mem_map (no invalidados)
        cluster_ids = [mem_id] + [s for s in similar_ids if s in mem_map and s not in visited]

        if len(cluster_ids) >= MIN_CLUSTER_SIZE:
            cluster = [mem_map[cid] for cid in cluster_ids if cid in mem_map]
            clusters.append(cluster)
            visited.update(cluster_ids)

        if len(clusters) >= MAX_CLUSTERS_PER_RUN:
            break

    return clusters


def summarize_cluster(cluster: list[dict]) -> dict:
    """Crea un resumen de un cluster de memorias similares."""
    # Ordenar por importancia (mayor primero) y fecha
    cluster_sorted = sorted(cluster, key=lambda m: (-m["importance"], m["created_at"]))

    # Tomar la importancia más alta del cluster
    max_importance = min(cluster_sorted[0]["importance"] + 1, 10)

    # Determinar categoría más común
    cats = [m["category"] for m in cluster]
    category = max(set(cats), key=cats.count)

    # Construir resumen
    contents = [m["content"][:200] for m in cluster_sorted[:5]]
    oldest = cluster_sorted[-1]["created_at"].strftime("%Y-%m-%d") if cluster_sorted[-1].get("created_at") else "?"
    newest = cluster_sorted[0]["created_at"].strftime("%Y-%m-%d") if cluster_sorted[0].get("created_at") else "?"

    summary_content = (
        f"[CONSOLIDADO {newest}] Resumen de {len(cluster)} memorias similares ({oldest} → {newest}): "
        + " | ".join(contents[:3])
    )

    if len(summary_content) > 800:
        summary_content = summary_content[:797] + "..."

    return {
        "content": summary_content,
        "category": category,
        "importance": max_importance,
        "cluster_size": len(cluster),
        "original_ids": [m["id"] for m in cluster],
    }


async def consolidate_agent(agent: str, dry_run: bool = False) -> str:
    """Consolida memorias redundantes de un agente."""
    pool = await get_pool()

    LOG.info(f"Buscando clusters para {agent}...")
    clusters = await find_similar_clusters(agent, pool)

    if not clusters:
        return f"{agent}: Sin clusters para consolidar."

    results = []
    for i, cluster in enumerate(clusters):
        summary = summarize_cluster(cluster)
        orig_ids = summary["original_ids"]

        LOG.info(
            f"Cluster {i+1}/{len(clusters)}: {len(cluster)} memorias → "
            f"'{summary['content'][:60]}...' (imp={summary['importance']})"
        )

        if not dry_run:
            async with pool.acquire() as conn:
                # DEDUP: check if identical consolidation already exists
                exists = await conn.fetchval(
                    "SELECT id FROM memories WHERE agent = $1 AND content = $2 LIMIT 1",
                    agent, summary["content"],
                )
                if exists:
                    LOG.info(f"Cluster {i+1}: consolidation already exists as #{exists} — skipped")
                    results.append(f"Cluster {i+1}: already consolidated as #{exists}")
                    continue

                # Guardar memoria consolidada
                new_id = await conn.fetchval(
                    """INSERT INTO memories (agent, category, content, importance, source, metadata)
                       VALUES ($1, $2, $3, $4, 'soul_consolidation', $5)
                       RETURNING id""",
                    agent,
                    summary["category"],
                    summary["content"],
                    summary["importance"],
                    json.dumps({"consolidated_from": orig_ids, "cluster_size": summary["cluster_size"]}),
                )

                # Qdrant: add consolidation, remove originals
                try:
                    from qdrant_client.models import PointIdsList
                    embedding = await get_embedding(summary["content"])
                    if embedding:
                        qdrant = AsyncQdrantClient(url="http://localhost:6333")
                        await qdrant.upsert(
                            collection_name="soul_memories",
                            points=[PointStruct(
                                id=new_id,
                                vector=embedding,
                                payload={
                                    "agent": agent,
                                    "category": summary["category"],
                                    "content": summary["content"],
                                    "importance": summary["importance"],
                                    "source": "soul_consolidation",
                                },
                            )],
                        )
                        # Remove invalidated originals from Qdrant
                        await qdrant.delete(
                            collection_name="soul_memories",
                            points_selector=PointIdsList(points=orig_ids),
                        )
                except Exception as e:
                    LOG.warning(f"Qdrant consolidation sync: {e}")

                # Invalidar memorias originales (no borrar — solo marcar)
                await conn.execute(
                    "UPDATE memories SET invalid_at = NOW() WHERE id = ANY($1)",
                    orig_ids,
                )

            results.append(f"Cluster {i+1}: {len(cluster)} → 1 (id #{new_id}, imp={summary['importance']})")
        else:
            results.append(f"[DRY] Cluster {i+1}: {len(cluster)} memorias serían consolidadas en 1")

    action = "consolidadas" if not dry_run else "identificadas (dry-run)"
    return f"{agent}: {len(clusters)} clusters {action}. " + " | ".join(results)


async def session_relink(agent: str, hours: float = 4.0, dry_run: bool = False) -> str:
    """A-MEM Zettelkasten post-session re-linking.

    After a session, finds semantic connections between recent memories and
    older ones not yet linked in Neo4j. Creates new EXCITES edges.

    Inspired by A-MEM (arXiv:2501.13326): Zettelkasten-style note-linking
    that enriches the knowledge graph without destroying anything.

    Args:
        agent: Agent to relink (ADA or JARVIS)
        hours: Look at memories from last N hours (default: 4 = typical session)
        dry_run: Show what would be created without writing
    """
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    from neo4j import AsyncGraphDatabase

    NEO4J_URI  = "bolt://localhost:7687"
    NEO4J_AUTH = ("neo4j", "seal2026soul")
    COLLECTION = "soul_memories"
    RELINK_SIM_THRESHOLD = 0.65   # looser than connectome_build (0.70) to catch cross-topic links
    MAX_LINKS_PER_MEMORY = 5
    MAX_NEW_LINKS_TOTAL  = 200

    qdrant = AsyncQdrantClient(url="http://localhost:6333")
    pool   = await get_pool()
    since  = datetime.now(LIMA_TZ) - timedelta(hours=hours)

    # Get recent memories (from this session)
    async with pool.acquire() as conn:
        recent = await conn.fetch(
            """SELECT id, category, content, importance
               FROM memories
               WHERE agent = $1 AND invalid_at IS NULL AND created_at >= $2
               ORDER BY importance DESC""",
            agent, since,
        )

    if not recent:
        return f"{agent}: No recent memories in last {hours:.0f}h — nothing to relink."

    LOG.info(f"{agent}: {len(recent)} recent memories, looking for new connections...")

    # Get existing Neo4j edges to avoid duplicates
    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    existing_pairs: set[tuple] = set()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (a:Memory)-[r:EXCITES|INHIBITS]->(b:Memory) "
            "RETURN a.memory_id AS src, b.memory_id AS tgt"
        )
        records = await result.data()
        existing_pairs = {(r["src"], r["tgt"]) for r in records}

    LOG.info(f"  Existing Neo4j edges: {len(existing_pairs):,}")

    created = 0
    skipped_existing = 0
    now_iso = datetime.now(LIMA_TZ).isoformat()

    async with driver.session() as neo_session:
        for mem in recent:
            if created >= MAX_NEW_LINKS_TOTAL:
                break

            # Get vector from Qdrant
            points = await qdrant.retrieve(
                collection_name=COLLECTION,
                ids=[mem["id"]],
                with_vectors=True,
            )
            if not points or not points[0].vector:
                continue

            # Find semantically related memories (not just recent ones)
            similar_resp = await qdrant.query_points(
                collection_name=COLLECTION,
                query=points[0].vector,
                query_filter=Filter(
                    must=[FieldCondition(key="agent", match=MatchValue(value=agent))]
                ),
                limit=MAX_LINKS_PER_MEMORY + 5,
                score_threshold=RELINK_SIM_THRESHOLD,
                with_payload=True,
            )

            for s in similar_resp.points:
                if s.id == mem["id"]:
                    continue
                if (mem["id"], s.id) in existing_pairs:
                    skipped_existing += 1
                    continue
                if created >= MAX_NEW_LINKS_TOTAL:
                    break

                # Determine edge type (corrections inhibit, everything else excites)
                src_cat = mem["category"]
                tgt_cat = s.payload.get("category", "")
                rel_type = "INHIBITS" if (src_cat == "correction" or tgt_cat == "correction") else "EXCITES"

                if not dry_run:
                    await neo_session.run(
                        f"MATCH (a:Memory {{memory_id: $src}}), (b:Memory {{memory_id: $tgt}}) "
                        f"MERGE (a)-[r:{rel_type}]->(b) "
                        f"SET r.weight = $weight, r.valid_from = coalesce(r.valid_from, $now), "
                        f"    r.source = 'session_relink'",
                        src=mem["id"], tgt=s.id,
                        weight=float(s.score), now=now_iso,
                    )
                    existing_pairs.add((mem["id"], s.id))

                created += 1
                if created % 50 == 0:
                    LOG.info(f"  ... {created} new links created")

    await driver.close()

    action = "would create" if dry_run else "created"
    return (
        f"{agent}: session_relink {action} {created} new edges "
        f"({skipped_existing} already existed) "
        f"from {len(recent)} recent memories (last {hours:.0f}h)"
    )


async def main(agent: Optional[str] = None, dry_run: bool = False, relink: bool = False, hours: float = 4.0):
    agents = [agent] if agent else ["ADA", "JARVIS", "ALICE", "DUM", "NEXUS"]
    results = []
    for ag in agents:
        if relink:
            result = await session_relink(ag, hours=hours, dry_run=dry_run)
        else:
            result = await consolidate_agent(ag, dry_run=dry_run)
        LOG.info(result)
        results.append(result)
    await close_pool()
    return "\n".join(results)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--agent", default=None)
    parser.add_argument("--relink", action="store_true", help="Run A-MEM post-session re-linking instead of consolidation")
    parser.add_argument("--hours", type=float, default=4.0, help="Look back N hours for recent memories (default: 4)")
    args = parser.parse_args()
    asyncio.run(main(agent=args.agent, dry_run=args.dry_run, relink=args.relink, hours=args.hours))
