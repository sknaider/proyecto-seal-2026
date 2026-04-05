"""SOUL CONNECTOME — Associative memory via spreading activation.

Inspired by the Drosophila brain model (Shiu et al., Nature 2024).
Instead of LIF neurons + Brian2, we use spreading activation on a
memory graph stored in PostgreSQL.

Nodes = memories (already in DB)
Edges = memory_connections (excitatory/inhibitory, weighted)

MAGMA v1 — Multi-edge typed graph (ADA + JARVIS, 2026-03-31):
  SIMILAR   — semantic similarity (excitatory, sim>0.70)
  CAUSED    — A caused B (temporal heuristic, confidence 0.3-0.9)
  CORRECTED — B corrects A (bidireccional, weight=1/time_delta)
  INFORMED  — memory informed a decision/trace (excitatory)
  CONTRADICTS — A and B contradict each other (inhibitory, LLM-detected)

Created by ADA for Team SEAL.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Optional

from db import get_pool
from embeddings import get_embedding

LOG = logging.getLogger("seal-connectome")

# Decay factors approved by JARVIS (cmd_019)
DECAY_EXCITATORY = 0.6   # propagates further — positive associations spread
DECAY_INHIBITORY = 0.8   # brakes hard — corrections and rules dampen strongly
ACTIVATION_THRESHOLD = 0.10  # minimum activation to be considered "remembered"
MAX_HOPS = 3             # max propagation depth (prevent runaway)
SIMILARITY_THRESHOLD = 0.70  # minimum cosine similarity to auto-create edge
TOP_K_PER_HOP = 5        # competitive inhibition: only top-K nodes propagate per hop
MAX_RESULTS = 15          # max memories returned from activation

# MAGMA edge type → spreading behavior mapping
# excitatory types propagate with DECAY_EXCITATORY
# inhibitory types brake with DECAY_INHIBITORY
MAGMA_EXCITATORY_TYPES = {"excitatory", "similar", "caused", "informed"}
MAGMA_INHIBITORY_TYPES = {"inhibitory", "corrected", "contradicts"}

# CAUSED edge heuristic: memories created within this window (seconds) by same agent
CAUSED_TEMPORAL_WINDOW_S = 30
CAUSED_CONFIDENCE_TEMPORAL = 0.3   # only temporal co-occurrence
CAUSED_CONFIDENCE_HEURISTIC = 0.5  # temporal + related category
CAUSED_CONFIDENCE_EXPLICIT = 0.9   # content references other memory's id


async def build_connections(agent: Optional[str] = None) -> dict:
    """Generate edges between memories based on embedding similarity and category rules.

    Auto-creates:
    1. Excitatory edges between memories with similarity > 0.70
    2. Inhibitory edges from 'correction' memories to the memories they correct
    3. Excitatory edges between memories of same event/session (temporal co-occurrence)

    Returns stats about edges created.
    """
    pool = await get_pool()
    created = 0
    skipped = 0

    async with pool.acquire() as conn:
        # Get all valid memories with embeddings
        where = "embedding IS NOT NULL AND invalid_at IS NULL"
        params = []
        if agent:
            where += " AND agent = $1"
            params = [agent]

        memories = await conn.fetch(
            f"""SELECT id, agent, category, content, embedding, importance, created_at
                FROM memories WHERE {where}
                ORDER BY id""",
            *params,
        )

        if len(memories) < 2:
            return {"created": 0, "skipped": 0, "total_memories": len(memories)}

        # Build similarity-based edges
        for i, m1 in enumerate(memories):
            for m2 in memories[i + 1:]:
                # Calculate cosine similarity using pgvector
                sim = await conn.fetchval(
                    "SELECT 1 - ($1::vector <=> $2::vector)",
                    m1["embedding"], m2["embedding"],
                )

                if sim is None or sim < SIMILARITY_THRESHOLD:
                    continue

                # Determine connection type
                conn_type = _determine_connection_type(m1, m2)
                weight = min(1.0, sim)  # similarity as weight

                # Insert bidirectional edges (skip if exists)
                for src, tgt in [(m1["id"], m2["id"]), (m2["id"], m1["id"])]:
                    try:
                        await conn.execute(
                            """INSERT INTO memory_connections (source_id, target_id, weight, connection_type, origin)
                               VALUES ($1, $2, $3, $4, 'auto')
                               ON CONFLICT (source_id, target_id) DO NOTHING""",
                            src, tgt, weight, conn_type,
                        )
                        created += 1
                    except Exception:
                        skipped += 1

        # MAGMA: CAUSED edges — temporal heuristic
        # If memory B was created within CAUSED_TEMPORAL_WINDOW_S seconds after A
        # by the same agent, with related categories → edge CAUSED A→B
        CAUSAL_RELATED_CATS = {
            ("insight", "decision"), ("decision", "milestone"),
            ("pattern", "correction"), ("insight", "correction"),
            ("emotion", "decision"), ("trust", "decision"),
        }
        sorted_mems = sorted(memories, key=lambda m: m.get("created_at") or "")
        for idx_c, ma in enumerate(sorted_mems):
            for mb in sorted_mems[idx_c + 1:]:
                if ma["agent"] != mb["agent"]:
                    continue
                ta = ma.get("created_at")
                tb = mb.get("created_at")
                if ta is None or tb is None:
                    break
                delta_s = (tb - ta).total_seconds() if hasattr(tb - ta, "total_seconds") else 0
                if delta_s > CAUSED_TEMPORAL_WINDOW_S:
                    break  # sorted — no further pairs within window
                cat_pair = (ma["category"], mb["category"])
                cat_pair_r = (mb["category"], ma["category"])
                if cat_pair in CAUSAL_RELATED_CATS or cat_pair_r in CAUSAL_RELATED_CATS:
                    confidence = CAUSED_CONFIDENCE_HEURISTIC
                else:
                    confidence = CAUSED_CONFIDENCE_TEMPORAL
                try:
                    await conn.execute(
                        """INSERT INTO memory_connections (source_id, target_id, weight, connection_type, origin)
                           VALUES ($1, $2, $3, 'caused', 'auto_magma')
                           ON CONFLICT (source_id, target_id) DO NOTHING""",
                        ma["id"], mb["id"], confidence,
                    )
                    created += 1
                except Exception:
                    skipped += 1

        # Add inhibitory edges for corrections
        corrections = [m for m in memories if m["category"] == "correction"]
        for corr in corrections:
            if corr["embedding"] is None:
                continue
            # Find the memory this correction most likely targets
            target = await conn.fetchrow(
                """SELECT id, 1 - (embedding <=> $1::vector) AS similarity
                   FROM memories
                   WHERE id != $2 AND embedding IS NOT NULL AND invalid_at IS NULL
                     AND category != 'correction'
                   ORDER BY embedding <=> $1::vector
                   LIMIT 1""",
                corr["embedding"], corr["id"],
            )
            if target and target["similarity"] > 0.5:
                try:
                    await conn.execute(
                        """INSERT INTO memory_connections (source_id, target_id, weight, connection_type, origin)
                           VALUES ($1, $2, $3, 'inhibitory', 'auto')
                           ON CONFLICT (source_id, target_id)
                           DO UPDATE SET connection_type = 'inhibitory', weight = $3""",
                        corr["id"], target["id"], min(1.0, target["similarity"]),
                    )
                    created += 1
                except Exception:
                    skipped += 1

    total_edges = await _count_edges(agent)
    return {
        "created": created,
        "skipped": skipped,
        "total_edges": total_edges,
        "total_memories": len(memories),
    }


def _determine_connection_type(m1: dict, m2: dict) -> str:
    """Determine MAGMA edge type between two memories.

    MAGMA v1 rules (ADA + JARVIS schema, 2026-03-31):
    - correction → anything        = corrected  (bidireccional inhibitory)
    - decision  ↔ decision         = inhibitory (competing decisions)
    - Everything else based on sim = similar    (excitatory)
    """
    cat1, cat2 = m1["category"], m2["category"]

    # Corrections → CORRECTED (inhibitory in spreading activation)
    if cat1 == "correction" or cat2 == "correction":
        return "corrected"

    # Competing decisions inhibit each other
    if cat1 == "decision" and cat2 == "decision":
        return "inhibitory"

    # Default: semantic similarity edge
    return "similar"


def _magma_edge_behavior(connection_type: str) -> str:
    """Map MAGMA edge type to spreading activation behavior."""
    if connection_type in MAGMA_EXCITATORY_TYPES:
        return "excitatory"
    return "inhibitory"


async def _count_edges(agent: Optional[str] = None) -> int:
    """Count total edges in the connectome."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if agent:
            return await conn.fetchval(
                """SELECT COUNT(*) FROM memory_connections mc
                   JOIN memories m ON mc.source_id = m.id
                   WHERE m.agent = $1""",
                agent,
            )
        return await conn.fetchval("SELECT COUNT(*) FROM memory_connections")


async def spreading_activation(
    seed_ids: list[int],
    agent: Optional[str] = None,
    max_hops: int = MAX_HOPS,
    threshold: float = ACTIVATION_THRESHOLD,
    top_k_per_hop: int = TOP_K_PER_HOP,
    max_results: int = MAX_RESULTS,
) -> list[dict]:
    """Run spreading activation from seed memories.

    Algorithm (inspired by Drosophila model — Shiu et al., Nature 2024):
    1. Activate seed nodes with energy = 1.0
    2. For each hop:
       - Each active node sends energy through its edges
       - Excitatory: target += source * weight * DECAY_EXCITATORY / in_degree(target)
       - Inhibitory: target -= source * weight * DECAY_INHIBITORY
       - Competitive inhibition: only top-K newly activated nodes propagate next hop
    3. Return top-N activated nodes sorted by activation

    Key differences from v1:
    - In-degree normalization prevents saturation in dense graphs
    - Competitive selection (winner-take-all) keeps activation focused
    - Inhibitory edges are NOT normalized (they brake hard regardless)

    Returns list of {id, content, category, importance, activation, hop} dicts.
    """
    pool = await get_pool()

    # Initialize activation map: {memory_id: energy}
    activation: dict[int, float] = {sid: 1.0 for sid in seed_ids}
    hop_map: dict[int, int] = {sid: 0 for sid in seed_ids}

    async with pool.acquire() as conn:
        # Load full graph into memory (it's small, ~100s of nodes)
        edges = await conn.fetch(
            """SELECT mc.source_id, mc.target_id, mc.weight, mc.connection_type
               FROM memory_connections mc
               JOIN memories m ON mc.source_id = m.id
               WHERE m.invalid_at IS NULL"""
        )

        # Build adjacency list + compute in-degree for normalization
        graph: dict[int, list[tuple[int, float, str]]] = {}
        in_degree: dict[int, int] = {}
        for e in edges:
            src = e["source_id"]
            tgt = e["target_id"]
            if src not in graph:
                graph[src] = []
            graph[src].append((tgt, e["weight"], e["connection_type"]))
            in_degree[tgt] = in_degree.get(tgt, 0) + 1

        # Spreading activation loop
        frontier = set(seed_ids)
        for hop in range(1, max_hops + 1):
            # Accumulate deltas for this hop (don't modify activation mid-hop)
            deltas: dict[int, float] = {}

            for node in frontier:
                if node not in graph:
                    continue
                source_energy = activation.get(node, 0)
                if source_energy <= 0:
                    continue

                for target, weight, conn_type in graph[node]:
                    behavior = _magma_edge_behavior(conn_type)
                    if behavior == "excitatory":
                        # Normalize by sqrt(in-degree): dampens accumulation without
                        # killing propagation (standard in GNNs, analogous to
                        # dendritic attenuation in biological neurons)
                        norm = math.sqrt(max(1, in_degree.get(target, 1)))
                        delta = source_energy * weight * DECAY_EXCITATORY / norm
                    else:  # inhibitory — brakes hard, no normalization
                        delta = -(source_energy * weight * DECAY_INHIBITORY)

                    deltas[target] = deltas.get(target, 0) + delta

            # Apply deltas
            new_activations: list[tuple[int, float]] = []
            for target, delta in deltas.items():
                old = activation.get(target, 0)
                new = max(0.0, min(1.0, old + delta))
                activation[target] = new

                if new > threshold and target not in hop_map:
                    hop_map[target] = hop
                    new_activations.append((target, new))

            # Competitive inhibition: only top-K propagate to next hop
            new_activations.sort(key=lambda x: -x[1])
            frontier = {nid for nid, _ in new_activations[:top_k_per_hop]}

            if not frontier:
                break

        # Get top-N activated nodes (not all above threshold)
        ranked = sorted(activation.items(), key=lambda x: -x[1])
        activated_ids = [mid for mid, energy in ranked if energy > threshold][:max_results]

        if not activated_ids:
            return []

        placeholders = ", ".join(f"${i+1}" for i in range(len(activated_ids)))
        memories = await conn.fetch(
            f"""SELECT id, agent, category, content, importance, valence, arousal
                FROM memories
                WHERE id IN ({placeholders}) AND invalid_at IS NULL""",
            *activated_ids,
        )

    # Build result with activation scores
    results = []
    for m in memories:
        mid = m["id"]
        results.append({
            "id": mid,
            "agent": m["agent"],
            "category": m["category"],
            "content": m["content"],
            "importance": m["importance"],
            "activation": round(activation.get(mid, 0), 4),
            "hop": hop_map.get(mid, -1),
            "valence": round(m["valence"], 2) if m["valence"] is not None else None,
            "arousal": round(m["arousal"], 2) if m["arousal"] is not None else None,
        })

    # Sort by activation (highest first), then by importance
    results.sort(key=lambda x: (-x["activation"], -x["importance"]))
    return results


async def activate_from_query(
    query: str,
    agent: Optional[str] = None,
    n_seeds: int = 3,
    max_hops: int = MAX_HOPS,
    threshold: float = ACTIVATION_THRESHOLD,
    max_results: int = MAX_RESULTS,
) -> list[dict]:
    """Activate the connectome from a natural language query.

    1. Embed the query
    2. Find top-N most similar memories as seeds
    3. Run spreading activation from seeds
    """
    pool = await get_pool()

    try:
        query_vec = await get_embedding(query)
    except Exception as e:
        LOG.warning("Failed to embed query for connectome: %s", e)
        return []

    async with pool.acquire() as conn:
        conditions = ["embedding IS NOT NULL", "invalid_at IS NULL"]
        params = [json.dumps(query_vec), n_seeds]
        idx = 3

        if agent:
            conditions.append(f"agent = ${idx}")
            params.append(agent)

        where = " AND ".join(conditions)
        seeds = await conn.fetch(
            f"""SELECT id FROM memories
                WHERE {where}
                ORDER BY embedding <=> $1::vector
                LIMIT $2""",
            *params,
        )

    if not seeds:
        return []

    seed_ids = [s["id"] for s in seeds]
    return await spreading_activation(seed_ids, agent, max_hops, threshold, max_results=max_results)


async def get_connectome_stats(agent: Optional[str] = None) -> dict:
    """Get statistics about the connectome."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if agent:
            total_memories = await conn.fetchval(
                "SELECT COUNT(*) FROM memories WHERE agent = $1 AND invalid_at IS NULL", agent
            )
            total_edges = await conn.fetchval(
                """SELECT COUNT(*) FROM memory_connections mc
                   JOIN memories m ON mc.source_id = m.id
                   WHERE m.agent = $1""", agent
            )
            exc = await conn.fetchval(
                """SELECT COUNT(*) FROM memory_connections mc
                   JOIN memories m ON mc.source_id = m.id
                   WHERE m.agent = $1 AND mc.connection_type = 'excitatory'""", agent
            )
            inh = await conn.fetchval(
                """SELECT COUNT(*) FROM memory_connections mc
                   JOIN memories m ON mc.source_id = m.id
                   WHERE m.agent = $1 AND mc.connection_type = 'inhibitory'""", agent
            )
        else:
            total_memories = await conn.fetchval(
                "SELECT COUNT(*) FROM memories WHERE invalid_at IS NULL"
            )
            total_edges = await conn.fetchval("SELECT COUNT(*) FROM memory_connections")
            exc = await conn.fetchval(
                "SELECT COUNT(*) FROM memory_connections WHERE connection_type = 'excitatory'"
            )
            inh = await conn.fetchval(
                "SELECT COUNT(*) FROM memory_connections WHERE connection_type = 'inhibitory'"
            )

    density = (total_edges / (total_memories * (total_memories - 1))) if total_memories > 1 else 0

    return {
        "total_memories": total_memories,
        "total_edges": total_edges,
        "excitatory": exc,
        "inhibitory": inh,
        "density": round(density, 4),
        "avg_edges_per_memory": round(total_edges / total_memories, 1) if total_memories else 0,
    }
