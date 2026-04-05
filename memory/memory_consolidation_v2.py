#!/usr/bin/env python3
"""
memory_consolidation_v2.py — Consolidación semántica de memorias SEAL
Detecta clusters de memorias semánticamente equivalentes y las colapsa
en un único insight de mayor importancia.

NUNCA borra memorias originales — solo las invalida con reason=superseded_by_consolidated.

Uso:
    python3 memory_consolidation_v2.py --agent ADA           # dry_run (default)
    python3 memory_consolidation_v2.py --agent ADA --execute # ejecución real
    python3 memory_consolidation_v2.py --agent ADA --all     # todos los agentes
    python3 memory_consolidation_v2.py --status              # estadísticas
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx

sys.path.insert(0, str(Path(__file__).parent))
from embeddings import get_embedding

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue
except ImportError:
    print("ERROR: pip install qdrant-client", file=sys.stderr)
    sys.exit(1)

LOG = logging.getLogger("seal-consolidation-v2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
QDRANT_HOST = "localhost"
QDRANT_PORT = 6333
COLLECTION = "soul_memories"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"

# Configuración de clustering
SIMILARITY_THRESHOLD = 0.75   # score mínimo para considerar mismo insight
MIN_CLUSTER_SIZE = 3          # mínimo de memorias para consolidar
MAX_MEMORIES_PER_SCAN = 200   # límite por agente para no sobrecargar


# ── Ollama health check ──────────────────────────────────────────────────────

async def check_ollama() -> bool:
    """Verifica que Ollama está disponible antes de usarlo."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            return resp.status_code == 200
    except Exception:
        return False


async def synthesize_insight(memories: list[dict], agent: str, category: str) -> str | None:
    """
    Genera un insight unificado a partir de N memorias similares usando Ollama.
    Retorna None si Ollama no está disponible.
    """
    if not await check_ollama():
        return None

    contents = "\n".join(f"- {m['content']}" for m in memories)
    prompt = (
        f"Eres {agent}, un agente de IA del equipo SEAL. "
        f"Las siguientes memorias de la categoría '{category}' son variaciones del mismo insight:\n\n"
        f"{contents}\n\n"
        f"Escribe UN SOLO insight unificado en primera persona que capture la esencia de todas estas memorias. "
        f"Máximo 2 oraciones. Sin prefijos ni explicaciones."
    )

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(OLLAMA_URL, json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 200},
            })
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
    except Exception as e:
        LOG.warning(f"Ollama error: {e}")
        return None


# ── Detección de clusters ────────────────────────────────────────────────────

async def find_clusters(agent: str) -> list[list[dict]]:
    """
    Busca clusters de memorias semánticamente equivalentes en Qdrant.
    Retorna lista de clusters (cada cluster es una lista de memorias).
    """
    q = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    # Obtener memorias activas del agente
    results = q.scroll(
        collection_name=COLLECTION,
        scroll_filter=Filter(
            must=[FieldCondition(key="agent", match=MatchValue(value=agent))]
        ),
        limit=MAX_MEMORIES_PER_SCAN,
        with_vectors=True,
    )

    memories = []
    for point in results[0]:
        # Solo memorias no invalidadas
        if point.payload.get("invalid_at"):
            continue
        if point.payload.get("source") == "consolidation":
            continue  # No re-consolidar consolidados
        memories.append({
            "id": point.id,
            "db_id": point.payload.get("pg_id"),
            "content": point.payload.get("content", ""),
            "importance": point.payload.get("importance", 5),
            "category": point.payload.get("category", "general"),
            "vector": point.vector,
            "created_at": point.payload.get("created_at", ""),
        })

    LOG.info(f"Escaneando {len(memories)} memorias activas de {agent}...")

    # Agrupar por categoría primero (reduce comparaciones)
    by_category: dict[str, list[dict]] = {}
    for m in memories:
        cat = m["category"]
        by_category.setdefault(cat, []).append(m)

    clusters = []
    processed_ids = set()

    for category, cat_memories in by_category.items():
        if len(cat_memories) < MIN_CLUSTER_SIZE:
            continue

        # Para cada memoria, buscar similares en Qdrant
        for mem in cat_memories:
            if mem["id"] in processed_ids:
                continue
            if mem["vector"] is None:
                continue

            # Búsqueda por vector en la misma categoría
            similar = q.query_points(
                collection_name=COLLECTION,
                query=mem["vector"],
                query_filter=Filter(
                    must=[
                        FieldCondition(key="agent", match=MatchValue(value=agent)),
                        FieldCondition(key="category", match=MatchValue(value=category)),
                    ]
                ),
                limit=20,
                score_threshold=SIMILARITY_THRESHOLD,
            )

            cluster_ids = set()
            cluster_mems = []
            for hit in similar.points:
                if hit.id in processed_ids:
                    continue
                # Buscar la memoria completa
                hit_mem = next((m for m in cat_memories if m["id"] == hit.id), None)
                if hit_mem and hit.id != mem["id"]:
                    cluster_ids.add(hit.id)
                    cluster_mems.append(hit_mem)

            if len(cluster_mems) + 1 >= MIN_CLUSTER_SIZE:
                full_cluster = [mem] + cluster_mems

                # Filtro temporal: si event_time spread >7 días → es evolución, no redundancia
                dates = []
                for m in full_cluster:
                    ts = m.get("created_at", "")
                    if ts:
                        try:
                            dates.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
                        except ValueError:
                            pass
                if len(dates) >= 2:
                    spread_days = (max(dates) - min(dates)).days
                    if spread_days > 7:
                        LOG.info(f"Cluster EVOLUTION (spread={spread_days}d) — omitiendo consolidación")
                        continue

                clusters.append(full_cluster)
                processed_ids.update(m["id"] for m in full_cluster)

    LOG.info(f"Clusters detectados en {agent}: {len(clusters)}")
    return clusters


# ── Consolidación ────────────────────────────────────────────────────────────

async def consolidate_cluster(
    cluster: list[dict],
    agent: str,
    conn: asyncpg.Connection,
    dry_run: bool = True,
) -> dict:
    """
    Consolida un cluster de memorias.
    En dry_run: solo muestra el plan.
    En execute: crea nueva memoria + invalida originales.
    """
    category = cluster[0]["category"]
    max_importance = max(m["importance"] for m in cluster)
    avg_score = SIMILARITY_THRESHOLD  # aproximado

    # Intentar síntesis con Ollama
    synthesis = await synthesize_insight(cluster, agent, category)
    ollama_available = synthesis is not None

    if not ollama_available:
        synthesis = f"[SÍNTESIS PENDIENTE — Ollama no disponible] Basado en {len(cluster)} memorias sobre: {cluster[0]['content'][:100]}..."

    result = {
        "cluster_size": len(cluster),
        "category": category,
        "agent": agent,
        "max_importance": max_importance,
        "avg_similarity": avg_score,
        "synthesis": synthesis,
        "ollama_available": ollama_available,
        "original_ids": [m["db_id"] for m in cluster if m.get("db_id")],
        "original_contents": [m["content"][:80] for m in cluster],
        "executed": False,
    }

    if dry_run or not ollama_available:
        return result

    # ── Ejecución real ──
    now = datetime.now(timezone.utc)

    # 1. Crear nueva memoria consolidada en PostgreSQL
    new_embedding = await get_embedding(synthesis)
    new_id = await conn.fetchval(
        """INSERT INTO memories
           (agent, category, content, importance, source, created_at, metadata)
           VALUES ($1, $2, $3, $4, 'consolidation', $5, $6)
           RETURNING id""",
        agent,
        category,
        synthesis,
        max_importance,
        now,
        json.dumps({
            "consolidated_from": result["original_ids"],
            "cluster_size": len(cluster),
            "consolidation_date": now.isoformat(),
        }),
    )

    # 2. Invalidar memorias originales (nunca borrar)
    original_db_ids = [m["db_id"] for m in cluster if m.get("db_id")]
    if original_db_ids:
        await conn.execute(
            """UPDATE memories
               SET invalid_at = $1,
                   metadata = COALESCE(metadata, '{}'::jsonb) ||
                              jsonb_build_object('superseded_by', $2,
                                                 'reason', 'superseded_by_consolidated')
               WHERE id = ANY($3::bigint[])""",
            now,
            new_id,
            original_db_ids,
        )

    # 3. Insertar nueva memoria en Qdrant
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct
    q = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    point_id = int(hashlib.md5(f"consolidated_{new_id}".encode()).hexdigest()[:15], 16) % (2**63)
    q.upsert(
        collection_name=COLLECTION,
        points=[PointStruct(
            id=point_id,
            vector=new_embedding,
            payload={
                "db_id": new_id,
                "agent": agent,
                "category": category,
                "content": synthesis,
                "importance": max_importance,
                "source": "consolidation",
                "created_at": now.isoformat(),
            },
        )],
    )

    result["executed"] = True
    result["new_memory_id"] = new_id
    LOG.info(f"Cluster consolidado → memoria #{new_id} ({len(cluster)} → 1)")
    return result


# ── Reporte ──────────────────────────────────────────────────────────────────

def print_report(results: list[dict], dry_run: bool, agent: str) -> None:
    mode = "DRY RUN" if dry_run else "EJECUTADO"
    print(f"\n{'='*60}")
    print(f"MEMORY CONSOLIDATION v2 — {mode} — Agente: {agent}")
    print(f"{'='*60}")
    print(f"Clusters detectados: {len(results)}")

    if not results:
        print("No se encontraron clusters para consolidar.")
        return

    total_savings = sum(r["cluster_size"] - 1 for r in results)
    print(f"Memorias que se consolidarían: {sum(r['cluster_size'] for r in results)}")
    print(f"Reducción neta: -{total_savings} memorias\n")

    for i, r in enumerate(results, 1):
        print(f"── Cluster {i} [{r['category']}] ({r['cluster_size']} memorias, imp={r['max_importance']}) ──")
        print(f"   Memorias originales:")
        for content in r["original_contents"]:
            print(f"     · {content}")
        ollama_status = "✅" if r["ollama_available"] else "⚠️ Ollama no disponible"
        print(f"   Síntesis propuesta ({ollama_status}):")
        print(f"     → {r['synthesis']}")
        if r.get("executed"):
            print(f"   ✅ CONSOLIDADO → memoria #{r.get('new_memory_id')}")
        print()

    if dry_run:
        print("Para ejecutar: python3 memory_consolidation_v2.py --execute")
        print("William debe revisar y aprobar antes de ejecutar.")


# ── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(description="SEAL Memory Consolidation v2")
    parser.add_argument("--agent", default="ADA", help="Agente a procesar (default: ADA)")
    parser.add_argument("--all", action="store_true", help="Procesar todos los agentes")
    parser.add_argument("--execute", action="store_true", help="Ejecutar consolidación real (default: dry_run)")
    parser.add_argument("--status", action="store_true", help="Mostrar estadísticas de memorias")
    args = parser.parse_args()

    dry_run = not args.execute

    if args.status:
        conn = await asyncpg.connect(DB_URL)
        for ag in ["ADA", "JARVIS", "DUM"]:
            total = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE agent=$1", ag)
            active = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL", ag)
            consolidated = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE agent=$1 AND source='consolidation'", ag)
            print(f"{ag}: {total} total | {active} activas | {consolidated} consolidadas")
        await conn.close()
        return

    agents = ["ADA", "JARVIS", "DUM"] if args.all else [args.agent.upper()]

    conn = await asyncpg.connect(DB_URL)
    try:
        for agent in agents:
            LOG.info(f"Procesando agente: {agent} (dry_run={dry_run})")
            clusters = await find_clusters(agent)

            if not clusters:
                print(f"\n{agent}: No se encontraron clusters para consolidar (threshold={SIMILARITY_THRESHOLD}).")
                continue

            results = []
            for cluster in clusters:
                result = await consolidate_cluster(cluster, agent, conn, dry_run=dry_run)
                results.append(result)

            print_report(results, dry_run, agent)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
