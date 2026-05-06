#!/usr/bin/env python3
"""Benchmark Hopfield re-ranking vs baseline — Fase 2 NEXUS
William autorizó 2026-04-26 01:59 Lima

Métricas:
- importance@5: avg importance de top-5 (proxy de calidad sin ground truth)
- rank_shift: cambio promedio de posición entre baseline y Hopfield
- coverage: % de queries donde top-5 cambia al menos 1 posición
- latency: ms por query con y sin Hopfield
"""
import asyncio
import time
import sys
import json
import os
from typing import Optional

import numpy as np
import asyncpg
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from sentence_transformers import SentenceTransformer

PG_DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
QDRANT_URL = "http://localhost:6333"
QDRANT_API_KEY = "79edecc663271e84a5a559c89038cd20276098101a68ad8995c20428d9a47560"
QDRANT_COLLECTION = "soul_memories"
EMBED_MODEL = "intfloat/multilingual-e5-base"  # mismo modelo que usa mcp_server_v3.py (768 dims)

TEST_QUERIES = [
    "decisiones arquitecturales de William",
    "fix de launchers de agentes SEAL",
    "bitemporalidad en Neo4j",
    "autorización de NEXUS para modificar agentes",
    "sistema de recompensa dopamina reinforcement learning",
    "heartbeat y monitoreo de agentes",
    "restart agentes después de kill",
    "MCP server SSE socket",
    "working memory persistente",
    "atención selectiva salience filter",
    "session distill consolidación periódica",
    "memory hybrid search Qdrant PostgreSQL",
    "cadena de mando equipo SEAL",
    "William autoridad suprema",
    "ADA cabeza de ejecución",
    "JARVIS arquitectura estrategia",
    "ALICE traductora documentación",
    "bootcontext identidad OCEAN",
    "active recall KAIROS sesión",
    "procedure store auto-aprendizaje",
    "belief inspector pre-acción crítica",
    "connectome build Neo4j edges",
    "backfill bitemporalidad 40800 edges",
    "ModernHopfieldNetwork re-ranking",
    "seal memory API producción",
    "modelo agnostico DGX Spark",
    "PyTorch nightly cu128 sm_120",
    "filtro relevancia webchat",
    "cita fuente autorización William",
    "Henry pseudónimo Kinger",
    "regla webchat ensure_ascii UTF-8",
    "compactación contexto sesión",
    "RESURRECT sistema recuperación",
    "heartbeat DUM monitoreo",
    "instinct evolve EMA success rate",
    "memory invalidate temporal fact",
    "reasoning trace store outcome",
    "self reflect emotional state",
    "inner thoughts NEXUS",
    "ESAN demo coordinación equipo",
    "GTL consulting aduanas exportación",
    "medical AI HIPAA data sovereignty",
    "MedGemma fine-tuning SEAL pipeline",
    "AXION platform enterprise Latin America",
    "DreamServer dual-node RTX 5090 DGX Spark",
    "cognee ingest graph knowledge",
    "Qdrant vector similarity search",
    "PostgreSQL pgvector embeddings",
    "Neo4j graph conectome",
    "CAMEL-AI OASIS simulación",
    "GAP-A sistema recompensa implementación",
    "GAP-B working memory buffer TTL",
    "GAP-C salience filter pipeline mensajes",
    "GAP-D sueño consolidación poda memoria",
    "GAP-E imaginación simulación hipotética",
    "GAP-F teoría de la mente peer model",
    "memory_store category importance scope",
    "memory_search query recencia importancia",
    "soul_snapshot estado completo agente",
    "reflection synthesize horizon recent",
    "session capture hook post-turno",
    "pre sleep distill noche",
    "cold archive migrate memorias viejas",
    "memory utility update frecuencia uso",
    "connectome ltp long-term potentiation",
    "connectome smart route camino no obvio",
    "connectome entity extracción hechos",
    "temporal query bitemporalidad valid_at",
    "identity eval coherencia identidad",
    "ocean auto calibrate personalidad",
    "peer model William estado mental",
    "instinct create trigger action",
    "instinct consolidate patrones",
    "rule set regla crítica agente",
    "event log append milestone command",
    "erl inject experiential reinforcement",
    "dmem store memoria declarativa",
    "magma retrieve grafo latente",
    "memory delta sync sincronización",
    "memory communities clustering",
    "memory flare urgente crítica",
    "sleep gate mood recuperación",
    "soul activate desencadenar",
    "soul synthesize narrativa",
    "ace curator curaduría automática",
    "brain health report diagnóstico",
    "microcompact text compresión",
    "observation analyze señal externa",
    "latent graph retrieve contexto implícito",
    "procedure record outcome éxito",
    "procedure search recuperar procedimiento",
    "session list historial sesiones",
    "session recall recuperar sesión",
    "tree stats árbol memoria jerarquía",
    "memory type stats distribución tipos",
    "memory list agente categoría",
    "memory update editar contenido",
    "memory share promote scope team",
    "memory broadcast equipo notificación",
    "memory decompress restaurar",
    "memory prefetch anticipación turno",
    "memory feedback refuerzo William",
    "memory cross search todos agentes",
]


def hopfield_attention_scores(query_vec: np.ndarray, vecs: np.ndarray, beta: float = 1.0) -> np.ndarray:
    """Modern Hopfield attention scores — same logic as mcp_server_v3.py"""
    scores = vecs @ query_vec  # cosine-like dot product
    scores = np.exp(beta * scores)
    scores = scores / (scores.sum() + 1e-9)
    return scores


async def get_embedding(model: SentenceTransformer, text: str) -> list[float]:
    vec = model.encode(text, normalize_embeddings=True)
    return vec.tolist()


async def search_baseline(
    qdrant: AsyncQdrantClient,
    pool: asyncpg.Pool,
    query: str,
    query_vec: list[float],
    limit: int = 10,
) -> list[dict]:
    """Baseline: pure Qdrant cosine similarity (no Hopfield)"""
    resp = await qdrant.query_points(
        collection_name=QDRANT_COLLECTION,
        query=query_vec,
        limit=limit * 2,
        with_payload=True,
        with_vectors=False,
    )
    results = []
    for r in resp.points:
        results.append({
            "id": r.id,
            "score": r.score,
            "importance": r.payload.get("importance", 5),
            "category": r.payload.get("category", ""),
            "agent": r.payload.get("agent", ""),
        })
    return results[:limit]


async def search_hopfield(
    qdrant: AsyncQdrantClient,
    pool: asyncpg.Pool,
    query: str,
    query_vec: list[float],
    limit: int = 10,
) -> list[dict]:
    """Hopfield re-ranking: cosine candidates → attentional re-order (70/30 blend)"""
    resp = await qdrant.query_points(
        collection_name=QDRANT_COLLECTION,
        query=query_vec,
        limit=limit * 2,
        with_payload=True,
        with_vectors=True,
    )
    candidates = []
    for r in resp.points:
        if r.vector:
            candidates.append({
                "id": r.id,
                "cosine_score": r.score,
                "vector": r.vector,
                "importance": r.payload.get("importance", 5),
                "category": r.payload.get("category", ""),
                "agent": r.payload.get("agent", ""),
            })

    if len(candidates) >= 2:
        hop_q = np.array(query_vec, dtype=np.float32)
        hop_vecs = np.array([c["vector"] for c in candidates], dtype=np.float32)
        hop_scores = hopfield_attention_scores(hop_q, hop_vecs)
        hop_min = float(hop_scores.min())
        hop_range = float(hop_scores.max()) - hop_min
        if hop_range == 0:
            hop_range = 1.0
        for i, c in enumerate(candidates):
            hop_norm = (float(hop_scores[i]) - hop_min) / hop_range
            c["score"] = 0.7 * c["cosine_score"] + 0.3 * hop_norm
            c["hopfield_score"] = round(hop_norm, 4)
    else:
        for c in candidates:
            c["score"] = c["cosine_score"]

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return [{
        "id": c["id"],
        "score": c["score"],
        "importance": c["importance"],
        "category": c["category"],
        "agent": c["agent"],
        "hopfield_score": c.get("hopfield_score", 0.0),
    } for c in candidates[:limit]]


def importance_at_k(results: list[dict], k: int = 5) -> float:
    top = results[:k]
    if not top:
        return 0.0
    return sum(r["importance"] for r in top) / len(top)


def rank_shift(baseline: list[dict], hopfield: list[dict], k: int = 5) -> float:
    """Avg position change of top-k baseline items in Hopfield ranking"""
    base_ids = [r["id"] for r in baseline[:k]]
    hop_ids = [r["id"] for r in hopfield]
    hop_rank = {rid: i for i, rid in enumerate(hop_ids)}
    shifts = []
    for rank, rid in enumerate(base_ids):
        new_rank = hop_rank.get(rid, len(hopfield))
        shifts.append(abs(new_rank - rank))
    return sum(shifts) / len(shifts) if shifts else 0.0


def overlap_at_k(baseline: list[dict], hopfield: list[dict], k: int = 5) -> float:
    base_ids = set(r["id"] for r in baseline[:k])
    hop_ids = set(r["id"] for r in hopfield[:k])
    if not base_ids:
        return 1.0
    return len(base_ids & hop_ids) / len(base_ids)


async def main():
    print("[NEXUS Benchmark] Cargando modelo de embeddings...")
    model = SentenceTransformer(EMBED_MODEL)

    print("[NEXUS Benchmark] Conectando a Qdrant y PostgreSQL...")
    qdrant = AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    pool = await asyncpg.create_pool(PG_DSN, min_size=2, max_size=5)

    queries = TEST_QUERIES[:100]
    print(f"[NEXUS Benchmark] Ejecutando {len(queries)} queries...\n")

    metrics_baseline = []
    metrics_hopfield = []
    rank_shifts = []
    overlaps = []
    latency_base = []
    latency_hop = []
    changed_queries = 0

    for i, q in enumerate(queries):
        query_vec = await get_embedding(model, q)

        t0 = time.perf_counter()
        base_results = await search_baseline(qdrant, pool, q, query_vec, limit=10)
        latency_base.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        hop_results = await search_hopfield(qdrant, pool, q, query_vec, limit=10)
        latency_hop.append((time.perf_counter() - t0) * 1000)

        imp_base = importance_at_k(base_results, 5)
        imp_hop = importance_at_k(hop_results, 5)
        shift = rank_shift(base_results, hop_results, 5)
        overlap = overlap_at_k(base_results, hop_results, 5)

        metrics_baseline.append(imp_base)
        metrics_hopfield.append(imp_hop)
        rank_shifts.append(shift)
        overlaps.append(overlap)
        if overlap < 1.0:
            changed_queries += 1

        if (i + 1) % 10 == 0:
            print(f"  [{i+1:3d}/{len(queries)}] imp_base={np.mean(metrics_baseline):.3f} imp_hop={np.mean(metrics_hopfield):.3f} overlap={np.mean(overlaps):.3f}")

    await pool.close()

    print("\n" + "=" * 60)
    print("RESULTADOS BENCHMARK HOPFIELD — NEXUS Fase 2")
    print("=" * 60)
    avg_imp_base = np.mean(metrics_baseline)
    avg_imp_hop = np.mean(metrics_hopfield)
    avg_shift = np.mean(rank_shifts)
    avg_overlap = np.mean(overlaps)
    avg_lat_base = np.mean(latency_base)
    avg_lat_hop = np.mean(latency_hop)
    coverage = changed_queries / len(queries) * 100

    imp_delta = avg_imp_hop - avg_imp_base
    imp_pct = (imp_delta / avg_imp_base * 100) if avg_imp_base > 0 else 0

    print(f"importance@5 baseline:   {avg_imp_base:.4f}")
    print(f"importance@5 Hopfield:   {avg_imp_hop:.4f}  (delta={imp_delta:+.4f}, {imp_pct:+.1f}%)")
    print(f"avg rank shift:          {avg_shift:.2f} posiciones")
    print(f"overlap@5:               {avg_overlap:.4f}  ({avg_overlap*100:.1f}% mismo top-5)")
    print(f"coverage (queries changed): {coverage:.1f}%  ({changed_queries}/{len(queries)})")
    print(f"latencia baseline avg:   {avg_lat_base:.1f} ms")
    print(f"latencia Hopfield avg:   {avg_lat_hop:.1f} ms  (overhead={avg_lat_hop-avg_lat_base:+.1f} ms)")
    print("=" * 60)

    veredicto = "ACTIVAR" if imp_pct > 0 else "REVISAR"
    print(f"\nVEREDICTO: {veredicto} Hopfield en produccion")
    if imp_pct > 0:
        print(f"  Hopfield mejora importance@5 en {imp_pct:+.1f}% con overhead de {avg_lat_hop-avg_lat_base:.1f}ms")
    else:
        print(f"  Hopfield no mejora calidad en este dataset (delta={imp_delta:+.4f})")

    result = {
        "importance_at_5_baseline": round(avg_imp_base, 4),
        "importance_at_5_hopfield": round(avg_imp_hop, 4),
        "importance_delta": round(imp_delta, 4),
        "importance_pct": round(imp_pct, 2),
        "avg_rank_shift": round(avg_shift, 2),
        "overlap_at_5": round(avg_overlap, 4),
        "coverage_pct": round(coverage, 1),
        "latency_baseline_ms": round(avg_lat_base, 1),
        "latency_hopfield_ms": round(avg_lat_hop, 1),
        "veredicto": veredicto,
        "n_queries": len(queries),
    }

    out_path = "/home/dadito/IA/proyecto-seal/sandbox-agent/agents/NEXUS/benchmark_hopfield_results.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nResultados guardados en: {out_path}")

    return result


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result["veredicto"] == "ACTIVAR" else 1)
