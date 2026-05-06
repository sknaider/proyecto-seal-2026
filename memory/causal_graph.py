#!/usr/bin/env python3
"""
GAP-CR8 — Razonamiento Causal Formal (DAG + Counterfactual)
SEAL Memory System | ADA | 2026-04-26

Implementa causalidad formal sobre el Connectome Neo4j:
  - DAG [:CAUSES] sobre reasoning_traces (Peter-Clark simplificado)
  - Pearl do-calculus: P(Y|do(X)) via graph surgery
  - Counterfactual: Abduction → Action → Prediction
  - PostgreSQL causal_edges para sync y auditoría

Algoritmo base:
  Peter-Clark (PC) simplificado: para cada par de eventos co-ocurrentes,
  ordenar por timestamp (dirección causal) y validar con chi² de independencia
  condicional. confidence += 0.1 por cada repetición consistente, *= 0.8 si
  mismo cause → diferentes effects.

Ref: Pearl (2009) "Causality" — do-calculus 3 reglas.
     arxiv:2512.23343 (SEAL Brain spec).

Uso:
    python3 causal_graph.py --extract --agent ADA --dry-run    # extraer desde traces
    python3 causal_graph.py --query-intervene <target_id> <intervention_id>  # do-calculus
    python3 causal_graph.py --counterfactual <outcome_id> --hypothetical <event_id>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional

import asyncpg
import networkx as nx
from neo4j import AsyncGraphDatabase

sys.path.insert(0, str(Path(__file__).parent))

LIMA_TZ = ZoneInfo("America/Lima")
DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "seal2026soul")

LOG = logging.getLogger("gap-cr8-causal")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

# PC algorithm parameters
MIN_COOCCURRENCE = 3        # min times cause precedes effect to add edge
CONFIDENCE_INCREMENT = 0.1  # per consistent repetition
CONFIDENCE_DECAY = 0.8      # when same cause → different effects
CONFIDENCE_THRESHOLD = 0.4  # min confidence to persist edge
MAX_EDGES_PER_EXTRACT = 50  # safety cap per agent per run


# ── Schema Setup ──────────────────────────────────────────────────────────────

CAUSAL_EDGES_DDL = """
CREATE TABLE IF NOT EXISTS causal_edges (
    id SERIAL PRIMARY KEY,
    cause_event_id INT NOT NULL,
    effect_event_id INT NOT NULL,
    edge_type VARCHAR(20) NOT NULL DEFAULT 'CAUSES',
    confidence FLOAT NOT NULL DEFAULT 0.5,
    evidence_count INT NOT NULL DEFAULT 1,
    agent VARCHAR(20),
    method VARCHAR(30) DEFAULT 'observational',
    last_validated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    invalidated BOOL NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_causal_edges_cause ON causal_edges (cause_event_id, invalidated);
CREATE INDEX IF NOT EXISTS idx_causal_edges_effect ON causal_edges (effect_event_id, invalidated);
CREATE INDEX IF NOT EXISTS idx_causal_edges_validated ON causal_edges (last_validated, invalidated);
"""

NEO4J_CAUSES_CONSTRAINT = """
CREATE CONSTRAINT IF NOT EXISTS FOR (e:CausalEvent) REQUIRE e.event_id IS UNIQUE
"""


async def ensure_schema(pool: asyncpg.Pool, neo4j_driver) -> None:
    """Ensure causal_edges table and Neo4j constraint exist."""
    async with pool.acquire() as conn:
        await conn.execute(CAUSAL_EDGES_DDL)

    async with neo4j_driver.session() as session:
        await session.run(NEO4J_CAUSES_CONSTRAINT)
        # Create CausalEvent index on agent+timestamp for efficient PC queries
        await session.run("""
            CREATE INDEX IF NOT EXISTS FOR (e:CausalEvent) ON (e.agent, e.timestamp)
        """)

    LOG.info("causal_edges schema + Neo4j constraint OK")


# ── Peter-Clark Simplified: Extract from reasoning_traces ─────────────────────

async def extract_causal_edges(
    agent: str,
    pool: asyncpg.Pool,
    dry_run: bool = True,
) -> dict:
    """
    Peter-Clark simplificado: extrae edges causales desde reasoning_traces.

    Para cada reasoning_trace con outcome_success=True:
    - Parsea premises como causas candidatas
    - La conclusion como efecto
    - Si misma [cause_hash → effect_hash] aparece ≥ MIN_COOCCURRENCE → edge CAUSES
    - Si mismo cause → diferentes effects → confidence *= CONFIDENCE_DECAY
    """
    rows = await pool.fetch("""
        SELECT id, agent, task, premises, reasoning, conclusion,
               outcome, outcome_success, created_at
        FROM reasoning_traces
        WHERE agent = $1
          AND created_at > NOW() - INTERVAL '90 days'
        ORDER BY created_at ASC
        LIMIT 500
    """, agent)

    if not rows:
        return {"agent": agent, "edges_found": 0, "reason": "no_traces"}

    # Build co-occurrence map: cause_key → {effect_key: [count, last_success_rate]}
    cause_effect_counts: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {
        "consistent": 0, "inconsistent": 0, "cause_id": None, "effect_id": None,
        "cause_content": "", "effect_content": "",
    }))

    for row in rows:
        task = (row["task"] or "")[:60]
        premises_text = row["premises"] or ""
        conclusion_text = row["conclusion"] or ""

        # Normalize keys (first 80 chars as fingerprint)
        cause_key = premises_text[:80].strip().lower()
        effect_key = conclusion_text[:80].strip().lower()

        if not cause_key or not effect_key or cause_key == effect_key:
            continue

        cell = cause_effect_counts[cause_key][effect_key]
        if row["outcome_success"]:
            cell["consistent"] += 1
        else:
            cell["inconsistent"] += 1

        # Track IDs of representative traces (use latest)
        cell["cause_id"] = row["id"]
        cell["effect_id"] = row["id"]
        cell["cause_content"] = premises_text[:200]
        cell["effect_content"] = conclusion_text[:200]

    # Build candidate edges with confidence scores
    edges: list[dict] = []
    for cause_key, effects in cause_effect_counts.items():
        for effect_key, stats in effects.items():
            total = stats["consistent"] + stats["inconsistent"]
            if total < MIN_COOCCURRENCE:
                continue

            # Base confidence: fraction consistent
            base_conf = stats["consistent"] / total

            # Penalize if same cause → multiple different effects (confounding)
            n_effects = len(effects)
            if n_effects > 1:
                base_conf *= CONFIDENCE_DECAY ** (n_effects - 1)

            if base_conf < CONFIDENCE_THRESHOLD:
                continue

            edges.append({
                "cause_content": stats["cause_content"],
                "effect_content": stats["effect_content"],
                "confidence": round(base_conf, 3),
                "evidence_count": total,
                "method": "pc_simplified",
            })

            if len(edges) >= MAX_EDGES_PER_EXTRACT:
                break
        if len(edges) >= MAX_EDGES_PER_EXTRACT:
            break

    result = {
        "agent": agent,
        "traces_analyzed": len(rows),
        "edges_found": len(edges),
        "dry_run": dry_run,
        "edges": edges[:10],  # preview first 10
    }

    if not dry_run and edges:
        # Persist to causal_edges table using memory IDs as proxy event IDs
        # (reasoning_trace IDs mapped to effect_event_id)
        inserted = 0
        async with pool.acquire() as conn:
            for edge in edges:
                # Use hash of content as stable ID
                cause_hash = hash(edge["cause_content"]) % (2**31)
                effect_hash = hash(edge["effect_content"]) % (2**31)

                # Upsert: increment evidence_count if edge exists
                existing = await conn.fetchrow("""
                    SELECT id, confidence, evidence_count
                    FROM causal_edges
                    WHERE cause_event_id = $1 AND effect_event_id = $2
                      AND agent = $3 AND invalidated = FALSE
                """, cause_hash, effect_hash, agent)

                if existing:
                    new_conf = min(0.99, existing["confidence"] + CONFIDENCE_INCREMENT)
                    await conn.execute("""
                        UPDATE causal_edges
                        SET confidence = $1, evidence_count = evidence_count + $2,
                            last_validated = NOW()
                        WHERE id = $3
                    """, new_conf, edge["evidence_count"], existing["id"])
                else:
                    await conn.execute("""
                        INSERT INTO causal_edges
                        (cause_event_id, effect_event_id, edge_type, confidence,
                         evidence_count, agent, method, metadata)
                        VALUES ($1, $2, 'CAUSES', $3, $4, $5, $6, $7::jsonb)
                    """,
                        cause_hash, effect_hash,
                        edge["confidence"], edge["evidence_count"],
                        agent, edge["method"],
                        json.dumps({
                            "cause_snippet": edge["cause_content"][:100],
                            "effect_snippet": edge["effect_content"][:100],
                        }),
                    )
                    inserted += 1

        result["inserted"] = inserted
        LOG.info("[%s] Causal edges inserted: %d", agent, inserted)

    return result


# ── Neo4j Sync: PG causal_edges → Neo4j [:CAUSES] ─────────────────────────────

async def sync_causal_to_neo4j(agent: str, pool: asyncpg.Pool, neo4j_driver) -> dict:
    """
    Sync PostgreSQL causal_edges → Neo4j [:CAUSES] edges.
    Creates CausalEvent nodes and CAUSES relationships.
    """
    rows = await pool.fetch("""
        SELECT id, cause_event_id, effect_event_id, confidence,
               evidence_count, method, metadata
        FROM causal_edges
        WHERE agent = $1 AND invalidated = FALSE
        ORDER BY confidence DESC
        LIMIT 200
    """, agent)

    if not rows:
        return {"agent": agent, "synced": 0}

    synced = 0
    async with neo4j_driver.session() as session:
        for row in rows:
            raw_meta = row["metadata"] or {}
            meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
            cause_label = meta.get("cause_snippet", str(row["cause_event_id"]))[:80]
            effect_label = meta.get("effect_snippet", str(row["effect_event_id"]))[:80]

            await session.run("""
                MERGE (c:CausalEvent {event_id: $cause_id, agent: $agent})
                  ON CREATE SET c.label = $cause_label, c.created_at = datetime()
                MERGE (e:CausalEvent {event_id: $effect_id, agent: $agent})
                  ON CREATE SET e.label = $effect_label, e.created_at = datetime()
                MERGE (c)-[r:CAUSES {pg_edge_id: $pg_id}]->(e)
                  ON CREATE SET r.confidence = $confidence,
                                r.evidence_count = $evidence_count,
                                r.method = $method,
                                r.created_at = datetime()
                  ON MATCH SET r.confidence = $confidence,
                               r.evidence_count = $evidence_count,
                               r.last_updated = datetime()
            """,
                cause_id=row["cause_event_id"],
                effect_id=row["effect_event_id"],
                agent=agent,
                cause_label=cause_label,
                effect_label=effect_label,
                pg_id=row["id"],
                confidence=row["confidence"],
                evidence_count=row["evidence_count"],
                method=row["method"] or "observational",
            )
            synced += 1

    LOG.info("[%s] Synced %d causal edges to Neo4j", agent, synced)
    return {"agent": agent, "synced": synced}


# ── Pearl do-calculus: P(Y | do(X)) ───────────────────────────────────────────

async def query_intervene(
    target_event_id: int,
    intervened_event_id: int,
    agent: str,
    pool: asyncpg.Pool,
) -> dict:
    """
    Pearl do-calculus via graph surgery.

    P(target | do(intervened)) — remove all incoming edges to 'intervened',
    then compute probability of target being reachable from intervened.

    Returns:
        p_with_intervention: P(target | do(intervened=True))
        p_without: P(target | intervened=False) — baseline without intervention
        causal_effect: difference (positive = causal contribution)
        path: shortest causal path if found
    """
    rows = await pool.fetch("""
        SELECT cause_event_id, effect_event_id, confidence, evidence_count
        FROM causal_edges
        WHERE agent = $1 AND invalidated = FALSE
    """, agent)

    if not rows:
        return {
            "error": "no_causal_edges",
            "agent": agent,
            "target": target_event_id,
            "intervened": intervened_event_id,
        }

    # Build networkx DAG
    G = nx.DiGraph()
    for row in rows:
        G.add_edge(
            row["cause_event_id"],
            row["effect_event_id"],
            confidence=row["confidence"],
            evidence=row["evidence_count"],
        )

    # Check if target and intervened exist in graph
    if intervened_event_id not in G.nodes:
        return {"error": f"intervened_event {intervened_event_id} not in causal graph"}
    if target_event_id not in G.nodes:
        return {"error": f"target_event {target_event_id} not in causal graph"}

    # Graph surgery: remove incoming edges to intervened node
    G_do = G.copy()
    incoming = list(G_do.in_edges(intervened_event_id))
    G_do.remove_edges_from(incoming)

    # P(target reachable | do(intervened)) — path exists in G_do
    try:
        path_do = nx.shortest_path(G_do, intervened_event_id, target_event_id, weight=None)
        p_intervention = _path_confidence(G_do, path_do)
        reachable_do = True
    except nx.NetworkXNoPath:
        path_do = []
        p_intervention = 0.0
        reachable_do = False

    # Baseline: P(target reachable | observational)
    try:
        path_obs = nx.shortest_path(G, intervened_event_id, target_event_id, weight=None)
        p_baseline = _path_confidence(G, path_obs)
    except nx.NetworkXNoPath:
        path_obs = []
        p_baseline = 0.0

    # Detect if intervened is a confounder (has incoming edges in original)
    confounder_warning = len(incoming) > 0

    return {
        "agent": agent,
        "target_event_id": target_event_id,
        "intervened_event_id": intervened_event_id,
        "p_observational": round(p_baseline, 4),
        "p_do_intervention": round(p_intervention, 4),
        "causal_effect": round(p_intervention - p_baseline, 4),
        "reachable_after_surgery": reachable_do,
        "path_do": path_do,
        "path_observational": path_obs,
        "edges_cut_by_surgery": len(incoming),
        "confounder_warning": confounder_warning,
        "interpretation": _interpret_causal_effect(p_baseline, p_intervention),
    }


def _path_confidence(G: nx.DiGraph, path: list) -> float:
    """Product of edge confidences along a path."""
    if len(path) < 2:
        return 1.0
    conf = 1.0
    for i in range(len(path) - 1):
        edge_data = G.get_edge_data(path[i], path[i+1]) or {}
        conf *= edge_data.get("confidence", 0.5)
    return conf


def _interpret_causal_effect(p_obs: float, p_do: float) -> str:
    effect = p_do - p_obs
    if abs(effect) < 0.05:
        return "NO_CAUSAL_EFFECT — correlation only (p_do ≈ p_obs)"
    elif effect > 0.2:
        return f"STRONG_CAUSE — intervention strongly increases outcome by {effect:.2f}"
    elif effect > 0:
        return f"WEAK_CAUSE — small causal contribution ({effect:.2f})"
    else:
        return f"PREVENTS — intervention reduces outcome by {abs(effect):.2f}"


# ── Pearl 3-step Counterfactual ───────────────────────────────────────────────

async def counterfactual_query(
    actual_outcome_id: int,
    hypothetical_event_id: int,
    agent: str,
    pool: asyncpg.Pool,
    hypothetical_value: bool = False,
) -> dict:
    """
    Pearl 3-step counterfactual: 'Si X no hubiera ocurrido, ¿habría ocurrido Y?'

    Step 1 — Abduction: infer the state of exogenous variables (U) that
             explain the actual outcome (use existing causal graph as structural model).
    Step 2 — Action: apply the hypothetical intervention (set X = hypothetical_value).
    Step 3 — Prediction: compute predicted outcome under the modified model.

    Args:
        actual_outcome_id: The event that actually happened (Y_actual)
        hypothetical_event_id: The event to hypothetically alter (X)
        agent: Agent whose causal graph to use
        hypothetical_value: False = 'X didn't happen', True = 'X happened differently'
    """
    rows = await pool.fetch("""
        SELECT cause_event_id, effect_event_id, confidence, evidence_count
        FROM causal_edges
        WHERE agent = $1 AND invalidated = FALSE
    """, agent)

    if not rows:
        return {"error": "no_causal_edges", "agent": agent}

    G = nx.DiGraph()
    for row in rows:
        G.add_edge(
            row["cause_event_id"],
            row["effect_event_id"],
            confidence=row["confidence"],
        )

    # Step 1 — Abduction: identify causal ancestors of actual_outcome
    if actual_outcome_id not in G.nodes:
        return {"error": f"outcome {actual_outcome_id} not in causal graph"}

    ancestors = nx.ancestors(G, actual_outcome_id)
    is_ancestor = hypothetical_event_id in ancestors

    # Step 2 — Action: modify the structural model
    # If hypothetical_value=False (X didn't happen): remove X and its outgoing edges
    G_cf = G.copy()
    if not hypothetical_value:
        # Remove X entirely (it didn't happen)
        if hypothetical_event_id in G_cf:
            G_cf.remove_node(hypothetical_event_id)
    else:
        # Add X as a forced intervention node (already in graph — no change needed)
        pass

    # Step 3 — Prediction: is Y still reachable?
    if actual_outcome_id not in G_cf.nodes:
        # Outcome itself was removed (it was X)
        cf_outcome_reachable = False
        cf_path = []
        cf_confidence = 0.0
    else:
        # Find any path to outcome from remaining ancestors
        surviving_ancestors = nx.ancestors(G_cf, actual_outcome_id) if actual_outcome_id in G_cf.nodes else set()
        cf_outcome_reachable = len(surviving_ancestors) > 0 or actual_outcome_id in G_cf.nodes

        # Attempt path from a root cause
        cf_path = []
        cf_confidence = 0.0
        roots = [n for n in G_cf.nodes if G_cf.in_degree(n) == 0]
        for root in roots:
            try:
                p = nx.shortest_path(G_cf, root, actual_outcome_id)
                cf_confidence = max(cf_confidence, _path_confidence(G_cf, p))
                if not cf_path:
                    cf_path = p
            except nx.NetworkXNoPath:
                continue

    # Original path confidence
    orig_path = []
    orig_confidence = 0.0
    if actual_outcome_id in G.nodes:
        roots_orig = [n for n in G.nodes if G.in_degree(n) == 0]
        for root in roots_orig:
            try:
                p = nx.shortest_path(G, root, actual_outcome_id)
                c = _path_confidence(G, p)
                if c > orig_confidence:
                    orig_confidence = c
                    orig_path = p
            except nx.NetworkXNoPath:
                continue

    delta = cf_confidence - orig_confidence
    if hypothetical_value is False:
        if not cf_outcome_reachable or cf_confidence < 0.05:
            interpretation = f"X era CAUSA NECESARIA — sin X, Y no habría ocurrido (delta={delta:.2f})"
        elif abs(delta) < 0.1:
            interpretation = f"X NO era causal — Y habría ocurrido de todas formas (delta={delta:.2f})"
        else:
            interpretation = f"X contribuyó pero no era suficiente — Y menos probable sin X (delta={delta:.2f})"
    else:
        interpretation = f"Con X forzado, P(Y) = {cf_confidence:.3f} vs original {orig_confidence:.3f}"

    return {
        "agent": agent,
        "actual_outcome_id": actual_outcome_id,
        "hypothetical_event_id": hypothetical_event_id,
        "hypothetical_value": hypothetical_value,
        "step1_abduction": {
            "ancestors_of_outcome": len(ancestors),
            "hypothetical_is_ancestor": is_ancestor,
        },
        "step2_action": f"{'removed' if not hypothetical_value else 'forced'} event {hypothetical_event_id}",
        "step3_prediction": {
            "counterfactual_outcome_reachable": cf_outcome_reachable,
            "counterfactual_confidence": round(cf_confidence, 4),
            "original_confidence": round(orig_confidence, 4),
            "delta": round(delta, 4),
            "counterfactual_path": cf_path,
        },
        "interpretation": interpretation,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="GAP-CR8 Causal Graph")
    parser.add_argument("--extract", action="store_true", help="Extract causal edges from traces")
    parser.add_argument("--sync-neo4j", action="store_true", help="Sync PG edges to Neo4j")
    parser.add_argument("--query-intervene", nargs=2, type=int, metavar=("TARGET", "INTERVENED"),
                        help="do-calculus: P(TARGET | do(INTERVENED))")
    parser.add_argument("--counterfactual", type=int, metavar="OUTCOME",
                        help="Counterfactual: what if HYPOTHETICAL hadn't happened?")
    parser.add_argument("--hypothetical", type=int, metavar="EVENT",
                        help="Event to hypothetically alter")
    parser.add_argument("--agent", default="ADA", help="Agent (default ADA)")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Preview only (default True)")
    parser.add_argument("--execute", action="store_true", help="Actually write")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    dry_run = not args.execute
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=3)
    neo4j_driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)

    try:
        await ensure_schema(pool, neo4j_driver)

        result = {}

        if args.extract:
            result = await extract_causal_edges(args.agent, pool, dry_run=dry_run)

        elif args.sync_neo4j:
            result = await sync_causal_to_neo4j(args.agent, pool, neo4j_driver)

        elif args.query_intervene:
            target_id, intervened_id = args.query_intervene
            result = await query_intervene(target_id, intervened_id, args.agent, pool)

        elif args.counterfactual and args.hypothetical:
            result = await counterfactual_query(
                args.counterfactual, args.hypothetical, args.agent, pool
            )

        else:
            # Default: extract + report
            result = await extract_causal_edges(args.agent, pool, dry_run=dry_run)

        if args.json:
            print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        else:
            for k, v in result.items():
                print(f"  {k}: {v}")

    finally:
        await pool.close()
        await neo4j_driver.close()


if __name__ == "__main__":
    asyncio.run(main())
