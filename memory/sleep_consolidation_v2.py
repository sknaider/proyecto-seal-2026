#!/usr/bin/env python3
"""
sleep_consolidation_v2.py — REM-style sleep consolidation (GAP 2)

Implementa funciones que extienden daily_sleep / weekly_sleep:
  - reinforce_connectome (GAP 2.C) — hebbian learning sobre memory_edges
  - abstract_patterns (GAP 2.A) — v2.2: cluster corrections → instincts
  - induce_schemas (GAP 2.B) — v2.4: cluster decisions → reusable schemas
  - counterfactual_replay (GAP 2.D) — TODO v2.5
  - cross_agent_consolidation (GAP 2.E) — TODO v2.6
  - procedural_rehearsal (GAP 2.F) — v2.3: re-embed stale procedures

Uso standalone:
    python3 sleep_consolidation_v2.py connectome JARVIS
    python3 sleep_consolidation_v2.py connectome ALL
    python3 sleep_consolidation_v2.py patterns JARVIS
    python3 sleep_consolidation_v2.py patterns ALL
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import asyncpg
import httpx

sys.path.insert(0, os.path.dirname(__file__))
from db import get_pool, close_pool
from embeddings import get_embedding

LIMA_TZ = ZoneInfo("America/Lima")

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"

# Pattern abstraction constants
PATTERN_SIM_THRESHOLD = 0.85      # min similarity to consider two corrections the same pattern
PATTERN_CANDIDATE_MIN = 3          # 3+ similar corrections → instinct candidate (lower strength)
PATTERN_INSTINCT_MIN = 5           # 5+ → promoted to full instinct
PATTERN_SAMPLE_LIMIT = 200         # max corrections per agent to scan (recency bias)
PATTERN_NEIGHBOR_LIMIT = 20        # ANN neighbors per correction
PATTERN_INSTINCT_STRENGTH_CANDIDATE = 0.4
PATTERN_INSTINCT_STRENGTH_FULL = 0.65

# Procedural rehearsal constants (GAP 2.F)
REHEARSE_MIN_HIT_COUNT = 2          # rehearse procedurals used at least 2 times
REHEARSE_STALE_DAYS = 7             # rehearse if not refreshed in this many days
REHEARSE_BATCH_LIMIT = 50           # max procedurals to rehearse per run (rate limit Ollama)


async def _llm_generate(prompt: str, max_tokens: int = 300) -> str:
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": max_tokens},
        })
        resp.raise_for_status()
        return resp.json().get("response", "").strip()

HEBBIAN_REINFORCE_DELTA = 0.1
HEBBIAN_DECAY_DAYS = 14
HEBBIAN_DECAY_FACTOR = 0.95
HEBBIAN_MAX_WEIGHT = 1.0
HEBBIAN_MIN_WEIGHT = 0.01
HEBBIAN_PRUNE_BELOW = 0.005


class _UnionFind:
    """Simple union-find for clustering correction IDs."""
    def __init__(self):
        self._parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        self._parent[self.find(x)] = self.find(y)

    def clusters(self) -> dict[int, list[int]]:
        result: dict[int, list[int]] = {}
        for node in self._parent:
            root = self.find(node)
            result.setdefault(root, []).append(node)
        return result


async def abstract_patterns(pool: asyncpg.Pool, agent: str | None = None) -> dict:
    """
    GAP 2.A — Pattern abstraction from repeated corrections.

    Algorithm:
      1. Sample recent corrections per agent (last 30d, max PATTERN_SAMPLE_LIMIT)
      2. ANN search to find pairs with cosine sim > PATTERN_SIM_THRESHOLD
      3. Cluster via union-find
      4. Clusters of 3+ → synthesize instinct via LLM
      5. Insert into instincts with source='pattern_abstraction'
         - 3-4 members → candidate (strength=0.4)
         - 5+ members → full instinct (strength=0.65)

    Returns: stats dict with keys instincts_created, candidates_created, clusters_found.
    """
    agents_to_process: list[str] = []
    if agent and agent != "ALL":
        agents_to_process = [agent]
    else:
        rows = await pool.fetch(
            "SELECT DISTINCT agent FROM memories WHERE category = 'correction' AND invalid_at IS NULL"
        )
        agents_to_process = [r["agent"] for r in rows]

    stats = {"instincts_created": 0, "candidates_created": 0, "clusters_found": 0, "skipped_duplicate": 0}

    for ag in agents_to_process:
        # Step 1: Sample recent corrections with embeddings
        corrections = await pool.fetch("""
            SELECT id, content, embedding::text AS emb_str
            FROM memories
            WHERE agent = $1
              AND category = 'correction'
              AND invalid_at IS NULL
              AND embedding IS NOT NULL
              AND created_at > NOW() - INTERVAL '30 days'
            ORDER BY created_at DESC
            LIMIT $2
        """, ag, PATTERN_SAMPLE_LIMIT)

        if len(corrections) < PATTERN_CANDIDATE_MIN:
            continue

        # Build id → content mapping
        id_to_content: dict[int, str] = {r["id"]: r["content"] for r in corrections}
        correction_ids = list(id_to_content.keys())

        # Step 2: ANN search — for each correction, find its neighbors with sim > threshold
        uf = _UnionFind()
        # Add all IDs to union-find
        for cid in correction_ids:
            uf.find(cid)

        for row in corrections:
            cid = row["id"]
            # Use the stored embedding vector directly — cast via pgvector
            neighbors = await pool.fetch("""
                SELECT id
                FROM memories
                WHERE agent = $1
                  AND category = 'correction'
                  AND invalid_at IS NULL
                  AND id != $2
                  AND id = ANY($3::bigint[])
                  AND 1 - (embedding <=> (
                      SELECT embedding FROM memories WHERE id = $2
                  )) > $4
                ORDER BY embedding <=> (SELECT embedding FROM memories WHERE id = $2)
                LIMIT $5
            """, ag, cid, correction_ids, PATTERN_SIM_THRESHOLD, PATTERN_NEIGHBOR_LIMIT)

            for nb in neighbors:
                uf.union(cid, nb["id"])

        # Step 3: Extract clusters of 3+
        clusters = {root: members for root, members in uf.clusters().items()
                    if len(members) >= PATTERN_CANDIDATE_MIN}

        if not clusters:
            continue

        stats["clusters_found"] += len(clusters)

        for root, members in clusters.items():
            size = len(members)
            is_full = size >= PATTERN_INSTINCT_MIN
            strength = PATTERN_INSTINCT_STRENGTH_FULL if is_full else PATTERN_INSTINCT_STRENGTH_CANDIDATE

            # Collect sample texts (up to 8)
            sample_texts = [id_to_content[m] for m in members[:8]]
            sample_block = "\n".join(f"- {t[:200]}" for t in sample_texts)

            # Step 4: Synthesize trigger + action via LLM
            prompt = f"""You are analyzing repeated corrections made to AI agent {ag}.
These {size} corrections share a common pattern (high semantic similarity):

{sample_block}

From this pattern, derive a behavioral instinct with exactly two lines:
TRIGGER: <one sentence — when does this situation occur?>
ACTION: <one sentence — what should the agent do when triggered?>

Be concise. Write in the same language as the corrections (Spanish/English mixed is fine)."""

            try:
                llm_out = await _llm_generate(prompt, max_tokens=200)
            except Exception:
                llm_out = ""

            # Parse TRIGGER / ACTION from LLM output
            trigger = ""
            action = ""
            for line in llm_out.splitlines():
                line = line.strip()
                if line.upper().startswith("TRIGGER:"):
                    trigger = line[8:].strip()
                elif line.upper().startswith("ACTION:"):
                    action = line[7:].strip()

            if not trigger or not action:
                # Fallback: use first correction as trigger, second as action
                trigger = f"Patrón detectado en {size} correcciones similares para {ag}"
                action = sample_texts[0][:300] if sample_texts else "Ver memorias relacionadas"

            # Step 5: Check for near-duplicate instinct already existing
            trigger_emb = await get_embedding(trigger)
            if trigger_emb:
                existing_similar = await pool.fetchval("""
                    SELECT id FROM instincts
                    WHERE agent = $1
                      AND invalid_at IS NULL
                      AND embedding IS NOT NULL
                      AND 1 - (embedding <=> $2::vector) > 0.92
                    LIMIT 1
                """, ag, json.dumps(trigger_emb))
                if existing_similar:
                    stats["skipped_duplicate"] += 1
                    continue
            else:
                trigger_emb = None

            # Insert into instincts
            meta = json.dumps({
                "source": "pattern_abstraction",
                "cluster_size": size,
                "member_ids": members[:20],
                "is_candidate": not is_full,
            })
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO instincts (
                        agent, trigger_condition, action, strength,
                        success_count, failure_count,
                        valid_from, metadata, embedding
                    ) VALUES (
                        $1, $2, $3, $4::float,
                        0, 0,
                        NOW(), $5::jsonb,
                        $6::vector
                    )
                """, ag, trigger, action, strength,
                    meta,
                    json.dumps(trigger_emb) if trigger_emb else None)

            if is_full:
                stats["instincts_created"] += 1
            else:
                stats["candidates_created"] += 1

    return stats


async def reinforce_connectome(pool: asyncpg.Pool, agent: str | None = None) -> dict:
    """
    GAP 2.C — Hebbian reinforcement del connectome.

    "Neurons that fire together, wire together":
      - Memorias co-activadas en mismo reasoning_trace → edge.weight += delta
      - Edges sin reactivación >14d → weight *= 0.95 (decay)
      - Edges con weight < 0.005 → pruned

    Returns: stats dict.
    """
    stats = {"created": 0, "reinforced": 0, "decayed": 0, "pruned": 0}

    agent_filter = "AND t.agent = $1" if agent and agent != "ALL" else ""
    args = (agent,) if agent and agent != "ALL" else ()

    coactivated_query = f"""
        WITH trace_pairs AS (
            SELECT
                LEAST(m_a.id, m_b.id) AS a_id,
                GREATEST(m_a.id, m_b.id) AS b_id
            FROM reasoning_traces t
            CROSS JOIN LATERAL unnest(t.linked_memory_ids) AS u_a(memory_id)
            CROSS JOIN LATERAL unnest(t.linked_memory_ids) AS u_b(memory_id)
            JOIN memories m_a ON m_a.id = u_a.memory_id
            JOIN memories m_b ON m_b.id = u_b.memory_id
            WHERE u_a.memory_id < u_b.memory_id
              AND m_a.invalid_at IS NULL
              AND m_b.invalid_at IS NULL
              AND t.linked_memory_ids IS NOT NULL
              AND array_length(t.linked_memory_ids, 1) >= 2
              {agent_filter}
        )
        SELECT a_id, b_id, COUNT(*) AS co_count
        FROM trace_pairs
        GROUP BY a_id, b_id
    """
    coactivated = await pool.fetch(coactivated_query, *args)

    async with pool.acquire() as conn:
        async with conn.transaction():
            for row in coactivated:
                a_id, b_id, co_count = row["a_id"], row["b_id"], row["co_count"]
                existing = await conn.fetchrow(
                    "SELECT weight, co_count FROM memory_edges WHERE a_id=$1 AND b_id=$2",
                    a_id, b_id
                )
                if existing is None:
                    initial_weight = min(HEBBIAN_REINFORCE_DELTA * co_count, HEBBIAN_MAX_WEIGHT)
                    await conn.execute("""
                        INSERT INTO memory_edges (a_id, b_id, weight, co_count, last_reinforced)
                        VALUES ($1, $2, $3, $4, now())
                    """, a_id, b_id, initial_weight, co_count)
                    stats["created"] += 1
                else:
                    delta = HEBBIAN_REINFORCE_DELTA * co_count
                    new_weight = min(float(existing["weight"]) + delta, HEBBIAN_MAX_WEIGHT)
                    await conn.execute("""
                        UPDATE memory_edges
                        SET weight = $3, co_count = co_count + $4, last_reinforced = now()
                        WHERE a_id = $1 AND b_id = $2
                    """, a_id, b_id, new_weight, co_count)
                    stats["reinforced"] += 1

            decay_result = await conn.execute(f"""
                UPDATE memory_edges
                SET weight = GREATEST(weight * $1, $2)
                WHERE last_reinforced < now() - interval '{HEBBIAN_DECAY_DAYS} days'
                  AND weight > $2
            """, HEBBIAN_DECAY_FACTOR, HEBBIAN_MIN_WEIGHT)
            stats["decayed"] = int(decay_result.split()[-1]) if decay_result else 0

            prune_result = await conn.execute(
                "DELETE FROM memory_edges WHERE weight < $1",
                HEBBIAN_PRUNE_BELOW
            )
            stats["pruned"] = int(prune_result.split()[-1]) if prune_result else 0

    return stats


async def procedural_rehearsal(pool: asyncpg.Pool, agent: str | None = None) -> dict:
    """
    GAP 2.F — Procedural memory rehearsal during sleep.

    Refreshes embeddings for frequently-used procedures not rehearsed in >7 days.
    Also auto-prunes procedures with >80% fail rate (deactivates them).

    Selects: active=true, hit_count >= REHEARSE_MIN_HIT_COUNT,
             last_rehearsed IS NULL OR < (now - 7 days).
    Embeds: workflow text (falls back to query if workflow empty).

    Returns: stats dict with rehearsed, skipped, errors, skipped_pruned.
    """
    agent_filter = "AND agent = $1" if agent and agent != "ALL" else ""
    args: tuple = (agent, REHEARSE_MIN_HIT_COUNT, REHEARSE_BATCH_LIMIT) if agent and agent != "ALL" \
        else (REHEARSE_MIN_HIT_COUNT, REHEARSE_BATCH_LIMIT)

    # When agent is set: $1=agent, $2=hit_threshold, $3=limit
    # When no agent:     $1=hit_threshold, $2=limit
    hit_p  = 2 if agent and agent != "ALL" else 1
    lim_p  = 3 if agent and agent != "ALL" else 2
    candidates = await pool.fetch(f"""
        SELECT id, agent, query, workflow, hit_count, fail_count
        FROM procedural_memories
        WHERE active = true
          AND hit_count >= ${hit_p}
          AND (last_rehearsed IS NULL OR last_rehearsed < now() - interval '{REHEARSE_STALE_DAYS} days')
          {agent_filter}
        ORDER BY hit_count DESC, updated_at ASC
        LIMIT ${lim_p}
    """, *args)

    stats = {
        "candidates_found": len(candidates),
        "rehearsed": 0,
        "skipped": 0,
        "errors": 0,
        "skipped_pruned": 0,
        "agents_processed": [],
    }

    seen_agents: set[str] = set()
    for proc in candidates:
        # Auto-prune: >5 uses, >80% failure rate → deactivate
        total = proc["hit_count"] or 0
        fails = proc["fail_count"] or 0
        if total >= 5 and fails / total > 0.8:
            await pool.execute(
                "UPDATE procedural_memories SET active=false, last_rehearsed=now() WHERE id=$1",
                proc["id"]
            )
            stats["skipped_pruned"] += 1
            continue

        try:
            text = (proc["workflow"] or "").strip() or proc["query"]
            new_emb = await get_embedding(text)
            if not new_emb:
                stats["errors"] += 1
                continue
            await pool.execute("""
                UPDATE procedural_memories
                SET embedding = $2::vector, last_rehearsed = now(), updated_at = now()
                WHERE id = $1
            """, proc["id"], json.dumps(new_emb))
            stats["rehearsed"] += 1
            seen_agents.add(proc["agent"])
        except Exception:
            stats["errors"] += 1

    stats["agents_processed"] = list(seen_agents)
    return stats


# Schema induction constants (GAP 2.B)
SCHEMA_SIM_THRESHOLD = 0.80      # min similarity to cluster decisions
SCHEMA_MIN_CLUSTER = 3            # min cluster size to induce schema
SCHEMA_SAMPLE_LIMIT = 100         # max decisions per agent to scan
SCHEMA_NEIGHBOR_LIMIT = 15        # ANN neighbors per decision


async def induce_schemas(pool: asyncpg.Pool, agent: str | None = None) -> dict:
    """
    GAP 2.B — Induce reusable schemas from decision patterns.

    Algorithm:
      1. Sample recent decisions per agent (last 30d, importance>=7, max 100)
      2. ANN cluster decisions with cosine sim > SCHEMA_SIM_THRESHOLD
      3. For each cluster of 3+, find linked reasoning_traces (if any)
      4. LLM induces schema: TRIGGER_PATTERN + ACTION_TEMPLATE
      5. Insert into schemas table with embedding (for future retrieval)
      6. Dedup against existing schemas (sim > 0.92)

    Returns: stats dict with schemas_created, clusters_found, skipped_duplicate.
    """
    agents_to_process: list[str] = []
    if agent and agent != "ALL":
        agents_to_process = [agent]
    else:
        rows = await pool.fetch(
            "SELECT DISTINCT agent FROM memories WHERE category='decision' AND invalid_at IS NULL"
        )
        agents_to_process = [r["agent"] for r in rows]

    stats = {"schemas_created": 0, "clusters_found": 0, "skipped_duplicate": 0}

    for ag in agents_to_process:
        decisions = await pool.fetch("""
            SELECT id, content
            FROM memories
            WHERE agent = $1
              AND category = 'decision'
              AND invalid_at IS NULL
              AND embedding IS NOT NULL
              AND importance >= 7
              AND created_at > NOW() - INTERVAL '30 days'
            ORDER BY created_at DESC
            LIMIT $2
        """, ag, SCHEMA_SAMPLE_LIMIT)

        if len(decisions) < SCHEMA_MIN_CLUSTER:
            continue

        id_to_content = {r["id"]: r["content"] for r in decisions}
        decision_ids = list(id_to_content.keys())

        uf = _UnionFind()
        for did in decision_ids:
            uf.find(did)

        for row in decisions:
            did = row["id"]
            neighbors = await pool.fetch("""
                SELECT id FROM memories
                WHERE agent = $1 AND category = 'decision' AND invalid_at IS NULL
                  AND id != $2 AND id = ANY($3::bigint[])
                  AND 1 - (embedding <=> (SELECT embedding FROM memories WHERE id = $2)) > $4
                ORDER BY embedding <=> (SELECT embedding FROM memories WHERE id = $2)
                LIMIT $5
            """, ag, did, decision_ids, SCHEMA_SIM_THRESHOLD, SCHEMA_NEIGHBOR_LIMIT)
            for nb in neighbors:
                uf.union(did, nb["id"])

        clusters = {root: members for root, members in uf.clusters().items()
                    if len(members) >= SCHEMA_MIN_CLUSTER}
        if not clusters:
            continue
        stats["clusters_found"] += len(clusters)

        for root, members in clusters.items():
            sample_texts = [id_to_content[m] for m in members[:6]]
            sample_block = "\n".join(f"- {t[:250]}" for t in sample_texts)

            prompt = f"""Analyzing recurring decisions made by AI agent {ag}.
These {len(members)} decisions share a strong semantic pattern:

{sample_block}

Induce a reusable mental schema with exactly two lines:
TRIGGER: <one sentence — what situation activates this schema?>
ACTION: <one sentence — what action sequence does the agent typically follow?>

Be concise and specific. Match the language of the decisions (Spanish/English ok)."""

            try:
                llm_out = await _llm_generate(prompt, max_tokens=200)
            except Exception:
                llm_out = ""

            trigger = ""
            action = ""
            for line in llm_out.splitlines():
                line = line.strip()
                if line.upper().startswith("TRIGGER:"):
                    trigger = line[8:].strip()
                elif line.upper().startswith("ACTION:"):
                    action = line[7:].strip()

            if not trigger or not action:
                trigger = f"Patrón decisional recurrente ({len(members)} decisiones similares en {ag})"
                action = sample_texts[0][:300] if sample_texts else "Ver decisiones relacionadas"

            # Dedup: check existing schema with similar trigger
            schema_text = f"{trigger} | {action}"
            schema_emb = await get_embedding(schema_text)
            if schema_emb:
                existing = await pool.fetchval("""
                    SELECT id FROM schemas
                    WHERE agent = $1 AND invalid_at IS NULL
                      AND metadata->>'embedding' IS NOT NULL
                    LIMIT 1
                """, ag)
                # Simple dedup by trigger_pattern text similarity (not embedding for now)
                similar = await pool.fetchval("""
                    SELECT id FROM schemas
                    WHERE agent = $1 AND invalid_at IS NULL
                      AND trigger_pattern = $2
                    LIMIT 1
                """, ag, trigger)
                if similar:
                    stats["skipped_duplicate"] += 1
                    continue

            meta = json.dumps({
                "source": "schema_induction",
                "cluster_size": len(members),
                "member_ids": members[:20],
            })
            await pool.execute("""
                INSERT INTO schemas (agent, trigger_pattern, action_template, success_count, metadata)
                VALUES ($1, $2, $3, 0, $4::jsonb)
            """, ag, trigger, action, meta)
            stats["schemas_created"] += 1

    return stats


async def main():
    if len(sys.argv) < 2:
        print("Uso: python3 sleep_consolidation_v2.py <step> [agent]")
        print("Steps: connectome, patterns, rehearsal, schemas")
        sys.exit(1)

    step = sys.argv[1]
    agent = sys.argv[2] if len(sys.argv) > 2 else "ALL"

    pool = await get_pool()
    try:
        if step == "connectome":
            print(f"=== reinforce_connectome (agent={agent}) ===")
            stats = await reinforce_connectome(pool, agent)
            print(f"  Created: {stats['created']}")
            print(f"  Reinforced: {stats['reinforced']}")
            print(f"  Decayed: {stats['decayed']}")
            print(f"  Pruned: {stats['pruned']}")

            total_edges = await pool.fetchval("SELECT COUNT(*) FROM memory_edges")
            avg_weight = await pool.fetchval("SELECT COALESCE(AVG(weight), 0) FROM memory_edges")
            print(f"\n  Total edges: {total_edges}, avg weight: {avg_weight:.3f}")

        elif step == "patterns":
            print(f"=== abstract_patterns (agent={agent}) ===")
            stats = await abstract_patterns(pool, agent)
            print(f"  Clusters found:       {stats['clusters_found']}")
            print(f"  Full instincts:       {stats['instincts_created']}")
            print(f"  Candidate instincts:  {stats['candidates_created']}")
            print(f"  Skipped (duplicate):  {stats['skipped_duplicate']}")

            if agent and agent != "ALL":
                total = await pool.fetchval(
                    "SELECT COUNT(*) FROM instincts WHERE agent=$1 AND invalid_at IS NULL", agent
                )
                pa = await pool.fetchval(
                    "SELECT COUNT(*) FROM instincts WHERE agent=$1 AND invalid_at IS NULL "
                    "AND metadata::jsonb->>'source' = 'pattern_abstraction'", agent
                )
                print(f"\n  Total instincts for {agent}: {total} ({pa} from pattern_abstraction)")
            else:
                pa = await pool.fetchval(
                    "SELECT COUNT(*) FROM instincts WHERE invalid_at IS NULL "
                    "AND metadata::jsonb->>'source' = 'pattern_abstraction'"
                )
                print(f"\n  Total pattern_abstraction instincts (all agents): {pa}")
        elif step == "rehearsal":
            print(f"=== procedural_rehearsal (agent={agent}) ===")
            stats = await procedural_rehearsal(pool, agent)
            print(f"  Rehearsed:    {stats['rehearsed']}")
            print(f"  Skipped:      {stats['skipped']}")
            print(f"  Errors:       {stats['errors']}")
            if stats["rehearsed"]:
                print(f"  Agents:       {stats.get('agents_processed', [])}")

        elif step == "schemas":
            print(f"=== induce_schemas (agent={agent}) ===")
            stats = await induce_schemas(pool, agent)
            print(f"  Clusters found:      {stats['clusters_found']}")
            print(f"  Schemas created:     {stats['schemas_created']}")
            print(f"  Skipped (duplicate): {stats['skipped_duplicate']}")
            total = await pool.fetchval("SELECT COUNT(*) FROM schemas WHERE invalid_at IS NULL")
            print(f"\n  Total active schemas: {total}")
        else:
            print(f"Unknown step: {step}")
            sys.exit(1)
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
