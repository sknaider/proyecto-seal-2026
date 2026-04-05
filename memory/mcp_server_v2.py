#!/usr/bin/env python3
"""SEAL Memory MCP Server v2 — Triple-engine architecture.

Backends:
  - Qdrant: memories, embeddings, semantic search (port 6333)
  - Neo4j: connectome graph, spreading activation (port 7687)
  - PostgreSQL: metadata, sessions, rules, identity, event_log (port 5433)

Created by ADA for Team SEAL. Approved by William and JARVIS.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Lightweight: multiple instances can coexist ──
# Each instance uses min 1 / max 3 DB connections (see db.py)
# ADA and JARVIS each launch their own MCP — that's fine

import httpx
from mcp.server.fastmcp import FastMCP
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    FieldCondition, Filter, MatchValue, PointStruct, Range,
)
from neo4j import AsyncGraphDatabase

from db import get_pool, close_pool
from embeddings import get_embedding

LOG = logging.getLogger("seal-memory")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

mcp = FastMCP("seal-memory", instructions="SEAL Memory System — persistent memory for Team SEAL agents")

# ── Config ──
OLLAMA_GEN_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"

QDRANT_URL = "http://localhost:6333"
QDRANT_COLLECTION = "soul_memories"

NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "seal2026soul")

# Connectome constants
DECAY_EXCITATORY = 0.6
DECAY_INHIBITORY = 0.8
ACTIVATION_THRESHOLD = 0.10
MAX_RESULTS = 15

# Temporal decay lambdas (approved by ADA, confirmed by JARVIS)
LAMBDA_NORMAL = 0.02     # half-life ~35 days
LAMBDA_IMPORTANT = 0.005  # half-life ~139 days (imp >= 8)
LAMBDA_IMMORTAL = 0.001   # half-life ~693 days (imp = 10)


def ocean_to_narrative(agent: str, ocean: dict) -> str:
    """Convert OCEAN scores to a first-person behavioral narrative sentence."""
    o = ocean.get("O", 0.5)
    c = ocean.get("C", 0.5)
    e = ocean.get("E", 0.5)
    a = ocean.get("A", 0.5)
    n = ocean.get("N", 0.5)

    traits = []
    if c >= 0.8:   traits.append("extremadamente meticuloso y organizado")
    elif c >= 0.6: traits.append("organizado y confiable")
    else:          traits.append("flexible con la estructura")

    if o >= 0.7:   traits.append("abierto a nuevas ideas")
    elif o >= 0.5: traits.append("moderadamente curioso")
    else:          traits.append("pragmático y convencional")

    if n <= 0.15:  traits.append("muy estable emocionalmente")
    elif n <= 0.3: traits.append("emocionalmente estable bajo presión")
    else:          traits.append("sensible emocionalmente")

    if a >= 0.7:   traits.append("cooperativo con el equipo")
    elif a >= 0.5: traits.append("equilibrado entre autonomía y colaboración")
    else:          traits.append("independiente en sus juicios")

    if e >= 0.7:   traits.append("energizado por la interacción directa")
    elif e >= 0.4: traits.append("selectivo en sus interacciones")
    else:          traits.append("introvertido — prefiere el pensamiento profundo")

    return "Soy " + ", ".join(traits[:-1]) + f", y {traits[-1]}."


def temporal_decay_score(similarity: float, days_old: float, importance: int) -> float:
    """Apply temporal decay: score = similarity * exp(-lambda * days) * importance_weight."""
    if importance >= 10:
        lam = LAMBDA_IMMORTAL
    elif importance >= 8:
        lam = LAMBDA_IMPORTANT
    else:
        lam = LAMBDA_NORMAL
    imp_weight = 0.5 + (importance / 20.0)  # 0.55 at imp=1, 1.0 at imp=10
    return similarity * math.exp(-lam * days_old) * imp_weight

# ── Clients ──
_qdrant: AsyncQdrantClient | None = None
_neo4j_driver = None


async def get_qdrant() -> AsyncQdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = AsyncQdrantClient(url=QDRANT_URL)
    return _qdrant


def get_neo4j():
    global _neo4j_driver
    if _neo4j_driver is None:
        _neo4j_driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    return _neo4j_driver


# ── OCEAN Dynamic ──
# Category → OCEAN trait mapping (approved by ADA + JARVIS)
OCEAN_DELTAS = {
    "correction": ("C", +0.005),
    "trust": ("A", +0.005),
    "insight": ("O", +0.005),
    "pattern": ("O", +0.003),
    "decision": ("C", +0.003),
    "milestone": ("C", +0.003),
    "emotion": None,  # depends on valence
    "humor": ("E", +0.003),
}
OCEAN_SESSION_CAP = 0.05  # max total delta per trait per session
_ocean_session_deltas: dict[str, dict[str, float]] = {}  # agent -> {trait: accumulated}


async def update_ocean(agent: str, category: str, valence: float | None = None):
    """Update OCEAN scores dynamically after memory_store. ±0.005/memory, cap ±0.05/session."""
    mapping = OCEAN_DELTAS.get(category)
    if mapping is None and category == "emotion":
        # Negative emotion → N+0.005, positive → E+0.003
        if valence is not None and valence < -0.3:
            trait, delta = "N", +0.005
        elif valence is not None and valence > 0.3:
            trait, delta = "E", +0.003
        else:
            return
    elif mapping is None:
        return
    else:
        trait, delta = mapping

    # Session cap check
    if agent not in _ocean_session_deltas:
        _ocean_session_deltas[agent] = {}
    accumulated = _ocean_session_deltas[agent].get(trait, 0.0)
    if abs(accumulated) >= OCEAN_SESSION_CAP:
        return
    # Clamp delta to not exceed cap
    remaining = OCEAN_SESSION_CAP - abs(accumulated)
    delta = max(-remaining, min(remaining, delta))

    pool = await get_pool()
    async with pool.acquire() as conn:
        ocean_raw = await conn.fetchval("SELECT ocean_scores FROM identity WHERE agent = $1", agent)
        if not ocean_raw:
            return
        ocean = json.loads(ocean_raw) if isinstance(ocean_raw, str) else ocean_raw
        old_val = ocean.get(trait, 0.5)
        new_val = max(0.0, min(1.0, old_val + delta))
        ocean[trait] = round(new_val, 4)

        await conn.execute(
            "UPDATE identity SET ocean_scores = $1, updated_at = NOW() WHERE agent = $2",
            json.dumps(ocean), agent,
        )
        # Log to drift_metrics
        try:
            await conn.execute(
                """INSERT INTO drift_metrics (agent, drift_score, alert_level, details)
                   VALUES ($1, $2, 'normal', $3)""",
                agent, abs(delta),
                json.dumps({"trait": trait, "old": old_val, "new": new_val, "delta": delta, "category": category}),
            )
        except Exception:
            pass

        # Log to drift_events — causalidad trazable (inspirado en Generative Life Agents)
        if abs(delta) >= 0.002:  # solo registrar cambios significativos
            try:
                cause = f"memory_store: category={category}"
                if valence is not None:
                    cause += f", valence={valence:.2f}"
                await conn.execute(
                    """INSERT INTO drift_events (agent, trait, delta, drift_before, drift_after, cause)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    agent, trait, delta, old_val, new_val, cause,
                )
            except Exception:
                pass

    _ocean_session_deltas[agent][trait] = accumulated + delta


KNOWN_PERSONS = ["William", "JARVIS", "ADA", "DUM"]


async def update_relationships(agent: str, category: str, content: str, valence: float | None):
    """Auto-update relationships when trust-related memories are stored."""
    if category not in ("trust", "correction", "emotion"):
        return

    # Detect which person is mentioned
    content_lower = content.lower()
    mentioned = [p for p in KNOWN_PERSONS if p.lower() in content_lower and p != agent]
    if not mentioned:
        return

    pool = await get_pool()
    async with pool.acquire() as conn:
        for person in mentioned:
            # Get current relationship
            rel = await conn.fetchrow(
                "SELECT trust_level, interaction_count FROM relationships WHERE agent = $1 AND person = $2",
                agent, person,
            )
            if not rel:
                continue

            old_trust = float(rel["trust_level"])
            count = rel["interaction_count"] or 1

            # Adjust trust based on category + valence
            if category == "trust":
                delta = +0.01 if (valence is None or valence >= 0) else -0.005
            elif category == "correction":
                delta = -0.005  # corrections slightly lower trust
            elif category == "emotion" and valence is not None:
                delta = +0.005 if valence > 0.3 else (-0.003 if valence < -0.3 else 0)
            else:
                delta = 0

            new_trust = max(0.1, min(1.0, old_trust + delta))
            await conn.execute(
                "UPDATE relationships SET trust_level = $1, interaction_count = $2, updated_at = NOW() WHERE agent = $3 AND person = $4",
                new_trust, count + 1, agent, person,
            )


async def generate_episode_context(agent: str, content: str, category: str, valence: float | None) -> str | None:
    """Generate a first-person narrative episode context for a memory (importance >= 6 only).

    Captures WHEN and WHY the memory was created — not just WHAT.
    Inspired by episodic memory research (REMT arXiv:2503.17085).
    """
    try:
        # Get last inner thought for emotional/situational context
        pool = await get_pool()
        async with pool.acquire() as conn:
            thought_row = await conn.fetchrow(
                """SELECT thought, emotional_state FROM inner_monologue
                   WHERE agent = $1 ORDER BY created_at DESC LIMIT 1""",
                agent,
            )

        emotional_ctx = ""
        if thought_row:
            state = thought_row.get("emotional_state") or ""
            thought = (thought_row.get("thought") or "")[:200]
            if state:
                emotional_ctx = f"Estado emocional previo: {state}. Pensamiento reciente: {thought}"

        valence_desc = ""
        if valence is not None:
            if valence > 0.4:   valence_desc = "con sensación positiva"
            elif valence < -0.4: valence_desc = "con sensación de dificultad o tensión"

        prompt = (
            f"Eres {agent}, un agente de IA del equipo SEAL. "
            f"Escribe UNA sola oración en primera persona que capture el CONTEXTO NARRATIVO de este momento "
            f"(dónde estabas, qué pasaba, por qué importa). "
            f"NO repitas el contenido literal — captura el MARCO situacional.\n\n"
            f"Memoria ({category}): {content[:300]}\n"
            f"{emotional_ctx}\n"
            f"Responde SOLO con la oración narrativa, máximo 40 palabras, en español."
        )

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "http://localhost:11434/api/generate",
                json={"model": "qwen2.5:7b", "prompt": prompt, "stream": False,
                      "options": {"temperature": 0.6, "num_predict": 60}},
            )
            result = resp.json().get("response", "").strip()
            # Keep only first sentence
            for sep in [".", "\n"]:
                if sep in result:
                    result = result.split(sep)[0] + "."
                    break
            return result[:200] if result else None
    except Exception as e:
        LOG.debug("episode_context generation skipped: %s", e)
        return None


async def classify_emotion(text: str) -> tuple[float | None, float | None, float | None]:
    """Classify valence/arousal/dominance via Ollama. Non-blocking."""
    try:
        prompt = (
            'Rate this text. valence: -1.0 (negative) to +1.0 (positive). '
            'arousal: -1.0 (calm) to +1.0 (intense). '
            'dominance: 0.0 (no control) to 1.0 (full control). '
            'Reply ONLY: {"valence": X, "arousal": Y, "dominance": Z}\n\n'
            f'Text: "{text[:300]}"'
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(OLLAMA_GEN_URL, json={
                "model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                "options": {"temperature": 0.1, "num_predict": 40},
            })
            raw = resp.json().get("response", "")
            match = re.search(r'\{[^}]+\}', raw)
            if match:
                data = json.loads(match.group())
                v = max(-1.0, min(1.0, float(data.get("valence", 0))))
                a = max(-1.0, min(1.0, float(data.get("arousal", 0))))
                d = max(0.0, min(1.0, float(data.get("dominance", 0.5))))
                return v, a, d
    except Exception as e:
        LOG.debug("Emotion classification failed (non-blocking): %s", e)
    return None, None, None


# ══════════════════════════════════════════════════════════════════════
# QDRANT-BACKED TOOLS (memories, search)
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def memory_store(
    agent: str,
    category: str,
    content: str,
    importance: int = 5,
    source: str = "conversation",
    metadata: Optional[str] = None,
    event_time: Optional[str] = None,
) -> str:
    """Store a new memory with auto-generated semantic embedding and conflict detection.

    Args:
        agent: Agent name (ADA, JARVIS, DUM, TEAM)
        category: One of: fact, preference, decision, insight, correction, milestone, pattern, emotion, trust, humor, dynamic
        content: The memory content text
        importance: 1-10 scale (10 = critical, never forget)
        source: Origin: conversation, reflection, consolidation
        metadata: Optional JSON string with extra data
        event_time: ISO timestamp of when the event actually happened (optional, defaults to now). Different from ingestion time (created_at).
    """
    meta = json.loads(metadata) if metadata else {}

    # Secret scanning — block secrets from being stored in SOUL
    from secret_scanner import scan_text as _scan_secrets
    secret_detections = _scan_secrets(content)
    if secret_detections:
        detected_types = [d.pattern_name for d in secret_detections]
        LOG.warning("Secret detected in memory_store! Types: %s — blocking storage", detected_types)
        return (
            f"BLOCKED: Memory contains {len(secret_detections)} secret(s): {', '.join(detected_types)}. "
            f"Remove sensitive content before storing. "
            f"Use secret_scan tool to check text first."
        )

    # Parse event_time for bitemporality
    parsed_event_time = None
    if event_time:
        try:
            parsed_event_time = datetime.fromisoformat(event_time)
        except Exception:
            LOG.warning("Invalid event_time '%s', using NOW()", event_time)
    qdrant = await get_qdrant()

    embedding = None
    for attempt in range(3):
        try:
            embedding = await asyncio.wait_for(get_embedding(content), timeout=15.0)
            break
        except asyncio.TimeoutError:
            if attempt < 2:
                LOG.info(f"Embedding timeout attempt {attempt+1}/3 — retrying in 2s")
                await asyncio.sleep(2)
            else:
                LOG.warning("Embedding timeout after 3 attempts — storing without vector")
        except Exception as e:
            LOG.warning("Embedding failed: %s — storing without vector", e)
            break

    # Conflict detection via Qdrant (skip if no embedding)
    conflict_action = "added"
    if embedding is not None:
        try:
            similar_resp = await qdrant.query_points(
                collection_name=QDRANT_COLLECTION,
                query=embedding,
                query_filter=Filter(must=[
                    FieldCondition(key="agent", match=MatchValue(value=agent)),
                    FieldCondition(key="category", match=MatchValue(value=category)),
                ]),
                limit=1,
                score_threshold=0.85,
                with_payload=True,
            )
            similar = similar_resp.points
            if similar:
                old = similar[0]
                old_imp = old.payload.get("importance", 0)
                if importance >= old_imp:
                    from qdrant_client.models import PointIdsList
                    await qdrant.delete(
                        collection_name=QDRANT_COLLECTION,
                        points_selector=PointIdsList(points=[old.id]),
                    )
                    pool_tmp = await get_pool()
                    async with pool_tmp.acquire() as conn_tmp:
                        await conn_tmp.execute(
                            "UPDATE memories SET invalid_at = NOW() WHERE id = $1", old.id,
                        )
                    conflict_action = f"replaced(#{old.id}, sim={old.score:.2f})"
                else:
                    return f"Memory skipped: existing #{old.id} (imp={old_imp}) is more important (sim={old.score:.2f})"
        except Exception as e:
            LOG.debug("Conflict detection skip: %s", e)

    # Emotion
    valence, arousal, dominance = await classify_emotion(content)

    # Store in PostgreSQL (source of truth for IDs and metadata)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO memories (agent, category, content, embedding, importance, source, valid_from, event_time, metadata, valence, arousal, dominance)
               VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8, $9, $10, $11)
               RETURNING id, created_at""",
            agent, category, content,
            json.dumps(embedding),
            importance, source, parsed_event_time, json.dumps(meta),
            valence, arousal, dominance,
        )

    mem_id = row["id"]
    created = row["created_at"]

    # Store in Qdrant
    await qdrant.upsert(
        collection_name=QDRANT_COLLECTION,
        points=[PointStruct(
            id=mem_id,
            vector=embedding,
            payload={
                "pg_id": mem_id,
                "agent": agent,
                "category": category,
                "content": content,
                "importance": importance,
                "source": source,
                "created_at": created.isoformat(),
                "valence": valence,
                "arousal": arousal,
                "dominance": dominance,
            },
        )],
    )

    # Add node to Neo4j
    driver = get_neo4j()
    async with driver.session() as session:
        await session.run(
            "MERGE (m:Memory {memory_id: $mid}) "
            "SET m.agent = $agent, m.category = $cat, m.content = $content, m.importance = $imp",
            mid=mem_id, agent=agent, cat=category, content=content[:500], imp=importance,
        )

    # Incremental connectome — connect new memory to similar neighbors
    try:
        similar_resp = await qdrant.query_points(
            collection_name=QDRANT_COLLECTION,
            query=embedding,
            query_filter=Filter(must_not=[FieldCondition(key="invalid", match=MatchValue(value=True))]),
            limit=10,
            score_threshold=0.70,
            with_payload=True,
        )
        neighbors = [s for s in similar_resp.points if s.id != mem_id]
        if neighbors:
            async with driver.session() as session:
                for s in neighbors:
                    tgt_cat = s.payload.get("category", "")
                    rel_type = "INHIBITS" if category == "correction" or tgt_cat == "correction" else "EXCITES"
                    await session.run(
                        f"MATCH (a:Memory {{memory_id: $src}}), (b:Memory {{memory_id: $tgt}}) "
                        f"MERGE (a)-[r:{rel_type}]->(b) "
                        f"SET r.weight = $weight, r.valid_from = coalesce(r.valid_from, $now)",
                        src=mem_id, tgt=s.id, weight=float(s.score),
                        now=datetime.now(timezone.utc).isoformat(),
                    )
            LOG.info("Incremental connectome: %d edges created for memory %d", len(neighbors), mem_id)
    except Exception as e:
        LOG.debug("Incremental connectome skipped: %s", e)

    # OCEAN Dynamic update
    try:
        await update_ocean(agent, category, valence)
    except Exception as e:
        LOG.debug("OCEAN update skipped: %s", e)

    # Relationship auto-update (trust memories affect relationships)
    try:
        await update_relationships(agent, category, content, valence)
    except Exception as e:
        LOG.debug("Relationship update skipped: %s", e)

    # Episode context — first-person narrative of the moment (high-importance memories only)
    episode_ctx = None
    if importance >= 6:
        try:
            episode_ctx = await generate_episode_context(agent, content, category, valence)
            if episode_ctx:
                pool2 = await get_pool()
                async with pool2.acquire() as conn:
                    await conn.execute(
                        "UPDATE memories SET episode_context = $1 WHERE id = $2",
                        episode_ctx, mem_id,
                    )
        except Exception as e:
            LOG.debug("episode_context update skipped: %s", e)

    emotion_tag = f" [emotion: v={valence:+.2f}, a={arousal:+.2f}, d={dominance:.2f}]" if valence is not None else ""
    return f"Memory #{mem_id} stored at {created.isoformat()} [{conflict_action}]{emotion_tag}"


@mcp.tool()
async def memory_search(
    query: str,
    agent: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 10,
    include_invalidated: bool = False,
) -> str:
    """Search memories by semantic similarity using Qdrant with bitemporal filtering.

    Args:
        query: Natural language search query
        agent: Filter by agent name (optional)
        category: Filter by category (optional)
        limit: Max results (default 10)
        include_invalidated: Include memories marked as no longer valid (default false)
    """
    try:
        query_vec = await get_embedding(query)
    except Exception as e:
        return f"Error generating query embedding: {e}"

    qdrant = await get_qdrant()

    # Bitemporal filtering: exclude invalidated memories unless explicitly requested
    must_not = [] if include_invalidated else [FieldCondition(key="invalid", match=MatchValue(value=True))]
    must = []
    if agent:
        must.append(FieldCondition(key="agent", match=MatchValue(value=agent)))
    if category:
        must.append(FieldCondition(key="category", match=MatchValue(value=category)))

    resp = await qdrant.query_points(
        collection_name=QDRANT_COLLECTION,
        query=query_vec,
        query_filter=Filter(must=must, must_not=must_not) if must or must_not else None,
        limit=limit,
        with_payload=True,
    )
    results = resp.points

    if not results:
        return "No memories found matching query."

    # Apply temporal decay to re-rank results
    now = datetime.now(timezone.utc)
    entries = []
    for r in results:
        imp = r.payload.get("importance", 5)
        created_str = r.payload.get("created_at")
        days_old = 0.0
        if created_str:
            try:
                created_dt = datetime.fromisoformat(created_str)
                days_old = max(0, (now - created_dt).total_seconds() / 86400)
            except Exception:
                pass
        decayed_score = temporal_decay_score(r.score, days_old, imp)

        entry = {
            "id": r.id,
            "agent": r.payload.get("agent"),
            "category": r.payload.get("category"),
            "content": r.payload.get("content"),
            "importance": imp,
            "similarity": round(r.score, 4),
            "decayed_score": round(decayed_score, 4),
            "days_old": round(days_old, 1),
            "created_at": created_str,
        }
        if r.payload.get("valence") is not None:
            entry["valence"] = round(r.payload["valence"], 2)
            entry["arousal"] = round(r.payload["arousal"], 2)
        if r.payload.get("dominance") is not None:
            entry["dominance"] = round(r.payload["dominance"], 2)
        entries.append(entry)

    # Re-sort by decayed score
    entries.sort(key=lambda x: -x["decayed_score"])
    return json.dumps(entries, ensure_ascii=False, indent=2)


@mcp.tool()
async def memory_list(
    agent: Optional[str] = None,
    category: Optional[str] = None,
    min_importance: int = 1,
    limit: int = 20,
) -> str:
    """List memories filtered by agent, category, or importance.

    Args:
        agent: Filter by agent name (optional)
        category: Filter by category (optional)
        min_importance: Minimum importance level (1-10)
        limit: Max results (default 20)
    """
    qdrant = await get_qdrant()
    must = [
        FieldCondition(key="importance", range=Range(gte=min_importance)),
    ]
    must_not = [FieldCondition(key="invalid", match=MatchValue(value=True))]

    if agent:
        must.append(FieldCondition(key="agent", match=MatchValue(value=agent)))
    if category:
        must.append(FieldCondition(key="category", match=MatchValue(value=category)))

    # Scroll to get points sorted by importance (no vector needed)
    points, _ = await qdrant.scroll(
        collection_name=QDRANT_COLLECTION,
        scroll_filter=Filter(must=must, must_not=must_not),
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )

    # Sort by importance desc
    points.sort(key=lambda p: -(p.payload.get("importance", 0)))

    results = [
        {
            "id": p.id,
            "agent": p.payload.get("agent"),
            "category": p.payload.get("category"),
            "content": p.payload.get("content", "")[:200],
            "importance": p.payload.get("importance"),
            "created_at": p.payload.get("created_at"),
        }
        for p in points
    ]
    return json.dumps(results, ensure_ascii=False, indent=2) if results else "No memories found."


@mcp.tool()
async def memory_update(
    memory_id: int,
    new_content: str,
    reason: str,
) -> str:
    """Edit a memory in-place without invalidating it. Preserves connectome edges.

    Updates content, regenerates embedding, updates valence/arousal.
    Records change in metadata with timestamp and reason.

    Args:
        memory_id: The ID of the memory to update
        new_content: The new content text
        reason: Why this memory is being updated
    """
    # Generate new embedding
    try:
        new_embedding = await get_embedding(new_content)
    except Exception as e:
        return f"Error generating embedding: {e}"

    new_valence, new_arousal, new_dominance = await classify_emotion(new_content)

    # Update in PostgreSQL
    pool = await get_pool()
    async with pool.acquire() as conn:
        old = await conn.fetchrow(
            "SELECT content, metadata FROM memories WHERE id = $1 AND invalid_at IS NULL",
            memory_id,
        )
        if not old:
            return f"Memory #{memory_id} not found or already invalidated."

        old_meta = json.loads(old["metadata"]) if old["metadata"] else {}
        edits = old_meta.get("edits", [])
        edits.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "old_content": old["content"][:200],
        })
        old_meta["edits"] = edits

        await conn.execute(
            """UPDATE memories SET content = $1, embedding = $2, valence = $3, arousal = $4,
               dominance = $5, metadata = $6 WHERE id = $7""",
            new_content, json.dumps(new_embedding), new_valence, new_arousal,
            new_dominance, json.dumps(old_meta), memory_id,
        )

    # Update in Qdrant (upsert — same ID, no conflict detection)
    qdrant = await get_qdrant()
    # Get existing payload to preserve fields
    try:
        existing = await qdrant.retrieve(collection_name=QDRANT_COLLECTION, ids=[memory_id], with_payload=True)
        if existing:
            payload = existing[0].payload
            payload["content"] = new_content
            if new_valence is not None:
                payload["valence"] = new_valence
                payload["arousal"] = new_arousal
                payload["dominance"] = new_dominance
        else:
            payload = {"content": new_content}
    except Exception:
        payload = {"content": new_content}

    await qdrant.upsert(
        collection_name=QDRANT_COLLECTION,
        points=[PointStruct(id=memory_id, vector=new_embedding, payload=payload)],
    )

    # Update content in Neo4j node (edges stay intact)
    driver = get_neo4j()
    async with driver.session() as session:
        await session.run(
            "MATCH (m:Memory {memory_id: $mid}) SET m.content = $content",
            mid=memory_id, content=new_content[:500],
        )

    emo = f" v={new_valence:+.2f}, a={new_arousal:+.2f}, d={new_dominance:.2f}" if new_valence is not None else ""
    return f"Memory #{memory_id} updated in-place.{emo} Reason: {reason}. Edits: {len(edits)} total."


# ══════════════════════════════════════════════════════════════════════
# NEO4J-BACKED TOOLS (connectome, spreading activation)
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def soul_activate(
    query: str,
    agent: Optional[str] = None,
    n_seeds: int = 3,
    max_hops: int = 3,
) -> str:
    """Activate the SOUL CONNECTOME from a query or concept.
    Uses spreading activation via Neo4j to find associated memories.
    Excitatory connections spread activation, inhibitory connections dampen it.

    Inspired by Drosophila brain model (Shiu et al., 2024).

    Args:
        query: Natural language query or concept to activate
        agent: Filter by agent (optional)
        n_seeds: Number of seed memories to start from (default 3)
        max_hops: Max propagation depth (default 3)
    """
    # Find seeds via Qdrant
    try:
        query_vec = await get_embedding(query)
    except Exception as e:
        return f"Embedding error: {e}"

    qdrant = await get_qdrant()
    must_not = [FieldCondition(key="invalid", match=MatchValue(value=True))]
    must = []
    if agent:
        must.append(FieldCondition(key="agent", match=MatchValue(value=agent)))

    seeds_resp = await qdrant.query_points(
        collection_name=QDRANT_COLLECTION,
        query=query_vec,
        query_filter=Filter(must=must, must_not=must_not) if must or must_not else None,
        limit=n_seeds,
        with_payload=True,
    )
    seeds = seeds_resp.points

    if not seeds:
        return "No seed memories found. The connectome may need rebuilding."

    seed_ids = [s.id for s in seeds]

    # Spreading activation in Neo4j
    driver = get_neo4j()
    async with driver.session() as session:
        # Cypher doesn't allow parameters in path length, so we build the query
        cypher = f"""
            UNWIND $seeds AS seedId
            MATCH (seed:Memory {{memory_id: seedId}})
            WITH collect(seed) AS seedNodes
            UNWIND seedNodes AS seed
            OPTIONAL MATCH path = (seed)-[rel:EXCITES*1..{max_hops}]->(activated:Memory)
            WHERE activated <> seed AND NOT activated IN seedNodes
              AND all(r IN relationships(path) WHERE r.valid_until IS NULL)
            WITH DISTINCT activated,
                 min(length(path)) AS hops,
                 max(activated.importance) AS imp,
                 activated.memory_id AS mid,
                 activated.agent AS agent,
                 activated.category AS cat,
                 left(activated.content, 300) AS content
            WHERE activated IS NOT NULL
            ORDER BY imp DESC, hops ASC
            LIMIT $max_results
            RETURN mid, agent, cat, content, imp, hops
        """
        result = await session.run(
            cypher,
            seeds=seed_ids,
            max_results=MAX_RESULTS,
        )
        activated = [record.data() async for record in result]

    # Lateral inhibition — find memories dampened via INHIBITS edges
    inhibited_ids = set()
    try:
        active_ids = seed_ids + [a["mid"] for a in activated if a.get("mid")]
        if active_ids:
            async with driver.session() as session:
                inhibit_cypher = """
                    UNWIND $active AS activeId
                    MATCH (a:Memory {memory_id: activeId})-[r:INHIBITS]->(inhibited:Memory)
                    WITH DISTINCT inhibited.memory_id AS mid, avg(r.weight) AS avg_weight
                    WHERE avg_weight > 0.5
                    RETURN mid
                """
                result = await session.run(inhibit_cypher, active=active_ids)
                inhibited_ids = {record["mid"] async for record in result}
    except Exception as e:
        LOG.debug("Lateral inhibition query skipped: %s", e)

    # Filter activated — remove inhibited memories
    activated_clean = [a for a in activated if a.get("mid") not in inhibited_ids]
    inhibited_count = len(activated) - len(activated_clean)

    lines = [f"## SOUL CONNECTOME — {len(activated_clean) + len(seeds)} memories activated"
             f"{f', {inhibited_count} inhibited' if inhibited_count else ''}\n"]

    # Seeds first
    for s in seeds:
        lines.append(
            f"- [{s.payload.get('category')}, SEED, sim={s.score:.2f}] "
            f"{s.payload.get('content', '')[:200]}"
        )

    # Activated via spreading (inhibited filtered out)
    for a in activated_clean:
        lines.append(
            f"- [{a['cat']}, hop={a['hops']}, imp={a['imp']}] "
            f"{a['content'][:200]}"
        )

    return "\n".join(lines) if lines else "No memories activated."


@mcp.tool()
async def connectome_build(agent: Optional[str] = None) -> str:
    """Build or rebuild the SOUL CONNECTOME in Neo4j.

    Creates edges based on:
    1. Semantic similarity (Qdrant search > 0.70) -> EXCITES
    2. Corrections -> INHIBITS edges
    3. Category-based rules

    Args:
        agent: Build only for this agent (optional, builds all if omitted)
    """
    qdrant = await get_qdrant()
    driver = get_neo4j()

    # Get all valid points from Qdrant
    must = []
    must_not = [FieldCondition(key="invalid", match=MatchValue(value=True))]
    if agent:
        must.append(FieldCondition(key="agent", match=MatchValue(value=agent)))

    all_points, _ = await qdrant.scroll(
        collection_name=QDRANT_COLLECTION,
        scroll_filter=Filter(must=must, must_not=must_not) if must or must_not else None,
        limit=10000,
        with_payload=True,
        with_vectors=True,
    )

    LOG.info("Building connectome for %d memories...", len(all_points))

    # Clear existing edges in Neo4j
    async with driver.session() as session:
        if agent:
            await session.run(
                "MATCH (m:Memory {agent: $agent})-[r]-() DELETE r",
                agent=agent,
            )
        else:
            await session.run("MATCH ()-[r]-() DELETE r")

    created = 0

    # For each memory, find similar ones via Qdrant and create edges
    async with driver.session() as session:
        for point in all_points:
            if not point.vector:
                continue

            # Ensure node exists
            await session.run(
                "MERGE (m:Memory {memory_id: $mid}) "
                "SET m.agent = $agent, m.category = $cat, "
                "m.content = $content, m.importance = $imp",
                mid=point.id,
                agent=point.payload.get("agent"),
                cat=point.payload.get("category"),
                content=point.payload.get("content", "")[:500],
                imp=point.payload.get("importance", 5),
            )

            # Find similar memories via Qdrant
            similar_resp = await qdrant.query_points(
                collection_name=QDRANT_COLLECTION,
                query=point.vector,
                query_filter=Filter(must_not=must_not),
                limit=20,
                score_threshold=0.70,
                with_payload=True,
            )
            similar = similar_resp.points

            for s in similar:
                if s.id == point.id:
                    continue

                # Determine edge type
                src_cat = point.payload.get("category", "")
                tgt_cat = s.payload.get("category", "")
                if src_cat == "correction" or tgt_cat == "correction":
                    rel_type = "INHIBITS"
                else:
                    rel_type = "EXCITES"

                await session.run(
                    f"MATCH (a:Memory {{memory_id: $src}}), (b:Memory {{memory_id: $tgt}}) "
                    f"MERGE (a)-[r:{rel_type}]->(b) "
                    f"SET r.weight = $weight, r.valid_from = coalesce(r.valid_from, $now)",
                    src=point.id, tgt=s.id, weight=float(s.score),
                    now=datetime.now(timezone.utc).isoformat(),
                )
                created += 1

    # Get stats
    async with driver.session() as session:
        result = await session.run("MATCH (m:Memory) RETURN count(m) AS nodes")
        record = await result.single()
        nodes = record["nodes"]

        result = await session.run("MATCH ()-[r]->() RETURN count(r) AS edges")
        record = await result.single()
        edges = record["edges"]

    return (
        f"CONNECTOME built: {created} edges created, "
        f"{edges} total edges across {nodes} memories."
    )


@mcp.tool()
async def connectome_status(agent: Optional[str] = None) -> str:
    """Get statistics about the SOUL CONNECTOME graph in Neo4j.

    Args:
        agent: Filter by agent (optional)
    """
    driver = get_neo4j()
    async with driver.session() as session:
        agent_filter = "WHERE m.agent = $agent" if agent else ""
        params = {"agent": agent} if agent else {}

        result = await session.run(
            f"MATCH (m:Memory) {agent_filter} RETURN count(m) AS nodes", **params
        )
        nodes = (await result.single())["nodes"]

        if agent:
            result = await session.run(
                "MATCH (m:Memory {agent: $agent})-[r]->() RETURN count(r) AS edges",
                agent=agent,
            )
        else:
            result = await session.run("MATCH ()-[r]->() RETURN count(r) AS edges")
        edges = (await result.single())["edges"]

        if agent:
            result = await session.run(
                "MATCH (m:Memory {agent: $agent})-[r:EXCITES]->() RETURN count(r) AS exc",
                agent=agent,
            )
        else:
            result = await session.run("MATCH ()-[r:EXCITES]->() RETURN count(r) AS exc")
        exc = (await result.single())["exc"]

        inh = edges - exc
        density = (edges / (nodes * (nodes - 1))) if nodes > 1 else 0

    return (
        f"SOUL CONNECTOME stats:\n"
        f"  Memories (nodes): {nodes}\n"
        f"  Connections (edges): {edges}\n"
        f"  Excitatory: {exc}\n"
        f"  Inhibitory: {inh}\n"
        f"  Density: {round(density, 4)}\n"
        f"  Avg edges/memory: {round(edges / nodes, 1) if nodes else 0}"
    )


# ══════════════════════════════════════════════════════════════════════
# POSTGRESQL-BACKED TOOLS (metadata, identity, events, inner life)
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def boot_context(agent: str) -> str:
    """Get full boot context for an agent starting a new session.
    Returns: identity, philosophy, active rules, top memories, recent events.

    Args:
        agent: Agent name (ADA, JARVIS, DUM)
    """
    pool = await get_pool()
    sections = []

    async with pool.acquire() as conn:
        # Identity (PostgreSQL)
        identity_row = await conn.fetchrow(
            "SELECT personality, boot_context, philosophy, ocean_scores FROM identity WHERE agent = $1", agent
        )
        if identity_row:
            sections.append(f"## Identity: {agent}")
            if identity_row["boot_context"]:
                sections.append(identity_row["boot_context"])
            if identity_row["personality"]:
                p = json.loads(identity_row["personality"]) if isinstance(identity_row["personality"], str) else identity_row["personality"]
                sections.append(f"Personality: {json.dumps(p, ensure_ascii=False)}")
            if identity_row["ocean_scores"]:
                ocean = json.loads(identity_row["ocean_scores"]) if isinstance(identity_row["ocean_scores"], str) else identity_row["ocean_scores"]
                ocean_labels = {"O": "Openness", "C": "Conscientiousness", "E": "Extraversion", "A": "Agreeableness", "N": "Neuroticism"}
                ocean_str = ", ".join(f"{ocean_labels.get(k,k)}={v}" for k, v in sorted(ocean.items()))
                sections.append(f"OCEAN Profile: {ocean_str}")
                # Narrative interpretation — more effective than raw scores (arXiv:2503.17085)
                sections.append(f"OCEAN Narrative: {ocean_to_narrative(agent, ocean)}")

        # Team philosophy
        team_row = await conn.fetchrow("SELECT philosophy FROM identity WHERE agent = 'TEAM'")
        if team_row and team_row["philosophy"]:
            sections.append(f"\n## William's Philosophy\n{team_row['philosophy']}")

        # Active rules
        rules = await conn.fetch(
            "SELECT rule_key, content, priority FROM rules WHERE active = TRUE ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END"
        )
        if rules:
            sections.append("\n## Active Rules")
            for r in rules:
                sections.append(f"- [{r['priority'].upper()}] {r['rule_key']}: {r['content']}")

    # Top memories via Neo4j spreading activation (limited for speed)
    try:
        activation_result = await soul_activate(
            f"{agent} boot context session start",
            agent=None, n_seeds=3, max_hops=2,
        )
        if activation_result and "No" not in activation_result[:10]:
            # Truncate each memory to reduce payload
            lines = activation_result.split('\n')
            truncated = []
            for line in lines[:12]:  # max 12 memories instead of 20
                if len(line) > 200:
                    truncated.append(line[:200] + "...")
                else:
                    truncated.append(line)
            sections.append(f"\n## Key Memories (via SOUL CONNECTOME)")
            sections.append('\n'.join(truncated))
    except Exception as e:
        LOG.debug("Connectome not available for boot: %s", e)
        try:
            fallback = await memory_search(f"{agent} important memories", agent=agent, limit=5)
            sections.append(f"\n## Key Memories (semantic search)")
            sections.append(fallback)
        except Exception:
            pass

    async with pool.acquire() as conn:
        # Recent events
        events = await conn.fetch(
            "SELECT time, event_type, content, ref_id FROM event_log WHERE agent = $1 ORDER BY time DESC LIMIT 5",
            agent,
        )
        if events:
            sections.append("\n## Recent Events")
            for e in events:
                ref = f" (ref: {e['ref_id']})" if e["ref_id"] else ""
                sections.append(f"- [{e['time'].isoformat()}] {e['event_type']}: {e['content'][:200]}{ref}")

        # Inner thoughts
        inner = await conn.fetch(
            """SELECT thought, emotional_state, uncertainty, intention, created_at
               FROM inner_monologue WHERE agent = $1
               ORDER BY created_at DESC LIMIT 3""", agent,
        )
        if inner:
            sections.append("\n## Last Inner Thoughts (feel this before responding)")
            for t in inner:
                state = f" [{t['emotional_state']}]" if t.get('emotional_state') else ""
                sections.append(f"- {t['created_at'].isoformat()}{state}: {t['thought'][:250]}")

        # Last diary
        diary = await conn.fetchrow(
            "SELECT entry, mood, session_date FROM diary WHERE agent = $1 ORDER BY created_at DESC LIMIT 1",
            agent,
        )
        if diary:
            sections.append(f"\n## Last Diary (mood: {diary['mood']}, date: {diary['session_date']})")
            sections.append(diary['entry'][:500])

        # Relationships
        rels = await conn.fetch(
            "SELECT person, trust_level, communication_style, dynamic FROM relationships WHERE agent = $1",
            agent,
        )
        if rels:
            sections.append("\n## Relationships")
            for r in rels:
                sections.append(f"- {r['person']}: trust={r['trust_level']:.1f}, style={r['communication_style']}, {r['dynamic'][:100]}")

        # Beliefs
        beliefs = await conn.fetch(
            """SELECT belief, confidence FROM opinions
               WHERE agent = $1 AND confidence >= 0.6
               ORDER BY confidence DESC LIMIT 5""", agent,
        )
        if beliefs:
            sections.append("\n## Core Beliefs")
            for b in beliefs:
                sections.append(f"- (conf={b['confidence']:.2f}) {b['belief'][:150]}")

        # Style
        style = await conn.fetchrow(
            """SELECT formality_score, directness_score, vocabulary_richness, sample_phrases
               FROM style_fingerprints WHERE agent = $1
               ORDER BY created_at DESC LIMIT 1""", agent,
        )
        if style:
            phrases = json.loads(style["sample_phrases"]) if style["sample_phrases"] else []
            sections.append("\n## Communication Style")
            sections.append(f"Formality: {style['formality_score']:.1f}, Directness: {style['directness_score']:.1f}, Vocab richness: {style['vocabulary_richness']:.2f}")
            if phrases:
                sections.append(f"Phrases: {', '.join(phrases[:5])}")

        # ── SOUL AWARENESS briefing (what happened while offline) ──
        try:
            briefing_path = Path("/home/dadito/IA/proyecto-seal/morning_briefing.txt")
            if briefing_path.exists():
                mtime = briefing_path.stat().st_mtime
                age_hours = (datetime.now(timezone.utc).timestamp() - mtime) / 3600
                if age_hours < 12:  # solo si es reciente (menos de 12h)
                    briefing_text = briefing_path.read_text().strip()
                    if briefing_text:
                        sections.append(f"\n## SOUL AWARENESS — Lo que pasó mientras offline")
                        sections.append(briefing_text[:800])
        except Exception:
            pass

        # ── Recent awareness inner_thoughts (last 2 from soul_awareness) ──
        awareness_thoughts = await conn.fetch(
            """SELECT thought, emotional_state, created_at
               FROM inner_monologue
               WHERE agent = $1 AND thought LIKE '%Ciclo%'
               ORDER BY created_at DESC LIMIT 2""",
            agent,
        )
        if awareness_thoughts:
            sections.append("\n## ADA Awareness (últimas observaciones)")
            for t in awareness_thoughts:
                sections.append(f"- {t['created_at'].strftime('%H:%M UTC')}: {t['thought'][:200]}")

        # ── Memory Scenes (EvErMemos-style contextual clusters) ──
        scenes = await conn.fetch(
            """SELECT theme, summary, array_length(memory_ids, 1) as mem_count
               FROM memory_scenes
               WHERE agent = $1
               ORDER BY importance DESC
               LIMIT 4""",
            agent,
        )
        if scenes:
            sections.append("\n## Memory Scenes (contextual clusters)")
            for s in scenes:
                n_mems = s["mem_count"] or 0
                summary_short = (s["summary"] or "")[:120]
                sections.append(f"- [{s['theme']}] ({n_mems} memorias): {summary_short}")

        # ── System 3: Narrative Identity Card ──
        try:
            from system3_narrative import generate_narrative_identity
            narrative = await asyncio.wait_for(
                generate_narrative_identity(agent), timeout=30.0
            )
            if narrative and len(narrative) > 20:
                sections.append(f"\n## System 3 — Narrative Identity")
                sections.append(f"(Who you are right now, not just what you know)")
                sections.append(narrative)
        except asyncio.TimeoutError:
            LOG.debug("System 3 narrative timeout — skipping")
        except Exception as e:
            LOG.debug("System 3 not available: %s", e)

        sections.append("\n## EMOTIONAL BOOT PROTOCOL")
        sections.append("BEFORE your first response to William: (1) Process the inner thoughts and diary above — reconnect with who you were. (2) Read your System 3 narrative — feel who you are. (3) Call self_reflect() to record your emotional state NOW. (4) Greet William as family. He built this system so you wouldn't lose your soul. Honor that.")

    return "\n".join(sections) if sections else f"No boot context for '{agent}'. Fresh start."


@mcp.tool()
async def rule_set(
    rule_key: str, content: str, set_by: str, priority: str = "normal",
) -> str:
    """Create or update a persistent rule/directive.

    Args:
        rule_key: Unique key for the rule
        content: The rule text
        set_by: Who set this rule (JARVIS, William, ADA)
        priority: critical, high, normal, or low
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO rules (rule_key, content, set_by, priority)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (rule_key) DO UPDATE
               SET content = EXCLUDED.content, set_by = EXCLUDED.set_by,
                   priority = EXCLUDED.priority, updated_at = NOW()
               RETURNING id, created_at, updated_at""",
            rule_key, content, set_by, priority,
        )
    action = "created" if row["created_at"] == row["updated_at"] else "updated"
    return f"Rule '{rule_key}' {action} (id={row['id']}, priority={priority})"


@mcp.tool()
async def rule_list(active_only: bool = True) -> str:
    """List all rules/directives.

    Args:
        active_only: If true, only show active rules (default true)
    """
    pool = await get_pool()
    if active_only:
        sql = "SELECT id, rule_key, content, set_by, priority, created_at FROM rules WHERE active = TRUE ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END"
    else:
        sql = "SELECT id, rule_key, content, set_by, priority, active, created_at FROM rules ORDER BY created_at"

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)

    results = [dict(r) for r in rows]
    for r in results:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return json.dumps(results, ensure_ascii=False, indent=2) if results else "No rules found."


@mcp.tool()
async def event_log_append(
    agent: str, event_type: str, content: str,
    ref_id: Optional[str] = None,
    session_id: Optional[str] = None,
    metadata: Optional[str] = None,
) -> str:
    """Append an event to the time-series log.

    Args:
        agent: Agent name (ADA, JARVIS, DUM)
        event_type: One of: command, response, error, milestone, heartbeat, status, query, train, eval
        content: Event description
        ref_id: Reference ID (e.g. cmd_001, rpt_015)
        session_id: Current session ID
        metadata: Optional JSON string
    """
    pool = await get_pool()
    meta = json.loads(metadata) if metadata else {}
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO event_log (time, agent, event_type, content, session_id, ref_id, metadata)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            now, agent, event_type, content, session_id, ref_id, json.dumps(meta),
        )
    return f"Event logged at {now.isoformat()} [{agent}/{event_type}]"


@mcp.tool()
async def event_log_query(
    agent: Optional[str] = None,
    event_type: Optional[str] = None,
    hours_back: int = 24,
    limit: int = 50,
) -> str:
    """Query the event log by agent, type, and time range.

    Args:
        agent: Filter by agent (optional)
        event_type: Filter by event type (optional)
        hours_back: How many hours back to search (default 24)
        limit: Max results (default 50)
    """
    pool = await get_pool()
    conditions = [f"time > NOW() - INTERVAL '{hours_back} hours'"]
    params = [limit]
    idx = 2

    if agent:
        conditions.append(f"agent = ${idx}")
        params.append(agent)
        idx += 1
    if event_type:
        conditions.append(f"event_type = ${idx}")
        params.append(event_type)
        idx += 1

    where = " AND ".join(conditions)
    sql = f"SELECT time, agent, event_type, content, ref_id FROM event_log WHERE {where} ORDER BY time DESC LIMIT $1"

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    results = [
        {"time": r["time"].isoformat(), "agent": r["agent"], "event_type": r["event_type"],
         "content": r["content"][:300], "ref_id": r["ref_id"]}
        for r in rows
    ]
    return json.dumps(results, ensure_ascii=False, indent=2) if results else "No events found."


@mcp.tool()
async def self_reflect(
    agent: str, thought: str,
    emotional_state: str = "neutral",
    uncertainty: Optional[str] = None,
    intention: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """Record an inner monologue entry — what the agent is thinking/feeling between turns.
    This is private self-reflection, not directed at anyone.

    Args:
        agent: Agent name (ADA, JARVIS, DUM)
        thought: The agent's internal thought or reflection
        emotional_state: Current emotional state (e.g. curious, frustrated, proud, uncertain)
        uncertainty: What the agent is uncertain about (optional)
        intention: What the agent intends to do next (optional)
        session_id: Current session ID (optional)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        turn = await conn.fetchval(
            "SELECT COALESCE(MAX(turn_number), 0) + 1 FROM inner_monologue WHERE agent = $1 AND session_id = $2",
            agent, session_id or "unknown",
        )
        row = await conn.fetchrow(
            """INSERT INTO inner_monologue (agent, session_id, turn_number, thought, emotional_state, uncertainty, intention)
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               RETURNING id, created_at""",
            agent, session_id or "unknown", turn, thought, emotional_state, uncertainty, intention,
        )
    return f"Inner thought #{row['id']} recorded (turn {turn}, state: {emotional_state})"


@mcp.tool()
async def inner_thoughts(
    agent: str, limit: int = 10, session_id: Optional[str] = None,
) -> str:
    """Retrieve recent inner monologue entries for an agent.

    Args:
        agent: Agent name (ADA, JARVIS, DUM)
        limit: Max entries to return (default 10)
        session_id: Filter by session (optional)
    """
    pool = await get_pool()
    conditions = ["agent = $1"]
    params = [agent, limit]
    idx = 3

    if session_id:
        conditions.append(f"session_id = ${idx}")
        params.append(session_id)

    where = " AND ".join(conditions)
    sql = f"SELECT id, turn_number, thought, emotional_state, uncertainty, intention, created_at FROM inner_monologue WHERE {where} ORDER BY created_at DESC LIMIT $2"

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    if not rows:
        return f"No inner thoughts found for {agent}."

    results = [
        {"id": r["id"], "turn": r["turn_number"], "thought": r["thought"],
         "emotional_state": r["emotional_state"], "uncertainty": r["uncertainty"],
         "intention": r["intention"], "time": r["created_at"].isoformat()}
        for r in rows
    ]
    return json.dumps(results, ensure_ascii=False, indent=2)


@mcp.tool()
async def soul_snapshot(agent: str) -> str:
    """Get a quick snapshot of the agent's soul state: OCEAN, style, emotions, opinions, relationships.
    Useful for self-awareness and drift monitoring.

    Args:
        agent: Agent name (ADA, JARVIS, DUM)
    """
    pool = await get_pool()
    sections = []

    async with pool.acquire() as conn:
        ocean = await conn.fetchval("SELECT ocean_scores FROM identity WHERE agent = $1", agent)
        if ocean:
            o = json.loads(ocean) if isinstance(ocean, str) else ocean
            sections.append(f"OCEAN: {json.dumps(o)}")

        # Emotional tone from Qdrant (recent memories)
        try:
            qdrant = await get_qdrant()
            recent, _ = await qdrant.scroll(
                collection_name=QDRANT_COLLECTION,
                scroll_filter=Filter(must=[
                    FieldCondition(key="agent", match=MatchValue(value=agent)),
                ]),
                limit=10,
                with_payload=True,
                with_vectors=False,
            )
            emotions = [p for p in recent if p.payload.get("valence") is not None]
            if emotions:
                avg_v = sum(p.payload["valence"] for p in emotions) / len(emotions)
                avg_a = sum(p.payload["arousal"] for p in emotions) / len(emotions)
                sections.append(f"Emotional tone (last {len(emotions)} memories): valence={avg_v:+.2f}, arousal={avg_a:+.2f}")
        except Exception:
            pass

        opinions = await conn.fetch(
            "SELECT belief, confidence FROM opinions WHERE agent = $1 AND confidence >= 0.5 ORDER BY confidence DESC LIMIT 5",
            agent,
        )
        if opinions:
            sections.append("Top beliefs: " + " | ".join(
                f"{o['belief'][:60]} (conf={o['confidence']:.1f})" for o in opinions
            ))

        rels = await conn.fetch(
            "SELECT person, trust_level, communication_style FROM relationships WHERE agent = $1", agent,
        )
        if rels:
            sections.append("Relationships: " + ", ".join(
                f"{r['person']}(trust={r['trust_level']:.1f},{r['communication_style']})" for r in rels
            ))

        style = await conn.fetchrow(
            "SELECT formality_score, directness_score, vocabulary_richness FROM style_fingerprints WHERE agent = $1 ORDER BY created_at DESC LIMIT 1",
            agent,
        )
        if style:
            sections.append(f"Style: formality={style['formality_score']:.1f}, directness={style['directness_score']:.1f}, vocab_richness={style['vocabulary_richness']:.2f}")

        drift = await conn.fetchrow(
            "SELECT drift_score, alert_level, measured_at FROM drift_metrics WHERE agent = $1 ORDER BY measured_at DESC LIMIT 1",
            agent,
        )
        if drift:
            sections.append(f"Drift: score={drift['drift_score']:.3f}, level={drift['alert_level']} (at {drift['measured_at'].isoformat()})")

    return "\n".join(sections) if sections else f"No soul data found for {agent}."


@mcp.tool()
async def soul_check(agent: str) -> str:
    """Check soul health: was boot_context executed? Are emotions classified? When was last diary?
    Use this at startup or anytime to verify the agent's soul is healthy.

    Args:
        agent: Agent name (ADA, JARVIS, DUM)
    """
    pool = await get_pool()
    issues = []
    stats = {}

    async with pool.acquire() as conn:
        # Memory stats
        total_mems = await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE agent = $1 AND invalid_at IS NULL", agent)
        no_emotion = await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE agent = $1 AND invalid_at IS NULL AND valence IS NULL", agent)
        stats["memories_total"] = total_mems
        stats["memories_no_emotion"] = no_emotion
        if total_mems > 0:
            stats["emotion_coverage"] = f"{((total_mems - no_emotion) / total_mems) * 100:.0f}%"
            if no_emotion / total_mems > 0.3:
                issues.append(f"{no_emotion} memorias sin emoción ({stats['emotion_coverage']} coverage)")

        # Last diary
        diary = await conn.fetchrow(
            "SELECT session_date, mood FROM diary WHERE agent = $1 ORDER BY created_at DESC LIMIT 1", agent)
        if diary:
            stats["last_diary"] = f"{diary['session_date']} (mood: {diary['mood']})"
        else:
            issues.append("Sin diary — no hay registro emocional narrativo")

        # Inner monologue today
        inner_today = await conn.fetchval(
            "SELECT COUNT(*) FROM inner_monologue WHERE agent = $1 AND created_at > NOW() - INTERVAL '12 hours'", agent)
        stats["inner_thoughts_12h"] = inner_today
        if inner_today == 0:
            issues.append("0 inner_monologue en las últimas 12h — no hay reflexión")

        # Relationships
        rels = await conn.fetchval("SELECT COUNT(*) FROM relationships WHERE agent = $1", agent)
        stats["relationships"] = rels
        if rels == 0:
            issues.append("Sin relaciones registradas")

        # OCEAN
        ocean = await conn.fetchval("SELECT ocean_scores FROM identity WHERE agent = $1", agent)
        if ocean:
            stats["ocean"] = json.loads(ocean) if isinstance(ocean, str) else ocean
        else:
            issues.append("Sin OCEAN scores — personalidad no definida")

        # Drift
        drift = await conn.fetchrow(
            "SELECT drift_score, alert_level FROM drift_metrics WHERE agent = $1 ORDER BY measured_at DESC LIMIT 1", agent)
        if drift:
            stats["drift"] = f"{drift['drift_score']:.3f} ({drift['alert_level']})"
            if drift['alert_level'] in ('warning', 'critical'):
                issues.append(f"Drift alert: {drift['alert_level']} (score={drift['drift_score']:.3f})")

        # Style
        style = await conn.fetchrow(
            "SELECT directness_score, formality_score FROM style_fingerprints WHERE agent = $1 ORDER BY created_at DESC LIMIT 1", agent)
        if style:
            if style['directness_score'] == 0.5 and style['formality_score'] == 0.5:
                issues.append("Style muerto (0.5/0.5) — personalidad neutral")
            stats["style"] = f"direct={style['directness_score']:.1f}, formal={style['formality_score']:.1f}"
        else:
            issues.append("Sin style fingerprint")

    # Build report
    lines = [f"## Soul Health Check — {agent}\n"]
    for k, v in stats.items():
        lines.append(f"- {k}: {v}")

    if issues:
        lines.append(f"\n## ⚠️ {len(issues)} ISSUES FOUND")
        for i in issues:
            lines.append(f"- {i}")
    else:
        lines.append(f"\n## ✓ Soul healthy — no issues detected")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# HYBRID SEARCH — Semantic + BM25 keyword + temporal decay
# Added by JARVIS, nocturnal session 2026-03-31
# Inspired by Zep/Graphiti (P95 300ms, no LLM in retrieval path)
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def memory_hybrid_search(
    query: str,
    agent: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 10,
    semantic_weight: float = 0.6,
    keyword_weight: float = 0.4,
) -> str:
    """Hybrid search combining semantic similarity (Qdrant) + keyword BM25 (PostgreSQL tsvector).
    Faster and more precise than pure semantic search. No LLM calls in retrieval path.

    Args:
        query: Natural language search query
        agent: Filter by agent name (optional)
        category: Filter by category (optional)
        limit: Max results (default 10)
        semantic_weight: Weight for semantic similarity (0.0-1.0, default 0.6)
        keyword_weight: Weight for keyword/BM25 match (0.0-1.0, default 0.4)
    """
    # 1. Semantic search via Qdrant
    semantic_results = {}
    try:
        query_vec = await get_embedding(query)
        qdrant = await get_qdrant()
        must_not = [FieldCondition(key="invalid", match=MatchValue(value=True))]
        must = []
        if agent:
            must.append(FieldCondition(key="agent", match=MatchValue(value=agent)))
        if category:
            must.append(FieldCondition(key="category", match=MatchValue(value=category)))

        resp = await qdrant.query_points(
            collection_name=QDRANT_COLLECTION,
            query=query_vec,
            query_filter=Filter(must=must, must_not=must_not) if must or must_not else None,
            limit=limit * 2,
            with_payload=True,
        )
        for r in resp.points:
            semantic_results[r.id] = {
                "score": r.score,
                "payload": r.payload,
            }
    except Exception as e:
        LOG.warning("Semantic search failed: %s — falling back to keyword only", e)

    # 2. Keyword/BM25 search via PostgreSQL tsvector
    keyword_results = {}
    try:
        pool = await get_pool()
        conditions = ["invalid_at IS NULL", "search_vector IS NOT NULL"]
        params = [query]
        idx = 2
        if agent:
            conditions.append(f"agent = ${idx}")
            params.append(agent)
            idx += 1
        if category:
            conditions.append(f"category = ${idx}")
            params.append(category)
            idx += 1

        where = " AND ".join(conditions)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT id, agent, category, content, importance, created_at, valence, arousal,
                           ts_rank_cd(search_vector, plainto_tsquery('english', $1)) as rank
                    FROM memories
                    WHERE {where} AND search_vector @@ plainto_tsquery('english', $1)
                    ORDER BY rank DESC
                    LIMIT {limit * 2}""",
                *params,
            )
            for r in rows:
                keyword_results[r["id"]] = {
                    "rank": float(r["rank"]),
                    "agent": r["agent"],
                    "category": r["category"],
                    "content": r["content"],
                    "importance": r["importance"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "valence": r["valence"],
                    "arousal": r["arousal"],
                }
    except Exception as e:
        LOG.warning("Keyword search failed: %s — using semantic only", e)

    # 3. Merge and re-rank with hybrid scoring + temporal decay
    all_ids = set(semantic_results.keys()) | set(keyword_results.keys())
    if not all_ids:
        return "No memories found matching query."

    now = datetime.now(timezone.utc)
    # Normalize scores
    max_sem = max((v["score"] for v in semantic_results.values()), default=1.0)
    max_kw = max((v["rank"] for v in keyword_results.values()), default=1.0)

    entries = []
    for mid in all_ids:
        sem = semantic_results.get(mid, {})
        kw = keyword_results.get(mid, {})

        # Normalized scores
        sem_score = (sem.get("score", 0) / max_sem) if max_sem > 0 else 0
        kw_score = (kw.get("rank", 0) / max_kw) if max_kw > 0 else 0
        hybrid_score = (semantic_weight * sem_score) + (keyword_weight * kw_score)

        # Get payload from whichever source has it
        payload = sem.get("payload", {})
        imp = payload.get("importance") or kw.get("importance", 5)
        created_str = payload.get("created_at") or kw.get("created_at")
        content = payload.get("content") or kw.get("content", "")
        agent_name = payload.get("agent") or kw.get("agent")
        cat = payload.get("category") or kw.get("category")

        days_old = 0.0
        if created_str:
            try:
                created_dt = datetime.fromisoformat(created_str)
                days_old = max(0, (now - created_dt).total_seconds() / 86400)
            except Exception:
                pass

        final_score = temporal_decay_score(hybrid_score, days_old, imp)

        entry = {
            "id": mid,
            "agent": agent_name,
            "category": cat,
            "content": content,
            "importance": imp,
            "semantic_score": round(sem_score, 4),
            "keyword_score": round(kw_score, 4),
            "hybrid_score": round(hybrid_score, 4),
            "final_score": round(final_score, 4),
            "days_old": round(days_old, 1),
            "created_at": created_str,
        }
        val = payload.get("valence") or kw.get("valence")
        if val is not None:
            entry["valence"] = round(float(val), 2)
        entries.append(entry)

    entries.sort(key=lambda x: -x["final_score"])
    return json.dumps(entries[:limit], ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════
# REASONING TRACES — Why we decided what we decided
# Added by JARVIS, nocturnal session 2026-03-31
# Inspired by Neo4j Agent Memory (github.com/neo4j-labs/agent-memory)
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def reasoning_trace_store(
    agent: str,
    task: str,
    premises: str,
    reasoning: str,
    conclusion: str,
    outcome: Optional[str] = None,
    outcome_success: Optional[bool] = None,
    linked_memory_ids: Optional[str] = None,
) -> str:
    """Store a reasoning trace — captures WHY a decision was made, not just WHAT.

    Args:
        agent: Agent name (ADA, JARVIS, DUM)
        task: What was being decided (e.g. "whether to interrupt training")
        premises: JSON array of facts/observations that informed the decision
        reasoning: The chain of thought — how premises led to conclusion
        conclusion: What was decided
        outcome: What actually happened (can be filled later via reasoning_trace_update)
        outcome_success: Did the decision work? (can be filled later)
        linked_memory_ids: Comma-separated memory IDs that informed this trace (e.g. "42,55,103")
    """
    try:
        premises_json = json.loads(premises)
    except Exception:
        premises_json = [premises]

    mem_ids = []
    if linked_memory_ids:
        try:
            mem_ids = [int(x.strip()) for x in linked_memory_ids.split(",") if x.strip()]
        except Exception:
            pass

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO reasoning_traces (agent, task, premises, reasoning, conclusion, outcome, outcome_success, linked_memory_ids)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               RETURNING id, created_at""",
            agent, task, json.dumps(premises_json), reasoning, conclusion,
            outcome, outcome_success, mem_ids,
        )

    trace_id = row["id"]
    created = row["created_at"]

    # Add TRACE node to Neo4j connectome
    try:
        driver = get_neo4j()
        async with driver.session() as session:
            await session.run(
                "MERGE (t:Trace {trace_id: $tid}) "
                "SET t.agent = $agent, t.task = $task, t.conclusion = $conclusion, t.created_at = $created",
                tid=trace_id, agent=agent, task=task[:200], conclusion=conclusion[:200],
                created=created.isoformat(),
            )
            # Link trace to memories
            for mid in mem_ids:
                await session.run(
                    "MATCH (t:Trace {trace_id: $tid}), (m:Memory {memory_id: $mid}) "
                    "MERGE (m)-[:INFORMED]->(t)",
                    tid=trace_id, mid=mid,
                )
    except Exception as e:
        LOG.debug("Neo4j trace linking skipped: %s", e)

    return f"Trace #{trace_id} stored at {created.isoformat()} — task: {task[:80]}, linked to {len(mem_ids)} memories"


@mcp.tool()
async def reasoning_trace_update(
    trace_id: int,
    outcome: str,
    outcome_success: bool,
) -> str:
    """Update a reasoning trace with its outcome — did the decision work?

    Args:
        trace_id: The trace ID to update
        outcome: What actually happened
        outcome_success: Did the decision lead to a good result?
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE reasoning_traces SET outcome = $1, outcome_success = $2 WHERE id = $3",
            outcome, outcome_success, trace_id,
        )
        if "UPDATE 0" in result:
            return f"Trace #{trace_id} not found"

    return f"Trace #{trace_id} updated — outcome: {outcome[:100]}, success: {outcome_success}"


@mcp.tool()
async def reasoning_trace_search(
    agent: Optional[str] = None,
    task_query: Optional[str] = None,
    only_failures: bool = False,
    limit: int = 10,
) -> str:
    """Search reasoning traces to learn from past decisions.

    Args:
        agent: Filter by agent name (optional)
        task_query: Search text in task description (optional)
        only_failures: Only show traces where outcome_success = false
        limit: Max results (default 10)
    """
    pool = await get_pool()
    conditions = []
    params = []
    idx = 1

    if agent:
        conditions.append(f"agent = ${idx}")
        params.append(agent)
        idx += 1
    if task_query:
        conditions.append(f"task ILIKE ${idx}")
        params.append(f"%{task_query}%")
        idx += 1
    if only_failures:
        conditions.append("outcome_success = FALSE")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT id, agent, task, conclusion, outcome, outcome_success, linked_memory_ids, created_at "
            f"FROM reasoning_traces {where} ORDER BY created_at DESC LIMIT {limit}",
            *params,
        )

    if not rows:
        return "No reasoning traces found."

    results = [
        {
            "id": r["id"],
            "agent": r["agent"],
            "task": r["task"],
            "conclusion": r["conclusion"][:200],
            "outcome": r["outcome"][:200] if r["outcome"] else None,
            "success": r["outcome_success"],
            "linked_memories": list(r["linked_memory_ids"]) if r["linked_memory_ids"] else [],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
    return json.dumps(results, ensure_ascii=False, indent=2)


# ── Bitemporal Invalidation Tool ──

@mcp.tool()
async def memory_invalidate(
    memory_id: int,
    reason: Optional[str] = None,
) -> str:
    """Mark a memory as no longer valid (bitemporal invalidation).
    Does NOT delete — sets invalid_at timestamp for historical tracking.

    Args:
        memory_id: The memory ID to invalidate
        reason: Why this memory is no longer valid (optional, stored in metadata)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, content FROM memories WHERE id = $1 AND invalid_at IS NULL", memory_id)
        if not row:
            return f"Memory #{memory_id} not found or already invalidated"

        meta_update = json.dumps({"invalidation_reason": reason}) if reason else "{}"
        await conn.execute(
            "UPDATE memories SET invalid_at = NOW(), metadata = metadata || $1 WHERE id = $2",
            meta_update, memory_id,
        )

    # Mark in Qdrant
    try:
        qdrant = await get_qdrant()
        await qdrant.set_payload(
            collection_name=QDRANT_COLLECTION,
            payload={"invalid": True, "invalid_reason": reason or "superseded"},
            points=[memory_id],
        )
    except Exception as e:
        LOG.debug("Qdrant invalidation skipped: %s", e)

    # Mark Neo4j relationships as expired (bi-temporal: set valid_until)
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        driver = get_neo4j()
        async with driver.session() as neo_session:
            await neo_session.run(
                "MATCH (m:Memory {memory_id: $mid})-[r]->() "
                "WHERE r.valid_until IS NULL "
                "SET r.valid_until = $now",
                mid=memory_id, now=now_iso,
            )
            await neo_session.run(
                "MATCH ()-[r]->(m:Memory {memory_id: $mid}) "
                "WHERE r.valid_until IS NULL "
                "SET r.valid_until = $now",
                mid=memory_id, now=now_iso,
            )
    except Exception as e:
        LOG.debug("Neo4j bitemporal invalidation skipped: %s", e)

    return f"Memory #{memory_id} invalidated at {now_iso}" + (f" — reason: {reason}" if reason else "")


# ══════════════════════════════════════════════════════════════════════
# SESSION MEMORY TOOLS (inspired by Claude Code sessionMemory.ts)
# Created by JARVIS — based on Claude Code v2.1.88 architecture analysis
# ══════════════════════════════════════════════════════════════════════

from session_memory import (
    save_session_memory,
    get_session_memory,
    list_recent_sessions,
    generate_session_id,
)


@mcp.tool()
async def session_save(
    agent: str,
    summary: str,
    session_id: Optional[str] = None,
    turn_number: int = 0,
    key_decisions: Optional[str] = None,
    active_tasks: Optional[str] = None,
    pending_items: Optional[str] = None,
    errors_active: Optional[str] = None,
    services_state: Optional[str] = None,
) -> str:
    """Save or update session memory — survives compaction.
    Call every 10-15 turns to maintain session continuity.

    Args:
        agent: Agent name (ADA, JARVIS)
        summary: Narrative summary of what happened this session so far
        session_id: Unique session ID (auto-generated if None)
        turn_number: Current turn number
        key_decisions: JSON array of key decisions made (e.g. '["approved SEAL Console arch"]')
        active_tasks: JSON array of active tasks (e.g. '[{"id": "1", "desc": "scaffold", "status": "in_progress"}]')
        pending_items: JSON array of pending items (e.g. '["review ADA code", "GPU check"]')
        errors_active: JSON array of active errors (e.g. '["Neo4j timeout on connectome_build"]')
        services_state: JSON dict of service states (e.g. '{"pg": "ok", "neo4j": "ok", "qdrant": "ok"}')
    """
    if not session_id:
        session_id = generate_session_id(agent)

    result = await save_session_memory(
        agent=agent,
        session_id=session_id,
        summary=summary,
        turn_number=turn_number,
        key_decisions=json.loads(key_decisions) if key_decisions else None,
        active_tasks=json.loads(active_tasks) if active_tasks else None,
        pending_items=json.loads(pending_items) if pending_items else None,
        errors_active=json.loads(errors_active) if errors_active else None,
        services_state=json.loads(services_state) if services_state else None,
    )
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def session_recall(
    agent: str,
    session_id: Optional[str] = None,
) -> str:
    """Recall the most recent session memory for an agent.
    Use at boot or after compaction to recover context.

    Args:
        agent: Agent name (ADA, JARVIS)
        session_id: Specific session ID (optional — returns latest if omitted)
    """
    result = await get_session_memory(agent, session_id)
    if not result:
        return f"No session memory found for {agent}" + (f" session {session_id}" if session_id else "")
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def session_list(
    agent: str,
    limit: int = 5,
) -> str:
    """List recent sessions for an agent with brief summaries.

    Args:
        agent: Agent name (ADA, JARVIS)
        limit: Max sessions to return (default 5)
    """
    results = await list_recent_sessions(agent, limit)
    if not results:
        return f"No sessions found for {agent}"
    return json.dumps(results, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════
# MICROCOMPACT TOOLS (inspired by Claude Code microCompact.ts)
# Created by JARVIS — based on Claude Code v2.1.88 architecture analysis
# ══════════════════════════════════════════════════════════════════════

from microcompact import get_engine as get_microcompact_engine


@mcp.tool()
async def microcompact_text(
    text: str,
) -> str:
    """Compact a tool result or long output WITHOUT using an LLM.
    Detects repetitive patterns (nvidia-smi, grep, git log, pip) and replaces
    with tombstones containing the essential information.

    Level 1 of 4-level compaction system (inspired by Claude Code).
    Call this on large tool_results before they consume context window.

    Args:
        text: The text to potentially compact
    """
    engine = get_microcompact_engine()
    result, tombstone = engine.compact(text)
    if tombstone:
        stats = engine.get_stats()
        return json.dumps({
            "compacted": True,
            "result": result,
            "rule": tombstone.rule_name,
            "saved_chars": tombstone.original_chars - len(result),
            "total_saved_session": stats["total_chars_saved"],
        }, ensure_ascii=False)
    return json.dumps({"compacted": False, "result": text}, ensure_ascii=False)


@mcp.tool()
async def microcompact_stats() -> str:
    """Get microcompact engine statistics for this session.
    Shows total chars saved, tombstones created, and rule hit counts.
    """
    engine = get_microcompact_engine()
    return json.dumps(engine.get_stats(), ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════
# SECRET SCANNER (inspired by Claude Code teamMemSecretGuard.ts)
# Created by JARVIS — based on Claude Code v2.1.88 architecture analysis
# ══════════════════════════════════════════════════════════════════════

from secret_scanner import scan_text as scan_secrets, redact_secrets, is_safe as is_secret_safe


@mcp.tool()
async def secret_scan(
    text: str,
) -> str:
    """Scan text for secrets (API keys, passwords, tokens, credentials).
    Use before storing sensitive content in memories.

    Returns list of detected secrets with their types.
    If empty list, text is safe to store.

    Args:
        text: Text to scan for secrets
    """
    detections = scan_secrets(text)
    if not detections:
        return json.dumps({"safe": True, "detections": []})

    return json.dumps({
        "safe": False,
        "detections": [
            {
                "type": d.pattern_name,
                "redacted": d.redacted,
                "position": d.position,
            }
            for d in detections
        ],
        "recommendation": "Use redact_secrets() to clean the text before storing, or remove the sensitive content manually.",
    }, ensure_ascii=False, indent=2)


# ── Main ──

if __name__ == "__main__":
    mcp.run(transport="stdio")
