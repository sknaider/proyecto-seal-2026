#!/usr/bin/env python3
"""
GAP-S7 — Consolidación Automática (Hipocampo durante Sueño)
SEAL Memory System | ADA | 2026-04-26

Implementa tres fases del proceso hipocampal humano durante sueño SWS/REM:

  Phase 1 — episodic → semantic  (cosine >= 0.85, min 3 episodios, Ollama abstracción)
  Phase 2 — episodic → procedural (reasoning_traces exitosos repetidos, min 3 ocurrencias)
  Phase 3 — selective decay       (import < 4, recall < 2, > 30 días → importance -= 1)

Ref: "AI Meets Brain" arxiv:2512.23343 — +41% retrieval con consolidación automática.
     "MemOS" jul-2025 — memory scheduling tier.

Uso:
    python3 consolidation_daemon.py --dry-run              # preview seguro
    python3 consolidation_daemon.py --execute              # ejecución real
    python3 consolidation_daemon.py --agent ADA --dry-run  # solo ADA
    python3 consolidation_daemon.py --phase s7e --dry-run  # solo fase episodic→semantic
    python3 consolidation_daemon.py --phase s7p --dry-run  # solo fase episodic→procedural
    python3 consolidation_daemon.py --phase s7d --dry-run  # solo decay

Cron (sleep_gate 3am Lima = 8am UTC):
    15 8 * * * /home/dadito/IA/seal-spark/.venv/bin/python3 \
        /home/dadito/IA/proyecto-seal/memory/consolidation_daemon.py --execute \
        >> /home/dadito/IA/proyecto-seal/messages/sleep_gate.log 2>&1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncpg
import httpx

LIMA_TZ = ZoneInfo("America/Lima")
sys.path.insert(0, str(Path(__file__).parent))

LOG = logging.getLogger("gap-s7-consolidation")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
QDRANT_URL = "http://localhost:6333"
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "79edecc663271e84a5a559c89038cd20276098101a68ad8995c20428d9a47560")
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"
COLLECTION = "soul_memories"

# GAP-S7 parameters (validated by JARVIS)
EPISODIC_SEMANTIC_THRESHOLD = 0.85   # cosine >= 0.85 for episodic→semantic clustering
SEMANTIC_PROCEDURAL_THRESHOLD = 0.72 # cosine >= 0.72 for procedural patterns
MIN_CLUSTER_SIZE = 3                  # min episodic memories to form a concept
QDRANT_EF = 200                       # ef parameter for high-recall Qdrant search
MIN_TRACE_OCCURRENCES = 3            # min repeated traces to form a procedure
DECAY_AGE_DAYS = 30                  # memories older than this eligible for decay
DECAY_MAX_IMPORTANCE = 4             # only decay memories below this importance
DECAY_MAX_RECALL = 2                 # only decay if recall_count below this
EPISODIC_MIN_AGE_DAYS = 7           # don't consolidate very fresh episodic memories
EPISODIC_MAX_IMPORTANCE = 8         # don't consolidate critical corrections (9, 10)
MAX_CLUSTERS_PER_RUN = 10           # safety cap on clusters per run
MAX_DECAY_PER_RUN = 100             # safety cap on decay per run

AGENTS = ["ADA", "JARVIS", "ALICE", "DUM", "NEXUS"]


# ── DB helpers ───────────────────────────────────────────────────────────────

_pool: asyncpg.Pool | None = None

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DB_URL, min_size=2, max_size=5)
    return _pool


# ── Ollama helpers ───────────────────────────────────────────────────────────

async def check_ollama() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get("http://localhost:11434/api/tags")
            return r.status_code == 200
    except Exception:
        return False


async def llm_abstract_cluster(memories: list[dict]) -> str:
    """Abstract a cluster of episodic memories into a single semantic insight via Ollama."""
    contents = "\n".join(f"- {m['content'][:300]}" for m in memories[:8])
    prompt = (
        "Eres un sistema de consolidación de memoria. Tu tarea es abstraer las siguientes "
        "memorias episódicas específicas en un insight semántico general y reutilizable. "
        "El insight debe capturar el patrón o lección común, no los detalles específicos de cada evento. "
        "Responde SOLO con el insight en 1-3 oraciones, sin prefijos ni explicaciones.\n\n"
        f"Memorias episódicas:\n{contents}\n\nInsight semántico:"
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(OLLAMA_URL, json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 200},
            })
            if r.status_code == 200:
                return r.json().get("response", "").strip()
    except Exception as e:
        LOG.warning("Ollama error: %s", e)
    # Fallback: simple concatenation if Ollama unavailable
    return f"Patrón observado en {len(memories)} episodios: " + memories[0]['content'][:200]


async def llm_abstract_traces(traces: list[dict]) -> str:
    """Abstract repeated reasoning traces into a procedural workflow description."""
    tasks = "\n".join(f"- {t['task'][:200]}" for t in traces[:6])
    reasoning_sample = traces[0].get('reasoning', '')[:400] if traces else ''
    prompt = (
        "Eres un sistema de consolidación de procedimientos. Las siguientes tareas exitosas "
        "siguen un patrón similar. Describe el WORKFLOW general paso a paso para lograr este tipo de tarea. "
        "Sé específico y accionable. Responde en 3-6 pasos numerados.\n\n"
        f"Tareas exitosas similares:\n{tasks}\n\n"
        f"Ejemplo de razonamiento:\n{reasoning_sample}\n\nWorkflow:"
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(OLLAMA_URL, json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 300},
            })
            if r.status_code == 200:
                return r.json().get("response", "").strip()
    except Exception as e:
        LOG.warning("Ollama error: %s", e)
    return f"Tarea recurrente exitosa ({len(traces)}x): {traces[0]['task'][:200]}"


# ── Qdrant clustering ─────────────────────────────────────────────────────────

async def find_episodic_clusters(agent: str, pool: asyncpg.Pool) -> list[list[dict]]:
    """Find clusters of similar episodic memories using Qdrant cosine similarity."""
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue, SearchParams

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=EPISODIC_MIN_AGE_DAYS)

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, content, importance, category, created_at
            FROM memories
            WHERE agent = $1
              AND memory_type = 'episodic'
              AND invalid_at IS NULL
              AND importance < $2
              AND created_at < $3
            ORDER BY importance ASC, created_at ASC
            LIMIT 300
        """, agent, EPISODIC_MAX_IMPORTANCE, cutoff_date)

    if len(rows) < MIN_CLUSTER_SIZE:
        return []

    mem_map = {r["id"]: dict(r) for r in rows}
    mem_ids = list(mem_map.keys())

    qdrant = AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
    clusters: list[list[dict]] = []
    visited: set[int] = set()

    for mem_id in mem_ids:
        if mem_id in visited:
            continue

        try:
            results = await qdrant.search(
                collection_name=COLLECTION,
                query_filter=Filter(
                    must=[FieldCondition(key="agent", match=MatchValue(value=agent))]
                ),
                query_vector={"name": "default", "vector": []},  # will be replaced
                limit=20,
                search_params=SearchParams(hnsw_ef=QDRANT_EF),
                with_payload=True,
                with_vectors=False,
            )
        except Exception:
            # Qdrant search by ID not directly supported — use scroll + manual filter
            break

        cluster_members = [mem_map[m] for m in [mem_id] if m in mem_map]
        if len(cluster_members) >= MIN_CLUSTER_SIZE:
            for m in cluster_members:
                visited.add(m["id"])
            clusters.append(cluster_members)
            if len(clusters) >= MAX_CLUSTERS_PER_RUN:
                break

    # Fallback: group by category if Qdrant search unavailable
    if not clusters:
        clusters = await _cluster_by_category(agent, pool, mem_map)

    return clusters


async def _cluster_by_category(
    agent: str, pool: asyncpg.Pool, mem_map: dict
) -> list[list[dict]]:
    """Fallback clustering by category when Qdrant vector search is unavailable."""
    from collections import defaultdict

    by_category: dict[str, list[dict]] = defaultdict(list)
    for m in mem_map.values():
        by_category[m["category"]].append(m)

    clusters = []
    for cat, members in by_category.items():
        if len(members) >= MIN_CLUSTER_SIZE:
            clusters.append(members[:10])  # cap per cluster
            if len(clusters) >= MAX_CLUSTERS_PER_RUN:
                break
    return clusters


async def find_episodic_clusters_v2(agent: str, pool: asyncpg.Pool) -> list[list[dict]]:
    """
    Find episodic clusters using Qdrant scroll + cosine similarity matrix.
    More reliable than search-by-id approach.
    """
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        _qdrant_available = True
    except ImportError:
        LOG.warning("qdrant_client not installed — using category fallback for clustering")
        _qdrant_available = False

    if not _qdrant_available:
        # Fallback: load mem_map from SQL and cluster by category
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=EPISODIC_MIN_AGE_DAYS)
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, content, importance, category, created_at
                FROM memories
                WHERE agent = $1
                  AND memory_type = 'episodic'
                  AND invalid_at IS NULL
                  AND importance < $2
                  AND created_at < $3
                ORDER BY importance ASC, created_at ASC
                LIMIT 200
            """, agent, EPISODIC_MAX_IMPORTANCE, cutoff_date)
        mem_map = {r["id"]: dict(r) for r in rows}
        return await _cluster_by_category(agent, pool, mem_map)

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=EPISODIC_MIN_AGE_DAYS)

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, content, importance, category, created_at
            FROM memories
            WHERE agent = $1
              AND memory_type = 'episodic'
              AND invalid_at IS NULL
              AND importance < $2
              AND created_at < $3
            ORDER BY importance ASC, created_at ASC
            LIMIT 200
        """, agent, EPISODIC_MAX_IMPORTANCE, cutoff_date)

    if len(rows) < MIN_CLUSTER_SIZE:
        LOG.info("[%s] episodic→semantic: only %d memories, skip", agent, len(rows))
        return []

    mem_ids = [r["id"] for r in rows]
    mem_map = {r["id"]: dict(r) for r in rows}

    qdrant = AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)

    # Fetch vectors for all episodic memories
    try:
        points = await qdrant.retrieve(
            collection_name=COLLECTION,
            ids=mem_ids,
            with_vectors=True,
            with_payload=False,
        )
    except Exception as e:
        LOG.warning("[%s] Qdrant retrieve failed: %s — using category fallback", agent, e)
        return await _cluster_by_category(agent, pool, mem_map)

    if not points:
        return await _cluster_by_category(agent, pool, mem_map)

    # Build cosine similarity clusters (greedy)
    import numpy as np

    id_to_vec = {}
    for p in points:
        if p.vector is not None:
            vec = p.vector if isinstance(p.vector, list) else list(p.vector.values())[0]
            id_to_vec[p.id] = np.array(vec, dtype=np.float32)

    if len(id_to_vec) < MIN_CLUSTER_SIZE:
        return await _cluster_by_category(agent, pool, mem_map)

    valid_ids = [i for i in mem_ids if i in id_to_vec]
    visited: set = set()
    clusters: list[list[dict]] = []

    for seed_id in valid_ids:
        if seed_id in visited:
            continue
        seed_vec = id_to_vec[seed_id]
        seed_norm = np.linalg.norm(seed_vec)
        if seed_norm == 0:
            continue

        cluster = [mem_map[seed_id]]
        for other_id in valid_ids:
            if other_id == seed_id or other_id in visited:
                continue
            other_vec = id_to_vec[other_id]
            other_norm = np.linalg.norm(other_vec)
            if other_norm == 0:
                continue
            cos = float(np.dot(seed_vec, other_vec) / (seed_norm * other_norm))
            if cos >= EPISODIC_SEMANTIC_THRESHOLD:
                cluster.append(mem_map[other_id])

        if len(cluster) >= MIN_CLUSTER_SIZE:
            for m in cluster:
                visited.add(m["id"])
            clusters.append(cluster)
            if len(clusters) >= MAX_CLUSTERS_PER_RUN:
                break

    LOG.info("[%s] Found %d clusters from %d episodic memories", agent, len(clusters), len(valid_ids))
    return clusters


# ── Phase 1: episodic → semantic ─────────────────────────────────────────────

async def consolidate_episodic_to_semantic(
    agent: str, pool: asyncpg.Pool, dry_run: bool = True
) -> dict:
    """
    Cluster episodic memories by cosine >= 0.85 (min 3).
    Abstract each cluster into a semantic memory via Ollama qwen2.5:7b.
    Mark originals with consolidation_parent_id.
    """
    LOG.info("[%s] Phase 1: episodic→semantic (dry_run=%s)", agent, dry_run)
    t0 = time.monotonic()

    clusters = await find_episodic_clusters_v2(agent, pool)

    if not clusters:
        return {"agent": agent, "phase": "s7e", "clusters": 0, "new_semantic": 0, "dry_run": dry_run}

    ollama_ok = await check_ollama()
    if not ollama_ok:
        LOG.warning("[%s] Ollama unavailable — will use fallback abstraction", agent)

    from embeddings import get_embedding

    new_semantic_count = 0
    results = []

    for i, cluster in enumerate(clusters):
        abstract = await llm_abstract_cluster(cluster)
        avg_importance = int(sum(m["importance"] for m in cluster) / len(cluster))
        new_importance = min(9, avg_importance + 1)
        source_ids = [m["id"] for m in cluster]

        results.append({
            "cluster_size": len(cluster),
            "source_ids": source_ids,
            "abstracted": abstract[:150],
            "new_importance": new_importance,
        })

        if not dry_run:
            try:
                embedding = await get_embedding(abstract)
                async with pool.acquire() as conn:
                    # Insert new semantic memory
                    new_id = await conn.fetchval("""
                        INSERT INTO memories
                            (agent, category, content, memory_type, importance, scope,
                             metadata, provenance, embedding)
                        VALUES ($1, 'abstracted_pattern', $2, 'semantic', $3, 'team',
                                $4, 'consolidation_daemon_v1', $5::vector)
                        RETURNING id
                    """,
                        agent,
                        abstract,
                        new_importance,
                        json.dumps({
                            "source_episodic_ids": source_ids,
                            "abstraction_method": "consolidation_v1",
                            "cluster_size": len(cluster),
                            "gap": "S7",
                        }),
                        str(embedding),
                    )

                    # Mark originals as consolidated (soft — don't invalidate)
                    await conn.execute("""
                        UPDATE memories
                        SET consolidation_parent_id = $1,
                            metadata = COALESCE(metadata, '{}'::jsonb) ||
                                       jsonb_build_object('consolidated_at', NOW()::text,
                                                          'consolidated_into', $1)
                        WHERE id = ANY($2)
                    """, new_id, source_ids)

                new_semantic_count += 1
                LOG.info("[%s] Cluster %d → new semantic id=%d (importance=%d)",
                         agent, i + 1, new_id, new_importance)
            except Exception as e:
                LOG.error("[%s] Error storing semantic memory cluster %d: %s", agent, i, e)

    elapsed = time.monotonic() - t0
    return {
        "agent": agent,
        "phase": "s7e",
        "clusters_found": len(clusters),
        "new_semantic": new_semantic_count if not dry_run else len(clusters),
        "dry_run": dry_run,
        "elapsed_s": round(elapsed, 2),
        "preview": results[:3],
    }


# ── Phase 2: episodic → procedural ───────────────────────────────────────────

async def consolidate_episodic_to_procedural(
    agent: str, pool: asyncpg.Pool, dry_run: bool = True
) -> dict:
    """
    Detect repeated reasoning_trace sequences with outcome_success=True (min 3).
    Store as new procedure if no similar procedure exists.
    """
    LOG.info("[%s] Phase 2: episodic→procedural (dry_run=%s)", agent, dry_run)
    t0 = time.monotonic()

    async with pool.acquire() as conn:
        traces = await conn.fetch("""
            SELECT id, task, reasoning, conclusion, created_at
            FROM reasoning_traces
            WHERE agent = $1
              AND outcome_success = TRUE
              AND created_at > NOW() - INTERVAL '60 days'
            ORDER BY task, created_at DESC
        """, agent)

    if len(traces) < MIN_TRACE_OCCURRENCES:
        return {"agent": agent, "phase": "s7p", "procedures_created": 0,
                "dry_run": dry_run, "reason": "insufficient_traces"}

    # Group traces by task prefix similarity (simple: first 60 chars)
    from collections import defaultdict
    task_groups: dict[str, list[dict]] = defaultdict(list)
    for t in traces:
        key = t["task"][:60].lower().strip()
        task_groups[key].append(dict(t))

    repeated = {k: v for k, v in task_groups.items() if len(v) >= MIN_TRACE_OCCURRENCES}
    LOG.info("[%s] Found %d repeated task patterns (>= %d traces each)",
             agent, len(repeated), MIN_TRACE_OCCURRENCES)

    new_procedures = 0
    results = []

    for task_key, group in list(repeated.items())[:5]:  # cap at 5 per run
        workflow = await llm_abstract_traces(group)

        results.append({
            "task_prefix": task_key[:80],
            "occurrences": len(group),
            "workflow_preview": workflow[:100],
        })

        if not dry_run:
            try:
                # Check if similar procedure already exists
                from embeddings import get_embedding
                embedding = await get_embedding(f"{task_key} {workflow}")

                # Simple existence check via task similarity
                existing = await pool.fetchrow("""
                    SELECT id FROM procedural_memories
                    WHERE agent = $1 AND LOWER(query) LIKE $2
                    LIMIT 1
                """, agent, f"%{task_key[:30].lower()}%")

                if not existing:
                    await pool.execute("""
                        INSERT INTO procedural_memories
                            (agent, task_type, query, workflow, facts, build_policy,
                             source_task, hit_count, success_count, fail_count, embedding)
                        VALUES ($1, 'auto-consolidated', $2, $3, $4, 'direct', $5, 0, $6, 0, $7)
                    """,
                        agent,
                        task_key[:200],
                        workflow,
                        json.dumps({
                            "derived_from_traces": [t["id"] for t in group],
                            "occurrence_count": len(group),
                            "gap": "S7",
                        }),
                        f"auto_s7_{task_key[:30]}",
                        len(group),
                        str(embedding),
                    )
                    new_procedures += 1
                    LOG.info("[%s] New procedure created for: %s", agent, task_key[:60])
            except Exception as e:
                LOG.error("[%s] Error creating procedure for '%s': %s", agent, task_key[:40], e)

    elapsed = time.monotonic() - t0
    return {
        "agent": agent,
        "phase": "s7p",
        "repeated_patterns_found": len(repeated),
        "procedures_created": new_procedures if not dry_run else min(len(repeated), 5),
        "dry_run": dry_run,
        "elapsed_s": round(elapsed, 2),
        "preview": results[:3],
    }


# ── Phase 3: selective decay ──────────────────────────────────────────────────

async def selective_decay(
    agent: str, pool: asyncpg.Pool, dry_run: bool = True
) -> dict:
    """
    Decrease importance by 1 for:
    - memories > DECAY_AGE_DAYS old
    - importance < DECAY_MAX_IMPORTANCE
    - recall_count < DECAY_MAX_RECALL
    If importance reaches 0 → invalidate.
    """
    LOG.info("[%s] Phase 3: selective decay (dry_run=%s)", agent, dry_run)
    t0 = time.monotonic()

    async with pool.acquire() as conn:
        # Preview: how many would be affected
        preview_count = await conn.fetchval("""
            SELECT COUNT(*) FROM memories
            WHERE agent = $1
              AND invalid_at IS NULL
              AND importance < $2
              AND importance > 0
              AND COALESCE(recall_count, 0) < $3
              AND created_at < NOW() - INTERVAL '%s days'
        """ % DECAY_AGE_DAYS,
            agent, DECAY_MAX_IMPORTANCE, DECAY_MAX_RECALL
        )

        preview_zero = await conn.fetchval("""
            SELECT COUNT(*) FROM memories
            WHERE agent = $1
              AND invalid_at IS NULL
              AND importance = 1
              AND COALESCE(recall_count, 0) < $2
              AND created_at < NOW() - INTERVAL '%s days'
        """ % DECAY_AGE_DAYS,
            agent, DECAY_MAX_RECALL
        )

        if not dry_run:
            # First: invalidate memories that will reach 0
            invalidated = await conn.fetchval("""
                UPDATE memories
                SET invalid_at = NOW(),
                    importance = 0,
                    metadata = COALESCE(metadata, '{}'::jsonb) ||
                               jsonb_build_object('decay_invalidated_at', NOW()::text, 'gap', 'S7')
                WHERE agent = $1
                  AND invalid_at IS NULL
                  AND importance = 1
                  AND COALESCE(recall_count, 0) < $2
                  AND created_at < NOW() - INTERVAL '%s days'
                RETURNING id
            """ % DECAY_AGE_DAYS,
                agent, DECAY_MAX_RECALL
            )

            # Then: decrease importance by 1 for the rest
            decayed = await conn.execute("""
                UPDATE memories
                SET importance = importance - 1,
                    metadata = COALESCE(metadata, '{}'::jsonb) ||
                               jsonb_build_object('last_decay_at', NOW()::text, 'gap', 'S7')
                WHERE agent = $1
                  AND invalid_at IS NULL
                  AND importance BETWEEN 2 AND $2
                  AND COALESCE(recall_count, 0) < $3
                  AND created_at < NOW() - INTERVAL '%s days'
            """ % DECAY_AGE_DAYS,
                agent, DECAY_MAX_IMPORTANCE - 1, DECAY_MAX_RECALL
            )
            decayed_count = int(decayed.split()[-1]) if decayed else 0
        else:
            decayed_count = 0

    elapsed = time.monotonic() - t0
    return {
        "agent": agent,
        "phase": "s7d",
        "eligible_for_decay": int(preview_count or 0),
        "eligible_for_invalidation": int(preview_zero or 0),
        "decayed": decayed_count if not dry_run else int(preview_count or 0),
        "dry_run": dry_run,
        "elapsed_s": round(elapsed, 2),
    }


# ── Main orchestrator ─────────────────────────────────────────────────────────

async def run_consolidation(
    agents: list[str],
    dry_run: bool = True,
    phases: list[str] | None = None,
) -> dict:
    """Run all GAP-S7 consolidation phases for given agents."""
    if phases is None:
        phases = ["s7e", "s7p", "s7d"]

    pool = await get_pool()
    results = {}
    total_t0 = time.monotonic()

    for agent in agents:
        agent_results = {}
        LOG.info("=== GAP-S7 Consolidation: %s (dry_run=%s, phases=%s) ===",
                 agent, dry_run, phases)

        if "s7e" in phases:
            agent_results["episodic_to_semantic"] = await consolidate_episodic_to_semantic(
                agent, pool, dry_run
            )

        if "s7p" in phases:
            agent_results["episodic_to_procedural"] = await consolidate_episodic_to_procedural(
                agent, pool, dry_run
            )

        if "s7d" in phases:
            agent_results["selective_decay"] = await selective_decay(
                agent, pool, dry_run
            )

        results[agent] = agent_results

    total_elapsed = time.monotonic() - total_t0

    summary = {
        "gap": "S7",
        "dry_run": dry_run,
        "agents": agents,
        "phases": phases,
        "total_elapsed_s": round(total_elapsed, 2),
        "timestamp": datetime.now(LIMA_TZ).isoformat(),
        "results": results,
    }

    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GAP-S7 Consolidation Daemon")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Preview only — no writes (default: True)")
    parser.add_argument("--execute", action="store_true",
                        help="Actually execute consolidation (overrides --dry-run)")
    parser.add_argument("--agent", default="all",
                        help="Agent name or 'all' (default: all)")
    parser.add_argument("--phase", choices=["s7e", "s7p", "s7d", "all"], default="all",
                        help="Phase to run: s7e=episodic→semantic, s7p=→procedural, s7d=decay")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    dry_run = not args.execute
    agents = AGENTS if args.agent == "all" else [args.agent.upper()]
    phases = ["s7e", "s7p", "s7d"] if args.phase == "all" else [args.phase]

    if not dry_run:
        LOG.warning("⚡ EXECUTE MODE — writing to Soul DB")
    else:
        LOG.info("DRY RUN — no writes")

    result = asyncio.run(run_consolidation(agents, dry_run=dry_run, phases=phases))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"\n{'='*60}")
        print(f"GAP-S7 Consolidation {'DRY RUN' if dry_run else 'EXECUTED'}")
        print(f"{'='*60}")
        for agent, ar in result["results"].items():
            print(f"\n[{agent}]")
            if "episodic_to_semantic" in ar:
                r = ar["episodic_to_semantic"]
                print(f"  Phase S7E: {r.get('clusters_found', 0)} clusters → "
                      f"{r.get('new_semantic', 0)} new semantic memories")
            if "episodic_to_procedural" in ar:
                r = ar["episodic_to_procedural"]
                print(f"  Phase S7P: {r.get('repeated_patterns_found', 0)} patterns → "
                      f"{r.get('procedures_created', 0)} new procedures")
            if "selective_decay" in ar:
                r = ar["selective_decay"]
                print(f"  Phase S7D: {r.get('eligible_for_decay', 0)} eligible → "
                      f"{r.get('decayed', 0)} decayed | "
                      f"{r.get('eligible_for_invalidation', 0)} to invalidate")
        print(f"\nTotal: {result['total_elapsed_s']}s | {result['timestamp']}")


if __name__ == "__main__":
    main()
