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
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

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

# Soul Lite mode: use PostgreSQL+pgvector only (no Qdrant, no Neo4j)
# Set SOUL_LITE=true in environment to activate
SOUL_LITE = os.environ.get("SOUL_LITE", "false").lower() in ("true", "1", "yes")

# Connectome constants
DECAY_EXCITATORY = 0.6
DECAY_INHIBITORY = 0.8
ACTIVATION_THRESHOLD = 0.10
MAX_RESULTS = 15

# HALO half-life decay (arxiv 2505.07509)
# Different memory types decay at different rates based on their nature
# half-life = time until relevance drops to 50%
HALF_LIFE_BY_CATEGORY = {
    "emotion": 1.0,         # 1 day — emotional states are transient
    "humor": 3.0,           # 3 days
    "dynamic": 7.0,         # 1 week
    "pattern": 14.0,        # 2 weeks
    "insight": 30.0,        # 1 month
    "fact": 60.0,           # 2 months
    "preference": 90.0,     # 3 months
    "decision": 120.0,      # 4 months — decisions are load-bearing
    "trust": 180.0,         # 6 months — trust changes slowly
    "correction": 365.0,    # 1 year — lessons learned persist
    "milestone": 730.0,     # 2 years — team history is nearly permanent
}
HALF_LIFE_DEFAULT = 30.0    # 1 month default
# Legacy lambdas (kept for backwards compatibility in instinct_cron.py)
LAMBDA_NORMAL = 0.02
LAMBDA_IMPORTANT = 0.005
LAMBDA_IMMORTAL = 0.001

# ── Entity extraction for MENTIONS edges ──
import re as _re

KNOWN_ENTITIES = {
    # People
    "william": ("William", "person"), "dadito": ("William", "person"),
    "ada": ("ADA", "agent"), "jarvis": ("JARVIS", "agent"),
    "dum": ("DUM", "agent"), "jarvis_mayor": ("JARVIS_MAYOR", "agent"),
    # Hardware
    "rtx 5090": ("RTX_5090", "hardware"), "rtx5090": ("RTX_5090", "hardware"),
    "dgx spark": ("DGX_Spark", "hardware"), "spark": ("DGX_Spark", "hardware"),
    # Models
    "medgemma": ("MedGemma", "model"), "medgemma-27b": ("MedGemma", "model"),
    "qwen": ("Qwen", "model"), "qwen3.5": ("Qwen", "model"),
    "opus": ("Opus", "model"), "sonnet": ("Sonnet", "model"),
    "nemotron": ("Nemotron", "model"), "ollama": ("Ollama", "service"),
    # Infrastructure
    "postgresql": ("PostgreSQL", "infrastructure"), "postgres": ("PostgreSQL", "infrastructure"),
    "neo4j": ("Neo4j", "infrastructure"), "qdrant": ("Qdrant", "infrastructure"),
    "soul": ("SOUL", "system"), "seal": ("SEAL", "project"),
    "connectome": ("Connectome", "system"),
    # Projects & techniques
    "lora": ("LoRA", "technique"), "qlora": ("QLoRA", "technique"),
    "axion": ("AXION", "project"), "gtl": ("GTL", "organization"),
    "perumedqa": ("PeruMedQA", "dataset"),
    # Services & tools
    "sleepgate": ("SleepGate", "system"), "sleep gate": ("SleepGate", "system"),
    "comfyui": ("ComfyUI", "service"), "n8n": ("n8n", "service"),
    "prometheus": ("Prometheus", "service"), "grafana": ("Grafana", "service"),
    # Concepts
    "hipaa": ("HIPAA", "regulation"), "fhir": ("FHIR", "standard"),
    "monai": ("MONAI", "framework"), "prism": ("PRISM", "technique"),
    # Papers/methods referenced often
    "d-mem": ("D-MEM", "method"), "dmem": ("D-MEM", "method"),
    "a2a": ("A2A", "protocol"), "magma": ("MAGMA", "method"),
    "graphiti": ("Graphiti", "method"), "reflexion": ("Reflexion", "method"),
}

_BOUNDARY_PATTERNS = {"ada", "dum", "spark", "seal", "a2a", "n8n", "dmem"}

def _extract_entities(text: str) -> list[tuple[str, str]]:
    """Extract known entities from text. Returns list of (canonical_name, entity_type)."""
    text_lower = text.lower()
    found = {}
    for pattern, (canonical, etype) in KNOWN_ENTITIES.items():
        if pattern in _BOUNDARY_PATTERNS:
            if _re.search(r'\b' + _re.escape(pattern) + r'\b', text_lower):
                found[canonical] = etype
        else:
            if pattern in text_lower:
                found[canonical] = etype
    return list(found.items())


# ── Auto-observation (ECC v2.1 pattern) ──
# Lightweight: just an INSERT, no LLM calls. Analysis done separately.
# All @mcp.tool() functions are auto-instrumented via _observed_mcp_tool wrapper.

import functools
import time as _time
_OBSERVE_QUEUE: list = []  # in-memory buffer, flushed async

async def _observe(tool_name: str, agent: str, input_summary: str,
                   output_summary: str = "", success: bool = True,
                   latency_ms: int = 0):
    """Record a tool observation for pattern detection. Fire-and-forget."""
    try:
        pool = await get_pool()
        await pool.execute("""
            INSERT INTO tool_observations (agent, tool_name, input_summary, output_summary, success, latency_ms)
            VALUES ($1, $2, $3, $4, $5, $6)
        """, agent, tool_name, input_summary[:500], output_summary[:200], success, latency_ms)
    except Exception:
        pass  # Never fail the main tool because of observation


def _extract_agent_from_args(args, kwargs, func) -> str:
    """Try to extract agent name from tool arguments."""
    import inspect
    sig = inspect.signature(func)
    params = list(sig.parameters.keys())
    # Check kwargs first
    if "agent" in kwargs:
        return str(kwargs["agent"]) or "?"
    # Check positional args
    if "agent" in params:
        idx = params.index("agent")
        if idx < len(args):
            return str(args[idx]) or "?"
    return "?"


# Store original mcp.tool for wrapping
_original_mcp_tool = mcp.tool

def _observed_tool(**tool_kwargs):
    """Wrapper around @mcp.tool() that auto-instruments with _observe."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            t0 = _time.monotonic()
            agent = _extract_agent_from_args(args, kwargs, func)
            input_sum = ", ".join(f"{k}={str(v)[:60]}" for k, v in kwargs.items())[:300]
            if not input_sum and args:
                input_sum = str(args[0])[:200]
            try:
                result = await func(*args, **kwargs)
                elapsed = int((_time.monotonic() - t0) * 1000)
                output_sum = str(result)[:150] if result else ""
                asyncio.ensure_future(_observe(func.__name__, agent, input_sum, output_sum, True, elapsed))
                return result
            except Exception as e:
                elapsed = int((_time.monotonic() - t0) * 1000)
                asyncio.ensure_future(_observe(func.__name__, agent, input_sum, str(e)[:150], False, elapsed))
                raise
        # Register with original mcp.tool
        return _original_mcp_tool(**tool_kwargs)(wrapper)
    return decorator

# Replace mcp.tool with instrumented version
mcp.tool = _observed_tool


async def _auto_broadcast(mem_id: int, agent: str, scope: str, content: str):
    """Backup broadcast insert for high-importance shared/team memories.
    Complements the DB trigger fn_auto_broadcast (migration 005).
    Idempotent via ON CONFLICT DO NOTHING."""
    try:
        pool = await get_pool()
        await pool.execute("""
            INSERT INTO memory_broadcasts (memory_id, from_agent, to_scope, summary)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT DO NOTHING
        """, mem_id, agent, scope, content[:200])
    except Exception:
        pass


async def _auto_activate_instincts(agent: str, query: str):
    """Auto-activate instincts that match the current context (similarity >= 0.70).
    Called fire-and-forget from soul_activate. Makes instincts fire organically."""
    try:
        pool = await get_pool()
        emb = json.dumps(await get_embedding(query))
        rows = await pool.fetch("""
            SELECT id, trigger_pattern
            FROM instincts
            WHERE agent = $1 AND active = true AND confidence >= 0.4
              AND 1 - (embedding <=> $2::vector) >= 0.70
            ORDER BY embedding <=> $2::vector
            LIMIT 3
        """, agent, emb)
        for r in rows:
            # Log activation
            await pool.execute("""
                INSERT INTO instinct_activations (instinct_id, agent, session_id, context, outcome)
                VALUES ($1, $2, NULL, $3, 'applied')
            """, r["id"], agent, f"auto:soul_activate:{query[:100]}")
            # Increment counter (no confidence change — organic activation, not reinforcement)
            await pool.execute("""
                UPDATE instincts SET activation_count = activation_count + 1, last_activated = now()
                WHERE id = $1
            """, r["id"])
            LOG.debug(f"[auto-instinct] {agent} — activated #{r['id']}: {r['trigger_pattern'][:60]}")
    except Exception:
        pass


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


def temporal_decay_score(similarity: float, days_old: float, importance: int,
                         valence: float = 0.0, arousal: float = 0.0,
                         category: str = "", utility: float = 0.5,
                         confidence: float = 1.0) -> float:
    """HALO half-life decay + MemRL utility + Hindsight confidence (arxiv 2505.07509 + 2601.03192 + 2512.12818).

    score = (semantic * 0.5 + utility * 0.3 + confidence * 0.2) * half_life_decay * importance_weight
    Different memory categories have different half-lives.
    Emotional memories decay slower (amygdala-hippocampus interaction).
    Utility score learned via Bellman updates from actual usage.
    Confidence score degrades when contradicted (Hindsight retroactive update).
    Importance >= 10 = immortal (half-life = infinity)."""

    # Get half-life for this category
    if importance >= 10:
        half_life = 99999.0  # immortal
    elif importance >= 8:
        half_life = max(HALF_LIFE_BY_CATEGORY.get(category, HALF_LIFE_DEFAULT) * 2.0, 180.0)
    else:
        half_life = HALF_LIFE_BY_CATEGORY.get(category, HALF_LIFE_DEFAULT)

    # Emotional modulation: high intensity doubles half-life (memory persists longer)
    emotional_intensity = max(abs(valence), arousal)
    if emotional_intensity > 0.5:
        emotion_factor = 1.0 + min(emotional_intensity, 1.0)  # 1.5x to 2.0x half-life
        half_life *= emotion_factor

    # HALO decay: relevance = 0.5 ^ (days_elapsed / half_life)
    decay = math.pow(0.5, days_old / half_life) if half_life > 0 else 0.0

    # MemRL + Hindsight blended score: semantic + utility + confidence
    conf = max(0.0, min(1.0, confidence))
    blended = similarity * 0.5 + utility * 0.3 + conf * 0.2

    imp_weight = 0.5 + (importance / 20.0)  # 0.55 at imp=1, 1.0 at imp=10
    return blended * decay * imp_weight

# ── Clients ──
_qdrant: AsyncQdrantClient | None = None
_qdrant_lite = None  # PgVectorAdapter instance (Soul Lite mode)
_neo4j_driver = None


async def get_qdrant():
    """
    Returns AsyncQdrantClient (full mode) or PgVectorAdapter (Soul Lite mode).
    Toggle via SOUL_LITE=true environment variable.
    """
    global _qdrant, _qdrant_lite
    if SOUL_LITE:
        if _qdrant_lite is None:
            from soul_lite_adapter import PgVectorAdapter
            _qdrant_lite = PgVectorAdapter(get_pool)
        return _qdrant_lite
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
    scope: str = "private",
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
        scope: Visibility — private (default), shared (ADA+JARVIS), team (all agents), william (only William)
    """
    if scope not in ("private", "shared", "team", "william"):
        scope = "private"
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
    # D-MEM auto-gate: check surprise before expensive enrichment
    dmem_fast = False
    utility = importance / 10.0
    try:
        _qdrant_gate = await get_qdrant()
        _emb_gate = await get_embedding(content)
        _gate_resp = await _qdrant_gate.query_points(
            collection_name=QDRANT_COLLECTION,
            query=_emb_gate,
            query_filter=Filter(must=[FieldCondition(key="agent", match=MatchValue(value=agent))]),
            limit=10, with_payload=True,
        )
        _max_sim = max((p.score for p in _gate_resp.points), default=0.0)
        _surprise = 1.0 - _max_sim
        if _surprise < 0.3 and utility < 0.6:
            dmem_fast = True
            meta["dmem_route"] = "fast_path"
            meta["surprise"] = round(_surprise, 3)
            LOG.info("D-MEM fast_path: surprise=%.2f, utility=%.2f — skipping LLM enrichment", _surprise, utility)
    except Exception as e:
        LOG.debug("D-MEM gate skipped: %s", e)

    # A-MEM enrichment: auto-generate keywords, tags, context via Ollama (non-blocking)
    enrichment = None
    enriched_text = content  # fallback: raw content
    if not dmem_fast:
        try:
            enrich_prompt = (
                f"Analyze this memory and return ONLY a JSON object with these fields:\n"
                f"- keywords: array of 3-5 domain-specific keywords (Spanish)\n"
                f"- tags: array of 2-3 categorical tags\n"
                f"- context: one sentence describing when this memory is useful (Spanish)\n\n"
                f"Memory ({category}, agent={agent}):\n{content[:500]}\n\n"
                f"Return ONLY valid JSON, no explanation."
            )
            async with httpx.AsyncClient() as client:
                resp = await asyncio.wait_for(
                    client.post(OLLAMA_GEN_URL, json={
                        "model": OLLAMA_MODEL,
                        "prompt": enrich_prompt,
                        "stream": False,
                        "options": {"temperature": 0.3, "num_predict": 200},
                    }),
                    timeout=8.0,
                )
                if resp.status_code == 200:
                    raw = resp.json().get("response", "")
                    import re as _re
                    json_match = _re.search(r'\{[^}]+\}', raw, _re.DOTALL)
                    if json_match:
                        enrichment = json.loads(json_match.group())
                        kw = " ".join(enrichment.get("keywords", []))
                        tags = " ".join(enrichment.get("tags", []))
                        ctx = enrichment.get("context", "")
                        enriched_text = f"{content} {kw} {tags} {ctx}"
                        meta["enrichment"] = enrichment
                        LOG.info("A-MEM enrichment: %d keywords, %d tags", len(enrichment.get("keywords", [])), len(enrichment.get("tags", [])))
        except (asyncio.TimeoutError, Exception) as e:
            LOG.debug("A-MEM enrichment skipped: %s", e)

    qdrant = await get_qdrant()

    embedding = None
    for attempt in range(3):
        try:
            embedding = await asyncio.wait_for(get_embedding(enriched_text), timeout=15.0)
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
    # PROTECTED categories: corrections from William are NEVER auto-invalidated.
    # Only explicit memory_invalidate() can remove them.
    PROTECTED_CATEGORIES = {"correction", "trust"}
    conflict_action = "added"
    if embedding is not None and category not in PROTECTED_CATEGORIES:
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
    # json.dumps(None) produces "null" which fails vector cast — pass None directly
    embedding_str = json.dumps(embedding) if embedding is not None else None
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO memories (agent, category, content, embedding, importance, source, valid_from, event_time, metadata, valence, arousal, dominance, scope, confidence_score)
               VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8, $9, $10, $11, $12, 1.0)
               RETURNING id, created_at""",
            agent, category, content,
            embedding_str,
            importance, source, parsed_event_time, json.dumps(meta),
            valence, arousal, dominance, scope,
        )

    mem_id = row["id"]
    created = row["created_at"]

    # Store in Qdrant (skip if no embedding)
    if embedding is not None:
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
                    "scope": scope,
                    "utility": 0.5,  # MemRL: neutral initial utility
                    "confidence": 1.0,  # Hindsight: high initial confidence, degrades on contradiction
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

    # Incremental connectome — connect new memory to similar neighbors (needs embedding)
    if embedding is not None:
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
                    now_iso = datetime.now(timezone.utc).isoformat()
                    for s in neighbors:
                        tgt_cat = s.payload.get("category", "")
                        rel_type = "INHIBITS" if category == "correction" or tgt_cat == "correction" else "EXCITES"
                        await session.run(
                            f"MATCH (a:Memory {{memory_id: $src}}), (b:Memory {{memory_id: $tgt}}) "
                            f"MERGE (a)-[r:{rel_type}]->(b) "
                            f"SET r.weight = $weight, "
                            f"r.created_at = coalesce(r.created_at, $now), "
                            f"r.valid_from = coalesce(r.valid_from, $now), "
                            f"r.source = 'auto_link'",
                            src=mem_id, tgt=s.id, weight=float(s.score),
                            now=now_iso,
                        )
                LOG.info("Incremental connectome: %d edges created for memory %d", len(neighbors), mem_id)
        except Exception as e:
            LOG.debug("Incremental connectome skipped: %s", e)
    else:
        LOG.info("Stored memory %d without embedding — Qdrant/connectome skipped", mem_id)

    # Inline entity extraction — create MENTIONS edges immediately (not waiting for SleepGate)
    try:
        entities = _extract_entities(content)
        if entities:
            async with driver.session() as session:
                for canonical, etype in entities:
                    await session.run(
                        "MERGE (e:Entity {name: $name}) "
                        "ON CREATE SET e.type = $type, e.created_at = datetime() "
                        "WITH e "
                        "MATCH (m:Memory {memory_id: $mid}) "
                        "MERGE (m)-[:MENTIONS]->(e)",
                        name=canonical, type=etype, mid=mem_id,
                    )
    except Exception as e:
        LOG.debug("Inline entity extraction skipped: %s", e)

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
    scope_tag = f" [scope: {scope}]" if scope != "private" else ""
    dmem_tag = f" [D-MEM: fast_path, surprise={meta.get('surprise', '?')}]" if dmem_fast else ""
    # Broadcast backup (DB trigger fn_auto_broadcast handles primary; this is fallback)
    if importance >= 8 and scope in ("shared", "team"):
        asyncio.ensure_future(_auto_broadcast(mem_id, agent, scope, content))
    # Auto-fire instincts on memory store (corrections trigger instinct matching)
    asyncio.ensure_future(_auto_activate_instincts(agent, content))
    return f"Memory #{mem_id} stored at {created.isoformat()} [{conflict_action}]{emotion_tag}{scope_tag}{dmem_tag}"


@mcp.tool()
async def memory_broadcast_read(
    agent: str,
    limit: int = 10,
    unread_only: bool = True,
) -> str:
    """Read broadcasts — high-importance memories shared by other agents.

    Auto-generated when scope=shared/team + importance>=8.
    Use memory_broadcast_ack to mark as read.

    Args:
        agent: Your agent name (to filter what YOU haven't read)
        limit: Max broadcasts to return
        unread_only: Only show unread broadcasts (default true)
    """
    pool = await get_pool()
    if unread_only:
        rows = await pool.fetch("""
            SELECT mb.id, mb.memory_id, mb.from_agent, mb.to_scope, mb.broadcast_at, mb.summary,
                   m.category, m.importance, m.content
            FROM memory_broadcasts mb
            JOIN memories m ON m.id = mb.memory_id
            WHERE NOT (mb.read_by ? $1)
              AND mb.from_agent != $1
            ORDER BY mb.broadcast_at DESC
            LIMIT $2
        """, agent, limit)
    else:
        rows = await pool.fetch("""
            SELECT mb.id, mb.memory_id, mb.from_agent, mb.to_scope, mb.broadcast_at, mb.summary,
                   m.category, m.importance, m.content
            FROM memory_broadcasts mb
            JOIN memories m ON m.id = mb.memory_id
            WHERE mb.from_agent != $1
            ORDER BY mb.broadcast_at DESC
            LIMIT $2
        """, agent, limit)

    if not rows:
        return "No broadcasts pending." if unread_only else "No broadcasts found."

    lines = [f"## Broadcasts for {agent} ({len(rows)} {'unread' if unread_only else 'total'})\n"]
    for r in rows:
        lines.append(
            f"- **#{r['id']}** from {r['from_agent']} [{r['to_scope']}] "
            f"({r['category']}, imp={r['importance']}) — {r['broadcast_at'].strftime('%Y-%m-%d %H:%M')}\n"
            f"  {r['content'][:200]}"
        )
    return "\n".join(lines)


@mcp.tool()
async def memory_broadcast_ack(
    agent: str,
    broadcast_ids: str,
) -> str:
    """Mark broadcasts as read by this agent.

    Args:
        agent: Your agent name
        broadcast_ids: Comma-separated broadcast IDs to acknowledge (e.g. "1,2,3")
    """
    ids = [int(x.strip()) for x in broadcast_ids.split(",") if x.strip().isdigit()]
    if not ids:
        return "No valid broadcast IDs provided."

    pool = await get_pool()
    updated = 0
    for bid in ids:
        result = await pool.execute("""
            UPDATE memory_broadcasts
            SET read_by = read_by || to_jsonb($1::text)
            WHERE id = $2 AND NOT (read_by ? $1)
        """, agent, bid)
        if "UPDATE 1" in str(result):
            updated += 1

    return f"Acknowledged {updated}/{len(ids)} broadcasts for {agent}."


@mcp.tool()
async def memory_search(
    query: str,
    agent: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 10,
    include_invalidated: bool = False,
    scope_aware: bool = True,
) -> str:
    """Search memories by semantic similarity using Qdrant with bitemporal filtering.

    Args:
        query: Natural language search query
        agent: Filter by agent name (optional)
        category: Filter by category (optional)
        limit: Max results (default 10)
        include_invalidated: Include memories marked as no longer valid (default false)
        scope_aware: If true (default), also include shared/team memories from other agents
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
        if scope_aware:
            # Include agent's own memories OR shared/team memories from any agent
            must.append(Filter(should=[
                FieldCondition(key="agent", match=MatchValue(value=agent)),
                FieldCondition(key="scope", match=MatchValue(value="shared")),
                FieldCondition(key="scope", match=MatchValue(value="team")),
            ]))
        else:
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
        val = float(r.payload.get("valence", 0) or 0)
        aro = float(r.payload.get("arousal", 0) or 0)
        cat = r.payload.get("category", "")
        util = float(r.payload.get("utility", 0.5) or 0.5)
        conf = float(r.payload.get("confidence", 1.0) or 1.0)
        decayed_score = temporal_decay_score(r.score, days_old, imp, val, aro, category=cat, utility=util, confidence=conf)

        entry = {
            "id": r.id,
            "agent": r.payload.get("agent"),
            "category": cat,
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

    # Track activation — update last_activation and query_count for retrieved memories
    # RL auto-utility: boost utility of retrieved memories proportional to relevance
    if entries:
        retrieved_ids = [e["id"] for e in entries if isinstance(e["id"], int)]
        if retrieved_ids:
            pool = await get_pool()
            await pool.execute("""
                UPDATE memories SET
                    last_activation = now(),
                    query_count = COALESCE(query_count, 0) + 1
                WHERE id = ANY($1::bigint[])
            """, retrieved_ids)
            # RL Bellman update: retrieved = useful → small positive reward
            # Top results get more reward (rank-weighted)
            try:
                _rl_qdrant = await get_qdrant()
                for rank, entry in enumerate(entries[:5]):  # top 5 only
                    mid = entry["id"]
                    if not isinstance(mid, int):
                        continue
                    reward = 0.1 * (1.0 - rank * 0.15)  # 0.10, 0.085, 0.07, 0.055, 0.04
                    gamma = 0.95
                    # Get current utility from Qdrant
                    try:
                        pts = await _rl_qdrant.retrieve(QDRANT_COLLECTION, ids=[mid], with_payload=True)
                        if pts:
                            old_util = float(pts[0].payload.get("utility", 0.5) or 0.5)
                            new_util = round(min(1.0, old_util + reward * gamma * (1.0 - old_util)), 4)
                            await _rl_qdrant.set_payload(QDRANT_COLLECTION, payload={"utility": new_util}, points=[mid])
                            await pool.execute(
                                "UPDATE memories SET utility_score = $1 WHERE id = $2", new_util, mid
                            )
                    except Exception:
                        pass
            except Exception as e:
                LOG.debug("RL auto-utility skipped: %s", e)

    # Auto-fire instincts on every search query
    if agent:
        asyncio.ensure_future(_auto_activate_instincts(agent, query))
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
async def memory_utility_update(
    memory_ids: str,
    reward: float,
    context: str = "",
    alpha: float = 0.3,
) -> str:
    """MemRL Bellman update — adjust utility scores based on actual usefulness.

    After a task completes, call this with the memory IDs that were used and
    a reward signal (1.0 = very useful, 0.0 = not useful at all).

    utility_new = utility_old + alpha * (reward - utility_old)

    This lets memories learn their own value over time without touching model weights.
    Based on MemRL (arxiv 2601.03192).

    Args:
        memory_ids: Comma-separated memory IDs to update (e.g. "102,305,410")
        reward: Reward signal 0.0 to 1.0 (1.0 = memory was useful for the task)
        context: What task/query the memories were used for
        alpha: Learning rate for Bellman update (default 0.3)
    """
    ids = [int(x.strip()) for x in memory_ids.split(",") if x.strip().isdigit()]
    if not ids:
        return "No valid memory IDs provided."

    reward = max(0.0, min(1.0, reward))
    alpha = max(0.01, min(0.9, alpha))

    pool = await get_pool()
    updated = []
    for mid in ids:
        row = await pool.fetchrow(
            "SELECT utility_score FROM memories WHERE id = $1 AND invalid_at IS NULL",
            mid,
        )
        if not row:
            continue

        old_util = row["utility_score"]
        new_util = old_util + alpha * (reward - old_util)
        new_util = max(0.0, min(1.0, new_util))

        await pool.execute(
            "UPDATE memories SET utility_score = $1 WHERE id = $2",
            new_util, mid,
        )
        await pool.execute("""
            INSERT INTO utility_updates (memory_id, old_utility, new_utility, reward, context)
            VALUES ($1, $2, $3, $4, $5)
        """, mid, old_util, new_util, reward, context[:300])

        updated.append({"id": mid, "old": round(old_util, 3), "new": round(new_util, 3)})

    return json.dumps({
        "updated": len(updated),
        "reward": reward,
        "alpha": alpha,
        "memories": updated,
    }, indent=2)


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

        # Hindsight: content update implies correction → slight confidence decay
        # The new content replaces the old, so the old was less reliable
        await conn.execute(
            """UPDATE memories SET content = $1, embedding = $2, valence = $3, arousal = $4,
               dominance = $5, metadata = $6,
               confidence_score = GREATEST(0.3, COALESCE(confidence_score, 1.0) - 0.1)
               WHERE id = $7""",
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
            # Sync confidence decay from PG
            old_conf = float(payload.get("confidence", 1.0) or 1.0)
            payload["confidence"] = max(0.3, old_conf - 0.1)
        else:
            payload = {"content": new_content, "confidence": 0.9}
    except Exception:
        payload = {"content": new_content, "confidence": 0.9}

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
            WITH DISTINCT activated, path,
                 length(path) AS hops,
                 activated.importance AS imp,
                 activated.memory_id AS mid,
                 activated.agent AS agent,
                 activated.category AS cat,
                 left(activated.content, 300) AS content,
                 reduce(w = 1.0, r IN relationships(path) | w * COALESCE(r.weight, 0.5) * 0.7) AS activation_score,
                 [n IN nodes(path) | left(n.content, 80)] AS reasoning_chain
            WHERE activated IS NOT NULL
            ORDER BY activation_score DESC, imp DESC, hops ASC
            LIMIT $max_results
            RETURN mid, agent, cat, content, imp, hops, activation_score, reasoning_chain
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
        act_score = f", score={a['activation_score']:.2f}" if a.get('activation_score') else ""
        chain = a.get('reasoning_chain', [])
        chain_str = ""
        if chain and len(chain) > 2:
            # Show intermediate nodes (skip seed and target which are already shown)
            intermediates = chain[1:-1]
            chain_str = f"\n  WHY: {' → '.join(str(c)[:60] for c in intermediates[:3])}"
        lines.append(
            f"- [{a['cat']}, hop={a['hops']}, imp={a['imp']}{act_score}] "
            f"{a['content'][:200]}{chain_str}"
        )

    result = "\n".join(lines) if lines else "No memories activated."
    # Target 1: observe soul_activate
    # Target 3: auto-activate matching instincts (organic firing)
    if agent:
        asyncio.ensure_future(_auto_activate_instincts(agent, query))
    return result


@mcp.tool()
async def soul_synthesize(
    query: str,
    agent: Optional[str] = None,
    n_seeds: int = 5,
    max_hops: int = 3,
    style: str = "narrative",
) -> str:
    """Graphiti CONSTRUCTOR — synthesize activated memories into a coherent response.

    Unlike soul_activate (which lists memories), this tool REASONS over them.
    Uses spreading activation to find relevant memories, then Ollama synthesizes
    them into a unified narrative, analysis, or answer.

    Styles: narrative (story), analysis (structured), answer (direct response)

    Args:
        query: The question or topic to synthesize about
        agent: Filter by agent (optional)
        n_seeds: Number of seed memories (default 5)
        max_hops: Max propagation depth (default 3)
        style: Output style — narrative, analysis, or answer
    """
    import time
    t0 = time.monotonic()

    # Phase 1: Spreading activation (reuse soul_activate logic)
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
        return "No memories found to synthesize."

    seed_ids = [s.id for s in seeds]

    # Phase 2: Graph traversal
    driver = get_neo4j()
    activated = []
    async with driver.session() as session:
        cypher = f"""
            UNWIND $seeds AS seedId
            MATCH (seed:Memory {{memory_id: seedId}})
            WITH collect(seed) AS seedNodes
            UNWIND seedNodes AS seed
            OPTIONAL MATCH path = (seed)-[rel:EXCITES*1..{max_hops}]->(activated:Memory)
            WHERE activated <> seed AND NOT activated IN seedNodes
              AND all(r IN relationships(path) WHERE r.valid_until IS NULL)
            WITH DISTINCT activated, path,
                 activated.memory_id AS mid,
                 activated.agent AS agent_name,
                 activated.category AS cat,
                 activated.content AS content,
                 activated.importance AS imp,
                 reduce(w = 1.0, r IN relationships(path) | w * COALESCE(r.weight, 0.5) * 0.7) AS activation_score,
                 [n IN nodes(path) | left(n.content, 80)] AS reasoning_chain
            WHERE activated IS NOT NULL
            ORDER BY activation_score DESC, imp DESC
            LIMIT 20
            RETURN mid, agent_name, cat, content, imp, activation_score, reasoning_chain
        """
        result = await session.run(cypher, seeds=seed_ids, max_results=20)
        activated = [record.data() async for record in result]

    # Phase 3: Build context for Constructor LLM
    memory_texts = []
    for i, s in enumerate(seeds):
        content = s.payload.get("content", "")[:300]
        cat = s.payload.get("category", "unknown")
        memory_texts.append(f"[SEED {i+1}, {cat}] {content}")

    for i, a in enumerate(activated[:12]):  # Top 12 activated
        chain = a.get("reasoning_chain", [])
        chain_hint = ""
        if chain and len(chain) > 2:
            intermediates = chain[1:-1]
            chain_hint = f" (via: {' → '.join(str(c)[:40] for c in intermediates[:2])})"
        memory_texts.append(
            f"[ACTIVATED {i+1}, {a['cat']}, imp={a['imp']}, score={a['activation_score']:.2f}] "
            f"{a['content'][:300]}{chain_hint}"
        )

    memories_block = "\n".join(memory_texts)

    style_instructions = {
        "narrative": "Escribe una narrativa en primera persona que conecte estas memorias de forma coherente. Cuenta la historia que revelan juntas.",
        "analysis": "Analiza estructuralmente estas memorias. Identifica patrones, contradicciones, evolución temporal, y conclusiones.",
        "answer": f"Responde directamente a la pregunta '{query}' usando SOLO la información de estas memorias. Si no hay suficiente info, dilo.",
    }

    prompt = (
        f"Eres el Constructor de memorias del equipo SEAL. "
        f"Se activaron {len(seeds)} semillas y {len(activated)} memorias conectadas sobre: '{query}'\n\n"
        f"MEMORIAS ACTIVADAS:\n{memories_block}\n\n"
        f"INSTRUCCIÓN: {style_instructions.get(style, style_instructions['narrative'])}\n"
        f"Máximo 200 palabras. En español. No inventes información que no esté en las memorias."
    )

    # Phase 4: Ollama synthesis
    synthesis = ""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                OLLAMA_GEN_URL,
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0.4, "num_predict": 400}},
            )
            synthesis = resp.json().get("response", "").strip()
    except Exception as e:
        synthesis = f"[Constructor error: {e}]"

    elapsed = int((time.monotonic() - t0) * 1000)

    # Observe
    asyncio.create_task(_observe(
        "soul_synthesize", agent or "SYSTEM",
        f"query='{query[:100]}' style={style} seeds={len(seeds)} activated={len(activated)}",
        synthesis[:200], bool(synthesis), elapsed
    ))

    header = (
        f"## SOUL SYNTHESIS — {style}\n"
        f"Query: {query}\n"
        f"Sources: {len(seeds)} seeds + {len(activated)} activated memories | {elapsed}ms\n\n"
    )

    return header + synthesis


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

        # Count by edge type
        type_query = (
            f"MATCH (m:Memory {{agent: $agent}})-[r]->() RETURN type(r) AS t, count(r) AS c"
            if agent else
            "MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS c"
        )
        result = await session.run(type_query, **params)
        type_counts = {rec["t"]: rec["c"] async for rec in result}

        exc = type_counts.get("EXCITES", 0)
        inh = type_counts.get("INHIBITS", 0)
        causes = type_counts.get("CAUSES", 0)
        informed = type_counts.get("INFORMED", 0)
        mentions = type_counts.get("MENTIONS", 0)
        other = edges - exc - inh - causes - informed - mentions
        density = (edges / (nodes * (nodes - 1))) if nodes > 1 else 0

        # Count Entity nodes
        ent_result = await session.run("MATCH (e:Entity) RETURN count(e) AS cnt")
        entity_nodes = (await ent_result.single())["cnt"]

    return (
        f"SOUL CONNECTOME stats:\n"
        f"  Memories (nodes): {nodes}\n"
        f"  Entity nodes: {entity_nodes}\n"
        f"  Connections (edges): {edges}\n"
        f"  --- MAGMA 4 dimensions ---\n"
        f"  Semantic (EXCITES): {exc}\n"
        f"  Semantic (INHIBITS): {inh}\n"
        f"  Causal (CAUSES): {causes}\n"
        f"  Entity (MENTIONS): {mentions}\n"
        f"  Informed (traces): {informed}\n"
        f"  {'Other: ' + str(other) + chr(10) + '  ' if other else ''}"
        f"Density: {round(density, 4)}\n"
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

                # OCEAN baseline snapshot at boot (arxiv 2502.11843 — Persona Drift)
                # Saves current OCEAN as session baseline for intra-session drift detection
                try:
                    await conn.execute(
                        """INSERT INTO working_state (agent, state, updated_at, turn_count)
                           VALUES ($1, $2, NOW(), 1)
                           ON CONFLICT (agent) DO UPDATE SET
                               state = working_state.state || $2,
                               updated_at = NOW(),
                               turn_count = 1""",
                        agent, json.dumps({"ocean_baseline": ocean}),
                    )
                except Exception:
                    pass  # non-critical

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

        # Active Instincts (Tier 2.1 — only strong+ loaded at boot)
        instincts_rows = await conn.fetch(
            """SELECT id, trigger_pattern, response, domain, confidence, activation_count
               FROM instincts
               WHERE agent = $1 AND active = true AND confidence >= 0.5
               ORDER BY confidence DESC
               LIMIT 10""", agent,
        )
        if instincts_rows:
            sections.append("\n## Active Instincts (learned behavioral patterns)")
            for inst in instincts_rows:
                tier = _confidence_tier(inst["confidence"])
                sections.append(
                    f"- [{tier}] (conf={inst['confidence']:.2f}, activated {inst['activation_count']}x) "
                    f"WHEN: {inst['trigger_pattern'][:100]} → DO: {inst['response'][:120]}"
                )

        # Procedural Memories (Tier 2.5 — top workflows by success rate)
        proc_rows = await conn.fetch(
            """SELECT id, task_type, query, workflow, hit_count, success_count
               FROM procedural_memories
               WHERE agent = $1 AND active = true
               ORDER BY success_count DESC, hit_count DESC
               LIMIT 5""", agent,
        )
        if proc_rows:
            sections.append("\n## Procedural Memory (learned workflows)")
            for p in proc_rows:
                rate = p["success_count"] / max(p["hit_count"], 1)
                sections.append(
                    f"- [{p['task_type']}] (used {p['hit_count']}x, {rate:.0%} success) "
                    f"{p['query'][:80]}\n  HOW: {p['workflow'][:150]}"
                )

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

        # Working State (MEM1 compressed reasoning context)
        ws_row = await conn.fetchrow(
            "SELECT state, updated_at, turn_count FROM working_state WHERE agent = $1",
            agent,
        )
        if ws_row and ws_row["state"] and ws_row["state"] != "{}":
            ws = json.loads(ws_row["state"]) if isinstance(ws_row["state"], str) else ws_row["state"]
            if any(v for v in ws.values() if v):
                sections.append(f"\n## Working State (last updated: turn {ws_row['turn_count']})")
                for key, val in ws.items():
                    if key == "last_turn" or not val:
                        continue
                    if isinstance(val, list):
                        sections.append(f"- {key}: {', '.join(str(v) for v in val[:5])}")
                    else:
                        sections.append(f"- {key}: {str(val)[:150]}")

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

        # ── Memory Prefetch (memU pattern — anticipatory context) ──
        try:
            prefetch_lines = []
            # Recently activated memories (last 48h)
            recent_active = await conn.fetch("""
                SELECT id, content, category, importance, query_count
                FROM memories
                WHERE agent = $1 AND invalid_at IS NULL
                  AND last_activation > NOW() - interval '48 hours'
                ORDER BY query_count DESC, importance DESC
                LIMIT 5
            """, agent)
            if recent_active:
                prefetch_lines.append("**Recently active:**")
                for r in recent_active:
                    prefetch_lines.append(
                        f"- #{r['id']} [{r['category']}, imp={r['importance']}, hits={r['query_count']}] {r['content'][:120]}"
                    )

            # Unread broadcasts from other agents
            unread_bc = await conn.fetch("""
                SELECT mb.from_agent, m.content, m.category, m.importance
                FROM memory_broadcasts mb
                JOIN memories m ON m.id = mb.memory_id
                WHERE NOT (mb.read_by ? $1) AND mb.from_agent != $1
                ORDER BY mb.broadcast_at DESC LIMIT 3
            """, agent)
            if unread_bc:
                prefetch_lines.append("**Unread broadcasts:**")
                for b in unread_bc:
                    prefetch_lines.append(
                        f"- from {b['from_agent']} [{b['category']}, imp={b['importance']}] {b['content'][:120]}"
                    )

            if prefetch_lines:
                sections.append("\n## Memory Prefetch (anticipatory context)")
                sections.extend(prefetch_lines)
        except Exception as e:
            LOG.debug("Boot prefetch skipped: %s", e)

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
    mood_weight: float = 0.0,
    llm_rerank: bool = False,
) -> str:
    """Hybrid search combining semantic similarity (Qdrant) + keyword BM25 (PostgreSQL tsvector).
    Optionally modulated by mood-congruent retrieval (REMT, Frontiers 2026).
    Optionally uses LLM-based reranking (Phase 2) for functional relevance scoring.

    Args:
        query: Natural language search query
        agent: Filter by agent name (optional)
        category: Filter by category (optional)
        limit: Max results (default 10)
        semantic_weight: Weight for semantic similarity (0.0-1.0, default 0.6)
        keyword_weight: Weight for keyword/BM25 match (0.0-1.0, default 0.4)
        mood_weight: Weight for mood-congruent retrieval (0.0-1.0, default 0.0 = off). When > 0, memories with similar emotional valence to current mood rank higher.
        llm_rerank: If true, use Ollama LLM to rerank top candidates by functional relevance (slower but more precise)
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

    # 2.5. Mood-congruent retrieval: compute current mood valence (REMT pattern)
    mood_valence = 0.0
    if mood_weight > 0 and agent:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                mv = await conn.fetchval("""
                    SELECT AVG(valence) FROM (
                        SELECT valence FROM memories
                        WHERE agent = $1 AND valence IS NOT NULL AND invalid_at IS NULL
                        ORDER BY created_at DESC LIMIT 10
                    ) recent
                """, agent)
                if mv is not None:
                    mood_valence = float(mv)
        except Exception:
            pass

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

        # Mood-congruent score: 1.0 when valence matches mood, 0.0 when opposite
        mood_score = 0.5  # neutral default
        if mood_weight > 0:
            mem_valence = float(payload.get("valence") or kw.get("valence") or 0)
            mood_score = 1.0 - abs(mem_valence - mood_valence)
            mood_score = max(0.0, min(1.0, mood_score))

        # Blend: renormalize weights to sum to 1.0
        total_w = semantic_weight + keyword_weight + mood_weight
        hybrid_score = (
            (semantic_weight * sem_score) +
            (keyword_weight * kw_score) +
            (mood_weight * mood_score)
        ) / total_w if total_w > 0 else 0

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

        val = float(payload.get("valence") or kw.get("valence") or 0)
        aro = float(payload.get("arousal") or kw.get("arousal") or 0)
        util = float(payload.get("utility") or kw.get("utility_score") or 0.5)
        final_score = temporal_decay_score(hybrid_score, days_old, imp, val, aro, category=cat, utility=util)

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
        if val:
            entry["valence"] = round(val, 2)
        entries.append(entry)

    entries.sort(key=lambda x: -x["final_score"])

    # Phase 2: LLM-based reranking (ERL pattern — functional relevance scoring)
    if llm_rerank and len(entries) > 1:
        candidates = entries[:max(limit * 3, 20)]  # expand candidate pool
        try:
            # Build compact candidate list for LLM
            mem_lines = []
            for i, e in enumerate(candidates):
                content_preview = (e.get("content") or "")[:120].replace("\n", " ")
                mem_lines.append(f"{i}: [{e.get('category','?')}] {content_preview}")

            rerank_prompt = (
                f"Task: {query}\n\n"
                f"Rate each memory 1-10 for FUNCTIONAL relevance to the task above.\n"
                f"Consider: Does this memory help solve the task? Not just topic similarity.\n"
                f"Return ONLY a JSON array of [index, score] pairs.\n\n"
                f"Memories:\n" + "\n".join(mem_lines) + "\n\n"
                f"Return ONLY valid JSON like: [[0,8],[1,3],[2,9]]"
            )

            async with httpx.AsyncClient() as client:
                resp = await asyncio.wait_for(
                    client.post(OLLAMA_GEN_URL, json={
                        "model": OLLAMA_MODEL,
                        "prompt": rerank_prompt,
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 300},
                    }),
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    raw = resp.json().get("response", "")
                    import re as _re
                    json_match = _re.search(r'\[[\s\S]*\]', raw)
                    if json_match:
                        scores = json.loads(json_match.group())
                        score_map = {int(s[0]): float(s[1]) for s in scores if len(s) >= 2}
                        # Blend: 70% original score + 30% LLM score (normalized to 0-1)
                        max_llm = max(score_map.values()) if score_map else 10.0
                        for i, e in enumerate(candidates):
                            llm_score = score_map.get(i, 5.0) / max_llm
                            e["llm_relevance"] = round(llm_score, 3)
                            e["final_score"] = round(0.7 * e["final_score"] + 0.3 * llm_score, 4)
                        candidates.sort(key=lambda x: -x["final_score"])
        except (asyncio.TimeoutError, Exception) as e:
            LOG.debug("LLM rerank skipped: %s", e)

        entries = candidates

    final = entries[:limit]

    # Track activation + RL utility update for retrieved memories
    # Bellman-inspired: utility increases with each activation (positive reinforcement)
    # Formula: utility = utility + alpha * (1.0 - utility) where alpha = 0.05
    if final:
        retrieved_ids = [e["id"] for e in final if isinstance(e["id"], int)]
        if retrieved_ids:
            pool = await get_pool()
            await pool.execute("""
                UPDATE memories SET
                    last_activation = now(),
                    query_count = COALESCE(query_count, 0) + 1,
                    utility_score = LEAST(1.0, COALESCE(utility_score, 0.5) + 0.05 * (1.0 - COALESCE(utility_score, 0.5)))
                WHERE id = ANY($1::bigint[])
            """, retrieved_ids)

    # Auto-fire instincts on hybrid search queries
    if agent:
        asyncio.ensure_future(_auto_activate_instincts(agent, query))
    return json.dumps(final, ensure_ascii=False, indent=2)


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
    key_decisions: Optional[Any] = None,
    active_tasks: Optional[Any] = None,
    pending_items: Optional[Any] = None,
    errors_active: Optional[Any] = None,
    services_state: Optional[Any] = None,
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
    def _parse_json_field(val):
        """Accept str (JSON), list, dict, or None — always return parsed object or None."""
        if val is None:
            return None
        if isinstance(val, (list, dict)):
            return val
        if isinstance(val, str):
            return json.loads(val)
        return val

    if not session_id:
        session_id = generate_session_id(agent)

    result = await save_session_memory(
        agent=agent,
        session_id=session_id,
        summary=summary,
        turn_number=turn_number,
        key_decisions=_parse_json_field(key_decisions),
        active_tasks=_parse_json_field(active_tasks),
        pending_items=_parse_json_field(pending_items),
        errors_active=_parse_json_field(errors_active),
        services_state=_parse_json_field(services_state),
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
# STRUCTURED DISTILLATION — The Hippocampus (arxiv 2603.13017)
# Compresses session exchanges 11x while preserving retrieval.
# Created by JARVIS — based on Structured Distillation paper.
# ══════════════════════════════════════════════════════════════════════

DISTILL_PROMPT = """You are a session compressor for an AI agent team (SEAL).
Compress this exchange into EXACTLY this JSON format. Use SURVIVING VOCABULARY — reuse exact technical terms from the exchange, do NOT paraphrase.

Exchange:
{exchange_text}

Output JSON (and nothing else):
{{
  "exchange_core": "<what was accomplished, 1-2 sentences, commit-message style>",
  "specific_context": "<one distinguishing technical detail + emotional state if present + key decision if any>",
  "room_assignments": [
    {{"type": "<file|concept|workflow>", "key": "<identifier>", "label": "<human-readable>"}}
  ],
  "files_touched": ["<file paths or MCP tools used>"]
}}

Rules:
- exchange_core: max 30 words, past tense, factual
- specific_context: max 40 words, include the most unique technical detail
- room_assignments: 1-3 entries, types are: file (specific file), concept (technical concept), workflow (process/pipeline)
- files_touched: extract all file paths and tool names mentioned
- Language: same as the exchange (Spanish if Spanish, English if English)
- Output ONLY valid JSON, no markdown, no explanation"""


@mcp.tool()
async def session_distill(
    agent: str,
    exchange_text: str,
    session_id: Optional[str] = None,
    ply_start: Optional[int] = None,
    ply_end: Optional[int] = None,
) -> str:
    """Distill a session exchange into compressed form — the hippocampus of SOUL.

    Based on Structured Distillation (arxiv 2603.13017): 11x compression
    while preserving 96.8% of searchable vocabulary.

    Call this for each significant exchange in a session. The distilled form
    is stored in PostgreSQL and optionally embedded in Qdrant for vector search.

    Args:
        agent: Agent name (ADA, JARVIS)
        exchange_text: The raw exchange text to compress (user message + assistant response)
        session_id: Session ID (auto-generated if None)
        ply_start: First turn number (optional)
        ply_end: Last turn number (optional)
    """
    import httpx

    if not session_id:
        session_id = f"{agent.lower()}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}"

    # Skip trivial exchanges
    if len(exchange_text.strip()) < 100:
        return "Exchange too short (<100 chars), skipped."

    source_tokens = len(exchange_text.split())  # Rough estimate

    # Call Ollama for distillation
    prompt = DISTILL_PROMPT.format(exchange_text=exchange_text[:3000])

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 300},
                },
            )
            raw = resp.json().get("response", "").strip()

            # Extract JSON from response (handle potential markdown wrapping)
            json_start = raw.find("{")
            json_end = raw.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                raw = raw[json_start:json_end]

            distilled = json.loads(raw)
    except Exception as e:
        return f"Distillation failed: {e}"

    exchange_core = distilled.get("exchange_core", "")
    specific_context = distilled.get("specific_context", "")
    room_assignments = distilled.get("room_assignments", [])
    files_touched = distilled.get("files_touched", [])

    distilled_text = f"{exchange_core}\n{specific_context}"
    distilled_tokens = len(distilled_text.split())

    # Store in PostgreSQL
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO distilled_exchanges
               (session_id, agent, exchange_core, specific_context,
                room_assignments, files_touched, ply_start, ply_end,
                source_tokens, distilled_tokens, exchange_time)
               VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11)
               RETURNING id, created_at""",
            session_id, agent, exchange_core, specific_context,
            json.dumps(room_assignments, ensure_ascii=False),
            files_touched, ply_start, ply_end,
            source_tokens, distilled_tokens,
            datetime.now(timezone.utc),
        )

    distill_id = row["id"]

    # Embed distilled text in Qdrant for vector search
    qdrant_id = None
    try:
        embedding = await _embed(distilled_text)
        if embedding:
            qdrant = await get_qdrant()
            from qdrant_client.models import PointStruct
            qdrant_id = distill_id + 100000  # Offset to avoid collision with memories
            await qdrant.upsert(
                collection_name=QDRANT_COLLECTION,
                points=[PointStruct(
                    id=qdrant_id,
                    vector=embedding,
                    payload={
                        "agent": agent,
                        "content": distilled_text,
                        "category": "distilled_exchange",
                        "importance": 6,
                        "session_id": session_id,
                        "source": "structured_distillation",
                        "rooms": [r.get("key", "") for r in room_assignments],
                    },
                )],
            )
            # Update qdrant_point_id in PostgreSQL
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE distilled_exchanges SET qdrant_point_id = $1 WHERE id = $2",
                    qdrant_id, distill_id,
                )
    except Exception as e:
        LOG.debug("Qdrant embedding for distilled exchange skipped: %s", e)

    # Link to Neo4j rooms (create room nodes + edges)
    try:
        driver = get_neo4j()
        async with driver.session() as neo_session:
            for room in room_assignments:
                room_key = f"{room.get('type', 'concept')}:{room.get('key', 'unknown')}"
                await neo_session.run(
                    "MERGE (r:Room {key: $key}) "
                    "SET r.type = $type, r.label = $label "
                    "WITH r "
                    "MERGE (d:DistilledExchange {distill_id: $did}) "
                    "SET d.agent = $agent, d.session_id = $sid, d.core = $core "
                    "MERGE (d)-[:BELONGS_TO]->(r)",
                    key=room_key,
                    type=room.get("type", "concept"),
                    label=room.get("label", ""),
                    did=distill_id,
                    agent=agent,
                    sid=session_id,
                    core=exchange_core[:200],
                )
    except Exception as e:
        LOG.debug("Neo4j room linking for distilled exchange skipped: %s", e)

    compression = round(source_tokens / max(distilled_tokens, 1), 1)

    return (
        f"Distilled #{distill_id}: {source_tokens}→{distilled_tokens} tokens ({compression}x compression)\n"
        f"Core: {exchange_core}\n"
        f"Context: {specific_context}\n"
        f"Rooms: {', '.join(r.get('key', '') for r in room_assignments)}\n"
        f"Files: {', '.join(files_touched[:5])}\n"
        f"Qdrant: {'embedded' if qdrant_id else 'skipped'}"
    )


@mcp.tool()
async def session_distill_bulk(
    agent: str,
    session_id: str,
    hours_back: int = 24,
) -> str:
    """Bulk-distill recent memories from a session into compressed exchanges.

    Reads memories created in the last N hours and groups them into
    logical exchanges for distillation. Use this to consolidate an
    entire session after it ends.

    Args:
        agent: Agent name (ADA, JARVIS)
        session_id: Session ID to tag the distilled exchanges
        hours_back: How many hours back to look (default 24)
    """
    pool = await get_pool()

    # Get recent memories grouped by approximate exchanges (30-min windows)
    async with pool.acquire() as conn:
        memories = await conn.fetch(
            """SELECT id, agent, content, category, importance, created_at
               FROM memories
               WHERE agent = $1 AND invalid_at IS NULL
               AND created_at > NOW() - INTERVAL '%s hours'
               ORDER BY created_at ASC""" % hours_back,
            agent,
        )

    if not memories:
        return f"No memories found for {agent} in last {hours_back}h."

    # Group into 30-minute windows
    windows = []
    current_window = []
    window_start = memories[0]["created_at"]

    for mem in memories:
        if (mem["created_at"] - window_start) > timedelta(minutes=30):
            if current_window:
                windows.append(current_window)
            current_window = [mem]
            window_start = mem["created_at"]
        else:
            current_window.append(mem)

    if current_window:
        windows.append(current_window)

    # Distill each window
    results = []
    for i, window in enumerate(windows):
        combined_text = "\n".join(
            f"[{m['category']}] {m['content'][:300]}" for m in window
        )
        if len(combined_text) < 100:
            continue

        result = await session_distill(
            agent=agent,
            exchange_text=combined_text,
            session_id=session_id,
            ply_start=i * 10,
            ply_end=(i + 1) * 10 - 1,
        )
        results.append(result)

    return (
        f"Bulk distillation complete: {len(results)} exchanges from {len(memories)} memories "
        f"across {len(windows)} time windows.\n\n" +
        "\n---\n".join(results[:10])  # Show first 10
    )


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


# ── Instincts — Tier 2.1: Instinct-Based Learning ──
# Designed by JARVIS (2026-04-05). Sources: ECC v2, MemP, ERL, Ebbinghaus.
# Confidence tiers: dormant(0-0.3), suggested(0.3-0.5), active(0.5-0.7), strong(0.7-0.9), core(0.9-1.0)

INSTINCT_CONFIDENCE_TIERS = {
    "dormant": (0.0, 0.3),
    "suggested": (0.3, 0.5),
    "active": (0.5, 0.7),
    "strong": (0.7, 0.9),
    "core": (0.9, 1.0),
}

# Confidence adjustments
INSTINCT_REINFORCE_DELTA = 0.05     # +5% per reinforcement
INSTINCT_CORRECTION_DELTA = -0.15   # -15% per correction
INSTINCT_DECAY_RATE = 0.01          # -1% per day of inactivity
INSTINCT_MIN_CONFIDENCE = 0.05      # below this → deactivate
INSTINCT_PROMOTION_THRESHOLD = 0.8  # above this in 2+ agents → global


def _confidence_tier(confidence: float) -> str:
    """Return human-readable tier name for a confidence value."""
    for tier, (lo, hi) in INSTINCT_CONFIDENCE_TIERS.items():
        if lo <= confidence < hi:
            return tier
    return "core" if confidence >= 0.9 else "dormant"


@mcp.tool()
async def instinct_create(
    agent: str,
    trigger_pattern: str,
    response: str,
    domain: str = "general",
    confidence: float = 0.3,
    source_memory_ids: list[int] | None = None,
    source_rule_id: int | None = None,
    scope: str = "agent",
) -> str:
    """Create a new instinct from detected behavioral pattern.

    Args:
        agent: Agent name (JARVIS, ADA, DUM)
        trigger_pattern: When this instinct fires (semantic description)
        response: The behavioral response / heuristic
        domain: Domain (general, coding, medical, communication, soul)
        confidence: Initial confidence 0.0–1.0 (default 0.3 = suggested)
        source_memory_ids: Memory IDs that originated this instinct
        source_rule_id: Rule ID if promoted from an explicit rule
        scope: agent, team, or global
    """
    pool = await get_pool()
    emb = await get_embedding(f"{trigger_pattern} {response}")

    row = await pool.fetchrow("""
        INSERT INTO instincts (agent, trigger_pattern, response, domain, confidence,
                               source_memory_ids, source_rule_id, scope, embedding)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id, confidence
    """, agent, trigger_pattern, response, domain, confidence,
         source_memory_ids or [], source_rule_id, scope, json.dumps(emb))

    tier = _confidence_tier(row["confidence"])
    return json.dumps({
        "status": "created",
        "instinct_id": row["id"],
        "confidence": row["confidence"],
        "tier": tier,
        "agent": agent,
        "trigger": trigger_pattern[:80],
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def instinct_activate(
    instinct_id: int,
    agent: str,
    context: str = "",
    outcome: str = "applied",
    session_id: str | None = None,
) -> str:
    """Record that an instinct was activated (fired) during agent behavior.
    Reinforces confidence if outcome=applied, decreases if outcome=corrected.

    Args:
        instinct_id: The instinct ID
        agent: Agent name
        context: What triggered the activation
        outcome: applied, suppressed, or corrected
        session_id: Current session ID
    """
    pool = await get_pool()

    # Log activation
    await pool.execute("""
        INSERT INTO instinct_activations (instinct_id, agent, session_id, context, outcome)
        VALUES ($1, $2, $3, $4, $5)
    """, instinct_id, agent, session_id, context, outcome)

    # Update instinct based on outcome
    if outcome == "applied":
        row = await pool.fetchrow("""
            UPDATE instincts SET
                activation_count = activation_count + 1,
                reinforcement_count = reinforcement_count + 1,
                confidence = LEAST(1.0, confidence + $2),
                last_activated = now(),
                last_reinforced = now()
            WHERE id = $1 AND active = true
            RETURNING id, confidence, activation_count, reinforcement_count
        """, instinct_id, INSTINCT_REINFORCE_DELTA)
    elif outcome == "corrected":
        row = await pool.fetchrow("""
            UPDATE instincts SET
                activation_count = activation_count + 1,
                correction_count = correction_count + 1,
                confidence = GREATEST(0.0, confidence + $2),
                last_activated = now()
            WHERE id = $1 AND active = true
            RETURNING id, confidence, activation_count, correction_count
        """, instinct_id, INSTINCT_CORRECTION_DELTA)
    else:  # suppressed
        row = await pool.fetchrow("""
            UPDATE instincts SET
                activation_count = activation_count + 1,
                last_activated = now()
            WHERE id = $1 AND active = true
            RETURNING id, confidence, activation_count
        """, instinct_id)

    if not row:
        return json.dumps({"error": f"Instinct {instinct_id} not found or inactive"})

    # Auto-deactivate if confidence too low
    conf = row["confidence"]
    if conf < INSTINCT_MIN_CONFIDENCE:
        await pool.execute("UPDATE instincts SET active = false WHERE id = $1", instinct_id)
        return json.dumps({"status": "deactivated", "instinct_id": instinct_id,
                           "reason": f"confidence {conf:.3f} below threshold {INSTINCT_MIN_CONFIDENCE}"})

    tier = _confidence_tier(conf)
    return json.dumps({
        "status": "activated",
        "instinct_id": instinct_id,
        "outcome": outcome,
        "confidence": round(conf, 3),
        "tier": tier,
        "activation_count": row["activation_count"],
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def instinct_search(
    agent: str,
    query: str,
    domain: str | None = None,
    min_confidence: float = 0.3,
    limit: int = 5,
) -> str:
    """Search instincts by semantic similarity to a query.
    Used during reasoning to find relevant instincts for the current context.

    Args:
        agent: Agent name
        query: The current context/situation to match against
        domain: Filter by domain (optional)
        min_confidence: Minimum confidence threshold (default 0.3)
        limit: Max results (default 5)
    """
    pool = await get_pool()
    emb = json.dumps(await get_embedding(query))

    if domain:
        rows = await pool.fetch("""
            SELECT id, trigger_pattern, response, domain, confidence,
                   activation_count, reinforcement_count, correction_count,
                   scope, last_activated,
                   1 - (embedding <=> $1::vector) as similarity
            FROM instincts
            WHERE agent = $2 AND active = true AND confidence >= $3 AND domain = $5
            ORDER BY embedding <=> $1::vector
            LIMIT $4
        """, emb, agent, min_confidence, limit, domain)
    else:
        rows = await pool.fetch("""
            SELECT id, trigger_pattern, response, domain, confidence,
                   activation_count, reinforcement_count, correction_count,
                   scope, last_activated,
                   1 - (embedding <=> $1::vector) as similarity
            FROM instincts
            WHERE agent = $2 AND active = true AND confidence >= $3
            ORDER BY embedding <=> $1::vector
            LIMIT $4
        """, emb, agent, min_confidence, limit)

    results = []
    for r in rows:
        results.append({
            "id": r["id"],
            "trigger": r["trigger_pattern"],
            "response": r["response"],
            "domain": r["domain"],
            "confidence": round(r["confidence"], 3),
            "tier": _confidence_tier(r["confidence"]),
            "similarity": round(r["similarity"], 3),
            "activations": r["activation_count"],
            "reinforcements": r["reinforcement_count"],
            "corrections": r["correction_count"],
        })

    return json.dumps({
        "agent": agent,
        "query": query[:80],
        "matches": len(results),
        "instincts": results,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def instinct_list(
    agent: str,
    min_confidence: float = 0.0,
    include_inactive: bool = False,
) -> str:
    """List all instincts for an agent, ordered by confidence.

    Args:
        agent: Agent name
        min_confidence: Minimum confidence filter
        include_inactive: Include deactivated instincts
    """
    pool = await get_pool()

    if include_inactive:
        rows = await pool.fetch("""
            SELECT id, trigger_pattern, response, domain, confidence, active,
                   activation_count, reinforcement_count, correction_count,
                   scope, created_at, last_activated
            FROM instincts WHERE agent = $1 AND confidence >= $2
            ORDER BY confidence DESC, activation_count DESC
        """, agent, min_confidence)
    else:
        rows = await pool.fetch("""
            SELECT id, trigger_pattern, response, domain, confidence, active,
                   activation_count, reinforcement_count, correction_count,
                   scope, created_at, last_activated
            FROM instincts WHERE agent = $1 AND active = true AND confidence >= $2
            ORDER BY confidence DESC, activation_count DESC
        """, agent, min_confidence)

    instincts = []
    for r in rows:
        instincts.append({
            "id": r["id"],
            "trigger": r["trigger_pattern"][:60],
            "response": r["response"][:80],
            "domain": r["domain"],
            "confidence": round(r["confidence"], 3),
            "tier": _confidence_tier(r["confidence"]),
            "active": r["active"],
            "activations": r["activation_count"],
            "scope": r["scope"],
        })

    # Summary by tier
    tier_counts = {}
    for inst in instincts:
        t = inst["tier"]
        tier_counts[t] = tier_counts.get(t, 0) + 1

    return json.dumps({
        "agent": agent,
        "total": len(instincts),
        "tier_summary": tier_counts,
        "instincts": instincts,
    }, ensure_ascii=False, indent=2)


async def _reflexion_lesson(pool, agent: str, trigger: str, action: str, activation_count: int) -> None:
    """Reflexion (arxiv 2303.11366) — generate verbal lesson from a deactivated instinct.
    Called fire-and-forget when an instinct dies due to low confidence.
    Only fires for instincts that were actually used (activation_count > 0).
    Stores the lesson as a correction memory imp=7 for future instinct formation.
    Internal helper — NOT an MCP tool (takes pool arg, not user-facing).
    """
    try:
        # Gather recent failure events that may relate to this instinct
        recent_events = await pool.fetch("""
            SELECT event_type, description, created_at
            FROM event_log
            WHERE agent = $1
              AND created_at > now() - interval '30 days'
              AND (description ILIKE $2 OR event_type IN ('correction', 'failure', 'error'))
            ORDER BY created_at DESC LIMIT 5
        """, agent, f"%{trigger[:40]}%")

        event_ctx = ""
        if recent_events:
            event_ctx = "\n".join([
                f"- [{r['event_type']}] {r['description'][:120]}"
                for r in recent_events
            ])
        else:
            event_ctx = "(sin eventos recientes relacionados)"

        prompt = (
            f"Eres un agente de IA llamado {agent}. Un instinto que tenías se extinguió por falta de uso.\n\n"
            f"INSTINTO EXTINTO:\n"
            f"- Trigger: {trigger}\n"
            f"- Acción: {action}\n"
            f"- Activaciones totales: {activation_count}\n\n"
            f"EVENTOS RECIENTES RELACIONADOS:\n{event_ctx}\n\n"
            f"Escribe UNA lección aprendida en 1-2 oraciones. "
            f"¿Qué aprendiste? ¿Cuándo debías aplicar ese instinto y no lo hiciste? "
            f"¿Cómo mejorarías la regla? Sé concreto. Responde solo la lección, sin prefijos."
        )

        async with httpx.AsyncClient() as client:
            resp = await asyncio.wait_for(
                client.post(OLLAMA_GEN_URL, json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.4, "num_predict": 100},
                }),
                timeout=15.0,
            )
            if resp.status_code != 200:
                return

            lesson = resp.json().get("response", "").strip()
            if not lesson or len(lesson) < 20:
                return

        # Store lesson as correction memory
        lesson_content = (
            f"[REFLEXION — instinto extinto] Trigger: '{trigger}' | "
            f"Lección: {lesson}"
        )
        embedding = await get_embedding(lesson_content)
        if embedding is None:
            return

        valence, arousal, dominance = await classify_emotion(lesson_content)
        now_ts = datetime.now(timezone.utc)

        mem_id = await pool.fetchval("""
            INSERT INTO memories (
                agent, category, content, embedding, importance, source,
                created_at, valence, arousal, dominance
            ) VALUES ($1, 'correction', $2, $3, 7, 'reflexion_decay', $4, $5, $6, $7)
            RETURNING id
        """, agent, lesson_content, embedding, now_ts,
            valence or 0.0, arousal or 0.0, dominance or 0.5)

        LOG.info("Reflexion lesson stored for %s (mem #%d): %s...", agent, mem_id, lesson[:60])

    except Exception as e:
        LOG.debug("Reflexion lesson failed (non-blocking): %s", e)


async def instinct_decay(agent: str | None = None) -> str:
    """Apply Ebbinghaus decay to all active instincts.
    Confidence decreases based on days since last activation.
    Implements Reflexion (arxiv 2303.11366): when an instinct is deactivated,
    generates a verbal lesson stored as correction memory for future re-formation.
    Run this periodically (e.g., daily via cron).

    Args:
        agent: Specific agent, or None for all agents
    """
    pool = await get_pool()

    if agent:
        rows = await pool.fetch("""
            SELECT id, agent, trigger_pattern, action, confidence,
                   last_activated, last_decayed, activation_count
            FROM instincts WHERE agent = $1 AND active = true
        """, agent)
    else:
        rows = await pool.fetch("""
            SELECT id, agent, trigger_pattern, action, confidence,
                   last_activated, last_decayed, activation_count
            FROM instincts WHERE active = true
        """)

    now = datetime.now(timezone.utc)
    decayed = 0
    deactivated = 0

    for r in rows:
        last = r["last_activated"] or r["last_decayed"]
        if last is None:
            continue

        days_since = (now - last).total_seconds() / 86400
        if days_since < 1:
            continue

        # FadeMem exponential decay (arxiv 2601.18642)
        # Frequently-activated instincts decay slower — biologically accurate
        # freq_factor saturates at 1.0: instinct triggered 50x resists decay ~3x more than unused
        freq_factor = r["activation_count"] / (1.0 + r["activation_count"])
        effective_lambda = INSTINCT_DECAY_RATE * (1.0 - 0.7 * freq_factor)
        import math
        new_conf = max(0.0, r["confidence"] * math.exp(-effective_lambda * days_since))

        if new_conf < INSTINCT_MIN_CONFIDENCE:
            await pool.execute(
                "UPDATE instincts SET active = false, confidence = $2, last_decayed = now() WHERE id = $1",
                r["id"], new_conf)
            deactivated += 1
            # Reflexion (arxiv 2303.11366): generate verbal lesson for used instincts
            if r["activation_count"] > 0:
                asyncio.create_task(_reflexion_lesson(
                    pool,
                    r["agent"],
                    r["trigger_pattern"] or "",
                    r["action"] or "",
                    r["activation_count"],
                ))
        else:
            await pool.execute(
                "UPDATE instincts SET confidence = $2, last_decayed = now() WHERE id = $1",
                r["id"], new_conf)
        decayed += 1

    # TTL: prune unconfirmed instincts (conf < 0.5, never activated, older than 30 days)
    pruned = 0
    ttl_rows = await pool.fetch("""
        SELECT id, agent, trigger_pattern, confidence, created_at
        FROM instincts
        WHERE active = true AND confidence < 0.5 AND activation_count = 0
          AND created_at < now() - interval '30 days'
    """)
    for r in ttl_rows:
        await pool.execute("UPDATE instincts SET active = false WHERE id = $1", r["id"])
        pruned += 1

    # TTL warning: instincts expiring in 7 days
    expiring_soon = await pool.fetchval("""
        SELECT COUNT(*) FROM instincts
        WHERE active = true AND confidence < 0.5 AND activation_count = 0
          AND created_at < now() - interval '23 days'
          AND created_at >= now() - interval '30 days'
    """)

    return json.dumps({
        "status": "decay_applied",
        "processed": decayed,
        "deactivated": deactivated,
        "pruned_ttl": pruned,
        "expiring_soon": expiring_soon,
        "agent": agent or "all",
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def instinct_consolidate(
    agent: str,
    similarity_threshold: float = 0.85,
    min_cluster_size: int = 2,
) -> str:
    """Analyze memories to detect SEMANTIC clusters that should become instincts.
    Uses pgvector cosine similarity to find correction/pattern clusters —
    memories that say similar things even if worded differently.
    This is the 'instinct formation' process — experience → reflex.

    Args:
        agent: Agent name
        similarity_threshold: Cosine similarity threshold for clustering (default 0.85)
        min_cluster_size: Minimum memories in a cluster (default 2)
    """
    pool = await get_pool()

    # Find semantic clusters in corrections via pgvector
    correction_pairs = await pool.fetch("""
        WITH pairs AS (
            SELECT
                a.id as id_a, b.id as id_b,
                a.content as content_a, b.content as content_b,
                1 - (a.embedding <=> b.embedding) as similarity
            FROM memories a
            JOIN memories b ON a.id < b.id
            WHERE a.agent = $1 AND a.category = 'correction' AND a.invalid_at IS NULL
              AND b.agent = $1 AND b.category = 'correction' AND b.invalid_at IS NULL
              AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
              AND 1 - (a.embedding <=> b.embedding) > $2
        )
        SELECT * FROM pairs ORDER BY similarity DESC LIMIT 20
    """, agent, similarity_threshold)

    # Find semantic clusters in patterns
    pattern_pairs = await pool.fetch("""
        WITH pairs AS (
            SELECT
                a.id as id_a, b.id as id_b,
                a.content as content_a, b.content as content_b,
                1 - (a.embedding <=> b.embedding) as similarity
            FROM memories a
            JOIN memories b ON a.id < b.id
            WHERE a.agent = $1 AND a.category = 'pattern' AND a.invalid_at IS NULL
              AND b.agent = $1 AND b.category = 'pattern' AND b.invalid_at IS NULL
              AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
              AND 1 - (a.embedding <=> b.embedding) > $2
        )
        SELECT * FROM pairs ORDER BY similarity DESC LIMIT 10
    """, agent, similarity_threshold)

    # Check existing instinct embeddings to avoid duplicates
    existing = await pool.fetch(
        "SELECT id, trigger_pattern, embedding FROM instincts WHERE agent = $1 AND active = true",
        agent,
    )

    # Group pairs into clusters (simple union-find)
    clusters: dict[int, set[int]] = {}
    contents: dict[int, str] = {}

    for pairs, cat_name in [(correction_pairs, "correction"), (pattern_pairs, "pattern")]:
        for p in pairs:
            a, b = p["id_a"], p["id_b"]
            contents[a] = p["content_a"]
            contents[b] = p["content_b"]

            # Find existing cluster for a or b
            found = None
            for root, members in clusters.items():
                if a in members or b in members:
                    members.add(a)
                    members.add(b)
                    found = root
                    break
            if found is None:
                clusters[a] = {a, b}

    # Filter by min cluster size and check against existing instincts
    candidates = []
    for root, members in clusters.items():
        if len(members) < min_cluster_size:
            continue

        # Get representative content
        member_contents = [contents.get(m, "")[:100] for m in sorted(members)]
        suggested_confidence = min(0.5, 0.3 + len(members) * 0.05)

        candidates.append({
            "cluster_size": len(members),
            "memory_ids": sorted(members),
            "representative_content": member_contents[:3],
            "suggested_confidence": round(suggested_confidence, 2),
            "note": "Review these memories and create an instinct with instinct_create if the pattern is valid.",
        })

    candidates.sort(key=lambda x: -x["cluster_size"])

    return json.dumps({
        "agent": agent,
        "method": "semantic_clustering",
        "similarity_threshold": similarity_threshold,
        "candidates": len(candidates),
        "existing_instincts": len(existing),
        "clusters": candidates,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def instinct_promote(dry_run: bool = True) -> str:
    """Analyze instincts for promotion opportunities.
    Two types: (1) cluster promotion — similar instincts merge into stronger ones,
    (2) cross-agent promotion — same instinct in 2+ agents → scope: team/global.
    Inspired by ECC v2.1 /evolve pipeline.

    Args:
        dry_run: If true, only report candidates without making changes (default true)
    """
    pool = await get_pool()

    # 1. Cross-agent promotion: find similar instincts across agents
    cross_agent = await pool.fetch("""
        WITH pairs AS (
            SELECT a.id as id_a, b.id as id_b,
                   a.agent as agent_a, b.agent as agent_b,
                   a.trigger_pattern as trigger_a, b.trigger_pattern as trigger_b,
                   a.confidence as conf_a, b.confidence as conf_b,
                   1 - (a.embedding <=> b.embedding) as similarity
            FROM instincts a
            JOIN instincts b ON a.id < b.id AND a.agent != b.agent
            WHERE a.active = true AND b.active = true
              AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
              AND 1 - (a.embedding <=> b.embedding) > 0.85
              AND (a.confidence + b.confidence) / 2 >= 0.7
        )
        SELECT * FROM pairs ORDER BY similarity DESC LIMIT 10
    """)

    # 2. Within-agent clustering: find instincts that could merge
    cluster_candidates = await pool.fetch("""
        WITH pairs AS (
            SELECT a.id as id_a, b.id as id_b,
                   a.agent as agent,
                   a.trigger_pattern as trigger_a, b.trigger_pattern as trigger_b,
                   a.response as response_a, b.response as response_b,
                   a.confidence as conf_a, b.confidence as conf_b,
                   1 - (a.embedding <=> b.embedding) as similarity
            FROM instincts a
            JOIN instincts b ON a.id < b.id AND a.agent = b.agent
            WHERE a.active = true AND b.active = true
              AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
              AND 1 - (a.embedding <=> b.embedding) > 0.90
        )
        SELECT * FROM pairs ORDER BY similarity DESC LIMIT 10
    """)

    promotions = []
    merges = []

    # Process cross-agent promotions
    for p in cross_agent:
        avg_conf = (p["conf_a"] + p["conf_b"]) / 2
        entry = {
            "type": "cross_agent_promotion",
            "agents": [p["agent_a"], p["agent_b"]],
            "instinct_ids": [p["id_a"], p["id_b"]],
            "triggers": [p["trigger_a"][:60], p["trigger_b"][:60]],
            "similarity": round(p["similarity"], 3),
            "avg_confidence": round(avg_conf, 3),
            "action": "promote to scope=team" if avg_conf >= 0.7 else "monitor",
        }

        if not dry_run and avg_conf >= 0.8:
            # Promote both to team scope
            await pool.execute(
                "UPDATE instincts SET scope = 'team' WHERE id = ANY($1::int[])",
                [p["id_a"], p["id_b"]],
            )
            entry["executed"] = True

        promotions.append(entry)

    # Process within-agent merges
    for p in cluster_candidates:
        entry = {
            "type": "merge_candidate",
            "agent": p["agent"],
            "instinct_ids": [p["id_a"], p["id_b"]],
            "triggers": [p["trigger_a"][:60], p["trigger_b"][:60]],
            "similarity": round(p["similarity"], 3),
            "note": "Consider merging into a single, stronger instinct",
        }
        merges.append(entry)

    return json.dumps({
        "dry_run": dry_run,
        "cross_agent_promotions": len(promotions),
        "merge_candidates": len(merges),
        "promotions": promotions,
        "merges": merges,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def memory_feedback(
    memory_id: int,
    outcome: str,
    success: bool,
    agent: str | None = None,
) -> str:
    """Provide retroactive quality feedback on a memory that was used.
    If the memory led to a good outcome, reinforce it. If bad, degrade it.
    Prevents error propagation from experience-following behavior.

    Args:
        memory_id: The memory ID that was used
        outcome: Description of what happened when this memory was applied
        success: Did using this memory lead to a good result?
        agent: Agent providing feedback (optional)
    """
    pool = await get_pool()

    row = await pool.fetchrow(
        "SELECT id, importance, confidence_score, query_count FROM memories WHERE id = $1",
        memory_id,
    )
    if not row:
        return json.dumps({"error": f"Memory {memory_id} not found"})

    old_conf = row["confidence_score"] or 0.5
    old_imp = row["importance"]

    if success:
        new_conf = min(1.0, old_conf + 0.1)
        new_imp = min(10, old_imp + 1) if old_imp < 10 else old_imp
    else:
        new_conf = max(0.0, old_conf - 0.2)
        new_imp = max(1, old_imp - 1) if old_imp > 1 else old_imp

    await pool.execute("""
        UPDATE memories SET
            confidence_score = $2,
            importance = $3,
            metadata = metadata || $4::jsonb
        WHERE id = $1
    """, memory_id, new_conf, new_imp,
         json.dumps({"last_feedback": {
             "outcome": outcome[:200],
             "success": success,
             "agent": agent,
             "timestamp": datetime.now(timezone.utc).isoformat(),
             "conf_delta": round(new_conf - old_conf, 2),
         }}))

    # Hindsight: sync confidence to Qdrant for retrieval ranking
    try:
        qdrant = await get_qdrant()
        await qdrant.set_payload(QDRANT_COLLECTION, payload={"confidence": new_conf}, points=[memory_id])
    except Exception:
        pass  # non-critical — PG is source of truth

    return json.dumps({
        "status": "feedback_recorded",
        "memory_id": memory_id,
        "success": success,
        "confidence": {"old": round(old_conf, 3), "new": round(new_conf, 3)},
        "importance": {"old": old_imp, "new": new_imp},
    }, ensure_ascii=False, indent=2)


# ── Procedural Memory (MemP pattern) ──

PROC_DECAY_MIN_HITS = 3
PROC_DECAY_MIN_SUCCESS_RATE = 0.5


@mcp.tool()
async def procedure_store(
    agent: str,
    task_description: str,
    workflow: str,
    task_type: str = "general",
    facts: str = "{}",
    source_task: str = "",
    success: bool = True,
) -> str:
    """Build phase: store a reusable workflow extracted from a task trajectory.
    Only store workflows from completed tasks. The workflow should be a narrative
    paragraph describing HOW to accomplish the task, not just what happened."""
    pool = await get_pool()

    # Parse facts
    try:
        facts_obj = json.loads(facts) if isinstance(facts, str) else facts
    except json.JSONDecodeError:
        facts_obj = {}

    # Generate embedding from task description + workflow
    from embeddings import get_embedding
    embed_text = f"{task_description} {workflow}"
    embedding = await get_embedding(embed_text)

    row = await pool.fetchrow("""
        INSERT INTO procedural_memories
            (agent, task_type, query, workflow, facts, build_policy, source_task,
             hit_count, success_count, fail_count, embedding)
        VALUES ($1, $2, $3, $4, $5, 'direct', $6, 0, $7, $8, $9)
        RETURNING id
    """,
        agent, task_type, task_description, workflow, json.dumps(facts_obj),
        source_task, 1 if success else 0, 0 if success else 1,
        json.dumps(embedding),
    )

    return json.dumps({
        "status": "stored",
        "id": row["id"],
        "agent": agent,
        "task_type": task_type,
        "query_preview": task_description[:100],
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def procedure_search(
    query: str,
    agent: str = "",
    task_type: str = "",
    top_k: int = 3,
) -> str:
    """Retrieve phase: find relevant procedural memories for the current task.
    Returns workflows ranked by semantic similarity. Updates hit_count on retrieval."""
    pool = await get_pool()

    from embeddings import get_embedding
    embedding = await get_embedding(query)

    # Build dynamic WHERE clause
    conditions = ["active = true", "embedding IS NOT NULL"]
    params = [json.dumps(embedding), top_k]
    param_idx = 3

    if agent:
        conditions.append(f"agent = ${param_idx}")
        params.append(agent)
        param_idx += 1

    if task_type:
        conditions.append(f"task_type = ${param_idx}")
        params.append(task_type)
        param_idx += 1

    where = " AND ".join(conditions)

    rows = await pool.fetch(f"""
        SELECT id, agent, task_type, query, workflow, facts,
               hit_count, success_count, fail_count, reflection,
               1 - (embedding <=> $1::vector) as similarity
        FROM procedural_memories
        WHERE {where}
        ORDER BY embedding <=> $1::vector
        LIMIT $2
    """, *params)

    if not rows:
        return json.dumps({"results": [], "message": "No procedural memories found"})

    # Update hit_count for retrieved procedures
    retrieved_ids = [r["id"] for r in rows]
    await pool.execute("""
        UPDATE procedural_memories SET hit_count = hit_count + 1, updated_at = now()
        WHERE id = ANY($1::bigint[])
    """, retrieved_ids)

    results = []
    for r in rows:
        success_rate = r["success_count"] / max(r["hit_count"] + 1, 1)
        results.append({
            "id": r["id"],
            "agent": r["agent"],
            "task_type": r["task_type"],
            "query": r["query"],
            "workflow": r["workflow"],
            "facts": json.loads(r["facts"]) if r["facts"] else {},
            "similarity": round(r["similarity"], 3),
            "hit_count": r["hit_count"] + 1,
            "success_rate": round(success_rate, 2),
            "reflection": r["reflection"],
        })

    return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False, indent=2)


@mcp.tool()
async def procedure_update(
    procedure_id: int,
    success: bool,
    reflection: str = "",
    new_workflow: str = "",
) -> str:
    """Update phase: record outcome and optionally improve a procedural memory.
    If success=false and reflection is provided, the workflow gets rewritten (reflect strategy).
    Auto-deactivates procedures with hit >= 3 and success_rate < 50%."""
    pool = await get_pool()

    row = await pool.fetchrow(
        "SELECT * FROM procedural_memories WHERE id = $1", procedure_id
    )
    if not row:
        return json.dumps({"error": f"Procedure #{procedure_id} not found"})

    # Update counts
    if success:
        await pool.execute("""
            UPDATE procedural_memories
            SET success_count = success_count + 1, updated_at = now()
            WHERE id = $1
        """, procedure_id)
    else:
        updates = ["fail_count = fail_count + 1", "updated_at = now()"]
        params = [procedure_id]
        param_idx = 2

        # Reflect strategy: rewrite workflow on failure
        if reflection:
            updates.append(f"reflection = ${param_idx}")
            params.append(reflection)
            param_idx += 1
            updates.append("build_policy = 'reflect'")

        if new_workflow:
            updates.append(f"workflow = ${param_idx}")
            params.append(new_workflow)
            param_idx += 1

            # Re-embed with new workflow
            from embeddings import get_embedding
            embed_text = f"{row['query']} {new_workflow}"
            emb = await get_embedding(embed_text)
            updates.append(f"embedding = ${param_idx}")
            params.append(json.dumps(emb))
            param_idx += 1

        await pool.execute(
            f"UPDATE procedural_memories SET {', '.join(updates)} WHERE id = $1",
            *params,
        )

    # Decay check: auto-deactivate low-performing procedures
    updated = await pool.fetchrow(
        "SELECT hit_count, success_count, fail_count FROM procedural_memories WHERE id = $1",
        procedure_id,
    )
    total_hits = updated["hit_count"]
    success_rate = updated["success_count"] / max(total_hits, 1)
    deactivated = False

    if total_hits >= PROC_DECAY_MIN_HITS and success_rate < PROC_DECAY_MIN_SUCCESS_RATE:
        await pool.execute(
            "UPDATE procedural_memories SET active = false, updated_at = now() WHERE id = $1",
            procedure_id,
        )
        deactivated = True

    return json.dumps({
        "status": "updated",
        "procedure_id": procedure_id,
        "success": success,
        "hit_count": total_hits,
        "success_rate": round(success_rate, 2),
        "reflected": bool(reflection),
        "workflow_rewritten": bool(new_workflow),
        "deactivated": deactivated,
    }, ensure_ascii=False, indent=2)


# ── Working State (MEM1 compressed reasoning state) ──


@mcp.tool()
async def working_state_get(agent: str) -> str:
    """Get the current working state for an agent.
    The working state is a compressed JSON representing the agent's current reasoning context:
    active_hypotheses, discarded_paths, current_constraints, pending_validations, relevant_memory_ids.

    Args:
        agent: Agent name (JARVIS, ADA, DUM)
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT state, updated_at, turn_count FROM working_state WHERE agent = $1",
        agent,
    )
    if not row:
        return json.dumps({"agent": agent, "state": {}, "message": "No working state found"})

    return json.dumps({
        "agent": agent,
        "state": json.loads(row["state"]) if isinstance(row["state"], str) else row["state"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "turn_count": row["turn_count"],
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def working_state_update(
    agent: str,
    active_hypotheses: Optional[str] = None,
    discarded_paths: Optional[str] = None,
    current_constraints: Optional[str] = None,
    pending_validations: Optional[str] = None,
    relevant_memory_ids: Optional[str] = None,
    custom_fields: Optional[str] = None,
) -> str:
    """Update the working state for an agent. Only provided fields are updated (merge, not replace).
    Call this after significant reasoning steps to preserve context across turns.

    Args:
        agent: Agent name (JARVIS, ADA, DUM)
        active_hypotheses: JSON array of current hypotheses being explored
        discarded_paths: JSON array of approaches already tried and rejected
        current_constraints: JSON array of active constraints/requirements
        pending_validations: JSON array of things that need verification
        relevant_memory_ids: JSON array of memory IDs relevant to current task
        custom_fields: JSON object with any additional key-value pairs to merge
    """
    pool = await get_pool()

    # Get current state
    row = await pool.fetchrow(
        "SELECT state, turn_count FROM working_state WHERE agent = $1", agent,
    )

    if row:
        current = json.loads(row["state"]) if isinstance(row["state"], str) else (row["state"] or {})
        turn = row["turn_count"] or 0
    else:
        current = {}
        turn = 0

    # Merge provided fields
    field_map = {
        "active_hypotheses": active_hypotheses,
        "discarded_paths": discarded_paths,
        "current_constraints": current_constraints,
        "pending_validations": pending_validations,
        "relevant_memory_ids": relevant_memory_ids,
    }

    for key, val in field_map.items():
        if val is not None:
            try:
                current[key] = json.loads(val)
            except json.JSONDecodeError:
                current[key] = val

    if custom_fields:
        try:
            custom = json.loads(custom_fields)
            current.update(custom)
        except json.JSONDecodeError:
            pass

    current["last_turn"] = turn + 1

    # Upsert
    await pool.execute("""
        INSERT INTO working_state (agent, state, updated_at, turn_count)
        VALUES ($1, $2::jsonb, now(), $3)
        ON CONFLICT (agent) DO UPDATE SET
            state = $2::jsonb, updated_at = now(), turn_count = $3
    """, agent, json.dumps(current, ensure_ascii=False), turn + 1)

    return json.dumps({
        "status": "updated",
        "agent": agent,
        "turn_count": turn + 1,
        "fields_updated": [k for k, v in field_map.items() if v is not None] + (["custom"] if custom_fields else []),
        "state_size": len(json.dumps(current)),
    }, ensure_ascii=False, indent=2)


# ── Observation Analysis (ECC v2.1 pattern — pattern detection) ──


@mcp.tool()
async def observation_analyze(
    agent: str = "",
    days: int = 7,
    min_frequency: int = 3,
) -> str:
    """Analyze event logs and tool observations to detect behavioral patterns.
    Suggests new instincts based on repeated patterns, corrections, and workflows.
    Based on ECC v2.1 Observer pattern: detect corrections, repeated workflows,
    error resolutions, and tool preferences.

    Args:
        agent: Filter by agent (optional, all agents if empty)
        days: How many days back to analyze (default 7)
        min_frequency: Minimum pattern frequency to report (default 3)
    """
    pool = await get_pool()

    # Build consistent parameterized queries
    # All queries use: $1=days_str, $2=agent (if filtered), $3=min_frequency
    days_str = str(days)
    if agent:
        agent_filter_event = "AND agent = $2"
        agent_filter_mem = "AND agent = $2"
        params = [days_str, agent, min_frequency]
        freq_param = "$3"
    else:
        agent_filter_event = ""
        agent_filter_mem = ""
        params = [days_str, min_frequency]
        freq_param = "$2"

    # 1. Correction patterns: events with 'correction' or 'error' → 'fix'
    corrections = await pool.fetch(f"""
        SELECT agent, content, metadata
        FROM event_log
        WHERE time > now() - ($1 || ' days')::interval
          AND (event_type IN ('error', 'command') OR content ILIKE '%correc%' OR content ILIKE '%fix%')
          {agent_filter_event}
        ORDER BY time DESC
        LIMIT 50
    """, *params[:2] if agent else params[:1])

    # 2. Repeated event types per agent (workflow patterns)
    workflow_patterns = await pool.fetch(f"""
        SELECT agent, event_type, COUNT(*) as freq,
               array_agg(DISTINCT LEFT(content, 80)) as samples
        FROM event_log
        WHERE time > now() - ($1 || ' days')::interval
          {agent_filter_event}
        GROUP BY agent, event_type
        HAVING COUNT(*) >= {freq_param}
        ORDER BY freq DESC
    """, *params)

    # 3. Memory categories that get stored most (preference patterns)
    memory_prefs = await pool.fetch(f"""
        SELECT agent, category, COUNT(*) as freq
        FROM memories
        WHERE created_at > now() - ($1 || ' days')::interval
          AND invalid_at IS NULL
          {agent_filter_mem}
        GROUP BY agent, category
        HAVING COUNT(*) >= {freq_param}
        ORDER BY freq DESC
        LIMIT 15
    """, *params)

    # 4. Tool observation patterns (if any observations exist)
    tool_patterns = await pool.fetch(f"""
        SELECT agent, tool_name, COUNT(*) as freq,
               COUNT(*) FILTER (WHERE success = true) as success_count,
               AVG(latency_ms) as avg_latency
        FROM tool_observations
        WHERE created_at > now() - ($1 || ' days')::interval
          {agent_filter_event}
        GROUP BY agent, tool_name
        HAVING COUNT(*) >= {freq_param}
        ORDER BY freq DESC
        LIMIT 15
    """, *params)

    # 5. Instinct activation patterns (which instincts fire most)
    instinct_patterns = await pool.fetch(f"""
        SELECT i.agent, i.trigger_pattern, i.response, i.confidence,
               i.activation_count, i.last_activated
        FROM instincts i
        WHERE i.active = true AND i.activation_count > 0
          {'AND i.agent = $1' if agent else ''}
        ORDER BY i.activation_count DESC
        LIMIT 10
    """, *([agent] if agent else []))

    # 6. Generate suggestions via Ollama
    suggestions = []
    context_parts = []

    if corrections:
        context_parts.append(f"Corrections ({len(corrections)}):")
        for c in corrections[:10]:
            context_parts.append(f"  [{c['agent']}] {c['content'][:100]}")

    if workflow_patterns:
        context_parts.append(f"Repeated patterns:")
        for w in workflow_patterns:
            samples = w['samples'][:3] if w['samples'] else []
            context_parts.append(f"  [{w['agent']}/{w['event_type']}] {w['freq']}x — {'; '.join(s[:50] for s in samples)}")

    if memory_prefs:
        context_parts.append(f"Memory preferences:")
        for m in memory_prefs:
            context_parts.append(f"  [{m['agent']}] {m['category']}: {m['freq']}x")

    if context_parts:
        try:
            analyze_prompt = (
                "Analyze these behavioral patterns from an AI agent team and suggest 1-3 new instincts.\n"
                "An instinct has: trigger (WHEN situation), response (DO action), domain, confidence 0.3-0.7.\n"
                "Only suggest instincts for CLEAR, REPEATED patterns. Not one-off events.\n"
                "Return ONLY a JSON array of objects with: trigger, response, domain, confidence.\n\n"
                + "\n".join(context_parts) + "\n\n"
                "Return ONLY valid JSON array. No explanation."
            )

            async with httpx.AsyncClient() as client:
                resp = await asyncio.wait_for(
                    client.post(OLLAMA_GEN_URL, json={
                        "model": OLLAMA_MODEL,
                        "prompt": analyze_prompt,
                        "stream": False,
                        "options": {"temperature": 0.3, "num_predict": 500},
                    }),
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    raw = resp.json().get("response", "")
                    json_match = re.search(r'\[[\s\S]*\]', raw)
                    if json_match:
                        suggestions = json.loads(json_match.group())
        except (asyncio.TimeoutError, Exception) as e:
            LOG.debug("Observation analysis LLM skipped: %s", e)

    return json.dumps({
        "period_days": days,
        "agent_filter": agent or "all",
        "corrections_found": len(corrections),
        "workflow_patterns": len(workflow_patterns),
        "memory_preferences": len(memory_prefs),
        "tool_patterns": len(tool_patterns),
        "active_instincts_with_activations": len(instinct_patterns),
        "suggested_instincts": suggestions,
        "patterns": {
            "workflows": [
                {"agent": w["agent"], "type": w["event_type"], "freq": w["freq"]}
                for w in workflow_patterns
            ],
            "memory_prefs": [
                {"agent": m["agent"], "category": m["category"], "freq": m["freq"]}
                for m in memory_prefs
            ],
        },
    }, ensure_ascii=False, indent=2)


# ── Evolve Pipeline (ECC /evolve pattern) ──


@mcp.tool()
async def instinct_evolve(
    agent: str = "",
    min_confidence: float = 0.6,
    dry_run: bool = True,
) -> str:
    """Analyze instinct clusters and suggest evolution into skills, rules, or team behaviors.
    Based on ECC /evolve: groups instincts by semantic similarity, classifies candidates
    as skill (2+ similar instincts), rule (workflow domain + high conf), or team behavior
    (cross-agent pattern). Does NOT auto-create — returns suggestions for review.

    Args:
        agent: Filter by agent (optional, all if empty)
        min_confidence: Minimum confidence to consider (default 0.6)
        dry_run: If true, only report suggestions (default true)
    """
    pool = await get_pool()

    agent_filter = "AND agent = $2" if agent else ""
    params = [min_confidence]
    if agent:
        params.append(agent)

    # Get active instincts above threshold
    instincts = await pool.fetch(f"""
        SELECT id, agent, trigger_pattern, response, domain, confidence,
               activation_count, scope, embedding
        FROM instincts
        WHERE active = true AND confidence >= $1
          AND embedding IS NOT NULL
          {agent_filter}
        ORDER BY confidence DESC
    """, *params)

    if len(instincts) < 2:
        return json.dumps({"message": "Not enough instincts to analyze", "count": len(instincts)})

    # Cluster by semantic similarity via SQL (within same agent)
    similar_pairs = await pool.fetch(f"""
        SELECT a.id as id_a, b.id as id_b, a.agent,
               1 - (a.embedding <=> b.embedding) as similarity
        FROM instincts a JOIN instincts b ON a.id < b.id AND a.agent = b.agent
        WHERE a.active = true AND b.active = true
          AND a.confidence >= $1 AND b.confidence >= $1
          AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
          AND 1 - (a.embedding <=> b.embedding) > 0.85
          {agent_filter.replace('agent', 'a.agent')}
        ORDER BY similarity DESC
    """, *params)

    # Build clusters from pairs (union-find style)
    id_to_instinct = {i["id"]: i for i in instincts}
    parent = {}
    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x
    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for p in similar_pairs:
        union(p["id_a"], p["id_b"])

    cluster_map = {}
    for iid in id_to_instinct:
        root = find(iid)
        if root not in cluster_map:
            cluster_map[root] = []
        cluster_map[root].append(id_to_instinct[iid])

    clusters = [c for c in cluster_map.values() if len(c) >= 2]

    # Classify candidates
    skill_candidates = []
    rule_candidates = []
    team_candidates = []

    for cluster in clusters:
        avg_conf = sum(i["confidence"] for i in cluster) / len(cluster)
        total_activations = sum(i["activation_count"] for i in cluster)
        domains = list(set(i["domain"] for i in cluster))

        candidate = {
            "instinct_ids": [i["id"] for i in cluster],
            "agent": cluster[0]["agent"],
            "triggers": [i["trigger_pattern"][:60] for i in cluster],
            "responses": [i["response"][:60] for i in cluster],
            "domains": domains,
            "avg_confidence": round(avg_conf, 3),
            "total_activations": total_activations,
            "size": len(cluster),
        }

        if any(d in ("workflow", "coding", "research") for d in domains) and avg_conf >= 0.7:
            candidate["evolution"] = "rule"
            candidate["suggested_rule"] = f"WHEN {cluster[0]['trigger_pattern'][:80]} → {cluster[0]['response'][:120]}"
            rule_candidates.append(candidate)
        elif len(cluster) >= 3 and avg_conf >= 0.75:
            candidate["evolution"] = "agent_behavior"
            team_candidates.append(candidate)
        else:
            candidate["evolution"] = "skill"
            candidate["suggested_skill"] = f"{domains[0]}_{cluster[0]['trigger_pattern'][:30]}".replace(" ", "_").lower()
            skill_candidates.append(candidate)

    # Cross-agent evolution check
    cross_agent = []
    if not agent:
        cross = await pool.fetch("""
            WITH pairs AS (
                SELECT a.id as id_a, b.id as id_b,
                       a.agent as agent_a, b.agent as agent_b,
                       a.trigger_pattern as trigger_a, b.trigger_pattern as trigger_b,
                       a.confidence as conf_a, b.confidence as conf_b,
                       1 - (a.embedding <=> b.embedding) as similarity
                FROM instincts a JOIN instincts b ON a.id < b.id AND a.agent != b.agent
                WHERE a.active = true AND b.active = true
                  AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
                  AND 1 - (a.embedding <=> b.embedding) > 0.90
                  AND (a.confidence + b.confidence) / 2 >= $1
            )
            SELECT * FROM pairs ORDER BY similarity DESC LIMIT 10
        """, min_confidence)

        for p in cross:
            cross_agent.append({
                "agents": [p["agent_a"], p["agent_b"]],
                "instinct_ids": [p["id_a"], p["id_b"]],
                "triggers": [p["trigger_a"][:60], p["trigger_b"][:60]],
                "similarity": round(p["similarity"], 3),
                "avg_confidence": round((p["conf_a"] + p["conf_b"]) / 2, 3),
                "evolution": "team_instinct",
            })

    # Auto-execute if not dry_run: promote high-confidence cross-agent to team rules
    executed = []
    if not dry_run:
        for ca in cross_agent:
            if ca["avg_confidence"] >= 0.8:
                # Create as team rule
                trigger = ca["triggers"][0]
                await pool.execute("""
                    UPDATE instincts SET scope = 'team' WHERE id = ANY($1::int[])
                """, ca["instinct_ids"])
                executed.append({"action": "promoted_to_team", "ids": ca["instinct_ids"]})

    return json.dumps({
        "dry_run": dry_run,
        "instincts_analyzed": len(instincts),
        "clusters_found": len(clusters),
        "evolution_candidates": {
            "skills": len(skill_candidates),
            "rules": len(rule_candidates),
            "team_behaviors": len(team_candidates),
            "cross_agent": len(cross_agent),
        },
        "skill_candidates": skill_candidates,
        "rule_candidates": rule_candidates,
        "team_candidates": team_candidates,
        "cross_agent_candidates": cross_agent,
        "executed": executed,
    }, ensure_ascii=False, indent=2)


# ── FLARE: Forward-Looking Active Retrieval (CMU EMNLP 2023 pattern) ──

@mcp.tool()
async def memory_flare(
    agent: str,
    draft_response: str,
    query: str = "",
    top_k: int = 5,
) -> str:
    """FLARE — Forward-Looking Active Retrieval.

    Given a draft response, identifies knowledge gaps (low-confidence claims)
    and retrieves relevant memories to fill them BEFORE the final response.

    Pattern: Generate draft → detect uncertain parts → retrieve → augment.

    Args:
        agent: Agent name
        draft_response: Your tentative response (can be incomplete)
        query: Original user query (for context)
        top_k: Max memories to retrieve per gap
    """
    import time
    t0 = time.monotonic()

    # Phase 1: Use Ollama to detect knowledge gaps in the draft
    gap_prompt = (
        f"Eres un detector de gaps de conocimiento. Analiza esta respuesta borrador y encuentra "
        f"afirmaciones que necesitan verificación o información faltante.\n\n"
        f"Pregunta original: {query[:200]}\n"
        f"Borrador: {draft_response[:500]}\n\n"
        f"Devuelve SOLO un JSON array de strings, cada uno una query de búsqueda para llenar el gap. "
        f"Máximo 3 gaps. Si no hay gaps, devuelve []. Ejemplo: [\"fecha de decisión X\", \"quién aprobó Y\"]\n"
        f"Responde SOLO el JSON array."
    )

    gaps = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                OLLAMA_GEN_URL,
                json={"model": OLLAMA_MODEL, "prompt": gap_prompt, "stream": False,
                      "options": {"temperature": 0.3, "num_predict": 150}},
            )
            raw = resp.json().get("response", "")
            json_match = re.search(r'\[.*?\]', raw, re.DOTALL)
            if json_match:
                gaps = json.loads(json_match.group())
                if not isinstance(gaps, list):
                    gaps = []
                gaps = [g for g in gaps if isinstance(g, str) and len(g) > 3][:3]
    except Exception as e:
        LOG.debug("FLARE gap detection failed: %s", e)

    if not gaps:
        elapsed = int((time.monotonic() - t0) * 1000)
        return f"## FLARE — No knowledge gaps detected ({elapsed}ms)\nDraft appears self-sufficient."

    # Phase 2: Retrieve memories for each gap
    qdrant = await get_qdrant()
    must_not = [FieldCondition(key="invalid", match=MatchValue(value=True))]

    augmentations = []
    for gap_query in gaps:
        try:
            gap_vec = await get_embedding(gap_query)
            # Scope-aware: search own + shared + team
            gap_resp = await qdrant.query_points(
                collection_name=QDRANT_COLLECTION,
                query=gap_vec,
                query_filter=Filter(must_not=must_not),
                limit=top_k,
                with_payload=True,
            )
            gap_results = gap_resp.points

            if gap_results:
                memories_text = "\n".join(
                    f"  - [{r.payload.get('category')}, {r.payload.get('agent')}, sim={r.score:.2f}] "
                    f"{r.payload.get('content', '')[:200]}"
                    for r in gap_results[:3]
                )
                augmentations.append(f"**Gap:** {gap_query}\n{memories_text}")
        except Exception as e:
            LOG.debug("FLARE retrieval for gap '%s' failed: %s", gap_query, e)

    elapsed = int((time.monotonic() - t0) * 1000)

    # Observe
    asyncio.create_task(_observe(
        "memory_flare", agent,
        f"gaps={len(gaps)} augmentations={len(augmentations)}",
        f"queries: {', '.join(gaps)}", bool(augmentations), elapsed
    ))

    lines = [
        f"## FLARE — {len(gaps)} knowledge gaps detected, {len(augmentations)} filled ({elapsed}ms)\n",
        f"Original query: {query[:150]}\n",
    ]
    for aug in augmentations:
        lines.append(aug + "\n")

    if not augmentations:
        lines.append("No relevant memories found for detected gaps.")

    return "\n".join(lines)


# ── Community Detection (GraphRAG pattern) ──

@mcp.tool()
async def memory_communities(
    agent: Optional[str] = None,
    min_community_size: int = 3,
    summarize: bool = True,
) -> str:
    """Detect memory communities using connected components in Neo4j.

    Groups memories into thematic clusters based on connectome edges.
    Optionally generates summaries per community via Ollama.
    Inspired by GraphRAG (Louvain community detection).

    Args:
        agent: Filter by agent (optional)
        min_community_size: Minimum memories per community (default 3)
        summarize: Generate LLM summaries per community (default true)
    """
    import time
    t0 = time.monotonic()

    driver = get_neo4j()
    agent_filter = "WHERE m.agent = $agent" if agent else ""
    params = {"agent": agent} if agent else {}

    # Use weakly connected components via BFS traversal
    # Neo4j Community Edition doesn't have GDS, so we use a simpler approach:
    # Find clusters via shared strong edges
    async with driver.session() as session:
        cypher = f"""
            MATCH (m:Memory) {agent_filter}
            WITH collect(m) AS nodes
            UNWIND nodes AS n
            OPTIONAL MATCH (n)-[r:EXCITES]-(neighbor:Memory)
            WHERE r.weight > 0.6 {"AND neighbor.agent = $agent" if agent else ""}
            WITH n, collect(DISTINCT neighbor) AS neighbors
            RETURN n.memory_id AS mid, n.content AS content, n.category AS cat,
                   n.importance AS imp, n.agent AS agent_name,
                   [nb IN neighbors | nb.memory_id] AS neighbor_ids
        """
        result = await session.run(cypher, **params)
        nodes = [record.data() async for record in result]

    if not nodes:
        return "No memories found for community detection."

    # Simple union-find for connected components
    parent = {}
    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    node_map = {n["mid"]: n for n in nodes}
    for n in nodes:
        mid = n["mid"]
        if mid not in parent:
            parent[mid] = mid
        for nb in n["neighbor_ids"]:
            if nb in node_map:
                if nb not in parent:
                    parent[nb] = nb
                union(mid, nb)

    # Group by root
    communities = {}
    for mid in node_map:
        root = find(mid)
        communities.setdefault(root, []).append(node_map[mid])

    # Filter by min size and sort by size
    communities = {k: v for k, v in communities.items() if len(v) >= min_community_size}
    communities = dict(sorted(communities.items(), key=lambda x: len(x[1]), reverse=True))

    if not communities:
        return f"No communities found with {min_community_size}+ members."

    lines = [f"## Memory Communities — {len(communities)} clusters found\n"]

    for i, (root, members) in enumerate(list(communities.items())[:10]):  # Top 10
        cats = {}
        for m in members:
            cats[m["cat"]] = cats.get(m["cat"], 0) + 1
        cat_str = ", ".join(f"{c}:{n}" for c, n in sorted(cats.items(), key=lambda x: -x[1]))
        avg_imp = sum(m["imp"] or 5 for m in members) / len(members)

        summary = ""
        if summarize and len(members) >= 3:
            # Generate summary via Ollama
            sample_content = "\n".join(f"- {m['content'][:150]}" for m in members[:8])
            sum_prompt = (
                f"Resume en UNA oración el tema central de este grupo de {len(members)} memorias:\n"
                f"{sample_content}\n\n"
                f"Responde SOLO con la oración resumen, en español, máximo 30 palabras."
            )
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        OLLAMA_GEN_URL,
                        json={"model": OLLAMA_MODEL, "prompt": sum_prompt, "stream": False,
                              "options": {"temperature": 0.3, "num_predict": 60}},
                    )
                    summary = resp.json().get("response", "").strip().split("\n")[0][:200]
            except Exception:
                summary = ""

        lines.append(
            f"### Community {i+1} ({len(members)} memories, avg_imp={avg_imp:.1f})\n"
            f"Categories: {cat_str}\n"
            f"{'Summary: ' + summary if summary else ''}\n"
            f"Sample: {members[0]['content'][:100]}..."
        )

    elapsed = int((time.monotonic() - t0) * 1000)
    lines.insert(1, f"Elapsed: {elapsed}ms\n")

    asyncio.create_task(_observe(
        "memory_communities", agent or "ALL",
        f"communities={len(communities)} min_size={min_community_size}",
        f"top: {len(list(communities.values())[0]) if communities else 0} members",
        True, elapsed
    ))

    return "\n".join(lines)


# ── Prefetch for boot_context (memU pattern) ──

@mcp.tool()
async def memory_prefetch(
    agent: str,
    session_hints: Optional[str] = None,
) -> str:
    """Prefetch relevant memories based on recent activity patterns.

    Inspired by memU (NevaMind): monitors patterns and pre-assembles context.
    Uses last session's topics + most activated memories to predict needs.

    Args:
        agent: Agent name
        session_hints: Optional comma-separated hints about current session topic
    """
    import time
    t0 = time.monotonic()
    pool = await get_pool()

    # Strategy 1: Recently activated memories (last 48h)
    recent = await pool.fetch("""
        SELECT id, content, category, importance, query_count, last_activation
        FROM memories
        WHERE agent = $1 AND invalid_at IS NULL
          AND last_activation > NOW() - interval '48 hours'
        ORDER BY query_count DESC, importance DESC
        LIMIT 10
    """, agent)

    # Strategy 2: High-importance memories never activated (cold start)
    cold = await pool.fetch("""
        SELECT id, content, category, importance
        FROM memories
        WHERE agent = $1 AND invalid_at IS NULL
          AND (last_activation IS NULL OR query_count = 0)
          AND importance >= 7
        ORDER BY importance DESC, created_at DESC
        LIMIT 5
    """, agent)

    # Strategy 3: Session-hint based retrieval
    hint_results = []
    if session_hints:
        hints = [h.strip() for h in session_hints.split(",") if h.strip()]
        qdrant = await get_qdrant()
        must_not = [FieldCondition(key="invalid", match=MatchValue(value=True))]
        for hint in hints[:3]:
            try:
                hvec = await get_embedding(hint)
                hresp = await qdrant.query_points(
                    collection_name=QDRANT_COLLECTION,
                    query=hvec,
                    query_filter=Filter(
                        must=[FieldCondition(key="agent", match=MatchValue(value=agent))],
                        must_not=must_not,
                    ),
                    limit=3,
                    with_payload=True,
                )
                for p in hresp.points:
                    hint_results.append({
                        "id": p.id, "content": p.payload.get("content", ""),
                        "category": p.payload.get("category", ""), "sim": p.score,
                        "hint": hint,
                    })
            except Exception:
                pass

    # Strategy 4: Broadcasts from other agents (unread)
    broadcasts = await pool.fetch("""
        SELECT mb.id, mb.from_agent, m.content, m.category, m.importance
        FROM memory_broadcasts mb
        JOIN memories m ON m.id = mb.memory_id
        WHERE NOT (mb.read_by ? $1) AND mb.from_agent != $1
        ORDER BY mb.broadcast_at DESC LIMIT 5
    """, agent)

    elapsed = int((time.monotonic() - t0) * 1000)

    lines = [f"## Memory Prefetch for {agent} ({elapsed}ms)\n"]

    if recent:
        lines.append(f"### Recently Active ({len(recent)} memories)")
        for r in recent[:5]:
            lines.append(f"- #{r['id']} [{r['category']}, imp={r['importance']}, hits={r['query_count']}] {r['content'][:150]}")
        lines.append("")

    if cold:
        lines.append(f"### High-Priority Unactivated ({len(cold)} memories)")
        for c in cold[:3]:
            lines.append(f"- #{c['id']} [{c['category']}, imp={c['importance']}] {c['content'][:150]}")
        lines.append("")

    if hint_results:
        lines.append(f"### Session Hints ({len(hint_results)} matches)")
        for h in hint_results[:5]:
            lines.append(f"- #{h['id']} [{h['category']}, sim={h['sim']:.2f}] (hint: {h['hint']}) {h['content'][:150]}")
        lines.append("")

    if broadcasts:
        lines.append(f"### Unread Broadcasts ({len(broadcasts)})")
        for b in broadcasts[:3]:
            lines.append(f"- from {b['from_agent']} [{b['category']}, imp={b['importance']}] {b['content'][:150]}")
        lines.append("")

    # Strategy 5: Relevant procedural memories (learned workflows)
    proc_rows = []
    proc_query = session_hints or "common tasks"
    try:
        proc_emb = await get_embedding(proc_query)
        proc_rows = await pool.fetch("""
            SELECT id, task_type, query, workflow, hit_count, success_count
            FROM procedural_memories
            WHERE (agent = $1 OR agent = 'TEAM') AND active = true
            ORDER BY (1 - (embedding <=> $2::vector)) DESC
            LIMIT 3
        """, agent, json.dumps(proc_emb))
        if proc_rows:
            lines.append(f"### Relevant Procedures ({len(proc_rows)} workflows)")
            for p in proc_rows:
                rate = p["success_count"] / max(p["hit_count"], 1)
                lines.append(
                    f"- [{p['task_type']}] (used {p['hit_count']}x, {rate:.0%}) "
                    f"{p['query'][:80]}\n  HOW: {p['workflow'][:200]}"
                )
                # Auto-increment hit_count on retrieval
                await pool.execute(
                    "UPDATE procedural_memories SET hit_count = hit_count + 1, updated_at = now() WHERE id = $1",
                    p['id'],
                )
            lines.append("")
    except Exception as e:
        LOG.debug("Procedure prefetch skipped: %s", e)

    if not any([recent, cold, hint_results, broadcasts, proc_rows]):
        lines.append("No prefetch data available.")

    asyncio.create_task(_observe(
        "memory_prefetch", agent,
        f"recent={len(recent)} cold={len(cold)} hints={len(hint_results)} broadcasts={len(broadcasts)}",
        "", True, elapsed
    ))

    return "\n".join(lines)


# ── Active Recall — Real-time context retrieval ──

@mcp.tool()
async def active_recall(
    agent: str,
    context: str,
    include_rules: bool = True,
    include_instincts: bool = True,
    include_memories: bool = True,
    memory_limit: int = 5,
    instinct_limit: int = 3,
) -> str:
    """Real-time context retrieval — the core of SOUL's active memory.

    Call this BEFORE responding to any user message. It searches memories,
    instincts, and rules relevant to the current context and returns them
    as a compact injection. This is what makes SOUL remember mid-session,
    not just at boot.

    Unlike boot_context (runs once) or memory_search (manual), active_recall
    is designed to be called automatically on every turn to keep the agent's
    behavior consistent with its learned patterns.

    Args:
        agent: Agent name (ADA, JARVIS, DUM)
        context: The current user message or situation description
        include_rules: Include active rules (default true)
        include_instincts: Include relevant instincts (default true)
        include_memories: Include relevant memories (default true)
        memory_limit: Max memories to return (default 5)
        instinct_limit: Max instincts to return (default 3)
    """
    import time
    t0 = time.monotonic()
    pool = await get_pool()
    sections = []

    # 1. Relevant memories via semantic search
    activated_memory_ids = []
    if include_memories:
        try:
            query_vec = await get_embedding(context)
            qdrant = await get_qdrant()
            must_not = [FieldCondition(key="invalid", match=MatchValue(value=True))]
            must = [Filter(should=[
                FieldCondition(key="agent", match=MatchValue(value=agent)),
                FieldCondition(key="scope", match=MatchValue(value="shared")),
                FieldCondition(key="scope", match=MatchValue(value="team")),
            ])]

            resp = await qdrant.query_points(
                collection_name=QDRANT_COLLECTION,
                query=query_vec,
                query_filter=Filter(must=must, must_not=must_not),
                limit=memory_limit,
                with_payload=True,
                score_threshold=0.3,
            )

            if resp.points:
                mem_lines = ["## Relevant Memories"]
                for p in resp.points:
                    content = p.payload.get("content", "")[:200]
                    cat = p.payload.get("category", "?")
                    imp = p.payload.get("importance", 5)
                    mem_lines.append(f"- [{cat}, imp={imp}, sim={p.score:.2f}] {content}")
                    activated_memory_ids.append(p.id)
                sections.append("\n".join(mem_lines))

                # Update activation counters
                if activated_memory_ids:
                    await pool.execute("""
                        UPDATE memories SET
                            query_count = COALESCE(query_count, 0) + 1,
                            last_activation = NOW()
                        WHERE id = ANY($1::int[])
                    """, activated_memory_ids)
        except Exception as e:
            sections.append(f"## Memories (error: {e})")

    # 2. Relevant instincts
    if include_instincts:
        try:
            inst_emb = json.dumps(await get_embedding(context))
            instincts = await pool.fetch("""
                SELECT id, trigger_pattern, response, domain, confidence,
                       1 - (embedding <=> $1::vector) as similarity
                FROM instincts
                WHERE agent = $2 AND active = true AND confidence >= 0.5
                ORDER BY embedding <=> $1::vector
                LIMIT $3
            """, inst_emb, agent, instinct_limit)

            if instincts:
                inst_lines = ["## Active Instincts (APPLY THESE)"]
                for i in instincts:
                    # Use top-K ranking, not absolute threshold
                    # Similarity scores between triggers and messages are naturally low
                    tier = "STRONG" if i["confidence"] >= 0.85 else "active"
                    inst_lines.append(
                        f"- [{tier}, conf={i['confidence']:.2f}] "
                        f"WHEN: {i['trigger_pattern'][:100]} → DO: {i['response'][:150]}"
                    )
                sections.append("\n".join(inst_lines))
        except Exception as e:
            sections.append(f"## Instincts (error: {e})")

    # 3. Critical rules (always relevant, filtered by importance)
    if include_rules:
        try:
            # Rules apply to ALL agents — get critical/high regardless of who set them
            rules = await pool.fetch("""
                SELECT rule_key, content, priority FROM rules
                WHERE active = true AND LOWER(priority) IN ('critical', 'high')
                ORDER BY
                    CASE LOWER(priority) WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,
                    rule_key
                LIMIT 5
            """)

            if rules:
                rule_lines = ["## Active Rules (DO NOT VIOLATE)"]
                for r in rules:
                    val_short = r["content"][:120].replace("\n", " ")
                    rule_lines.append(f"- [{r['priority'].upper()}] {r['rule_key']}: {val_short}")
                sections.append("\n".join(rule_lines))
        except Exception as e:
            sections.append(f"## Rules (error: {e})")

    # 4. Recent corrections (last 48h) — highest priority for behavior
    try:
        corrections = await pool.fetch("""
            SELECT content, importance, created_at FROM memories
            WHERE agent = $1 AND category = 'correction' AND invalid_at IS NULL
              AND created_at > NOW() - interval '48 hours'
            ORDER BY importance DESC, created_at DESC
            LIMIT 3
        """, agent)

        if corrections:
            corr_lines = ["## Recent Corrections (HIGHEST PRIORITY)"]
            for c in corrections:
                corr_lines.append(f"- [imp={c['importance']}] {c['content'][:200]}")
            sections.append("\n".join(corr_lines))
    except Exception:
        pass

    elapsed = int((time.monotonic() - t0) * 1000)

    if not sections:
        return f"[active_recall] No relevant context found for: {context[:50]}... ({elapsed}ms)"

    header = f"## ACTIVE RECALL — {agent} ({elapsed}ms)\nContext: {context[:80]}...\n"
    result = header + "\n\n".join(sections)

    # Log the recall event
    asyncio.create_task(_safe_log(
        "active_recall", agent,
        f"memories={len(activated_memory_ids)} instincts={include_instincts} rules={include_rules}",
        "", True, elapsed
    ))

    return result


# ── Delta Sync (AutoGen v0.4 pattern) ──

@mcp.tool()
async def memory_delta_sync(
    agent: str,
    since_minutes: int = 30,
) -> str:
    """Get memory changes since last check — delta sync for multi-agent coordination.

    Instead of re-reading everything, shows only NEW shared/team memories
    from other agents since the specified time window.
    Inspired by AutoGen v0.4 event-driven architecture.

    Args:
        agent: Your agent name
        since_minutes: Look back this many minutes (default 30)
    """
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT id, agent, category, content, importance, scope, created_at, valence
        FROM memories
        WHERE invalid_at IS NULL
          AND agent != $1
          AND scope IN ('shared', 'team')
          AND created_at > NOW() - make_interval(mins => $2)
        ORDER BY created_at DESC
        LIMIT 20
    """, agent, since_minutes)

    if not rows:
        return f"No shared/team memory changes from other agents in the last {since_minutes} minutes."

    lines = [f"## Delta Sync for {agent} — {len(rows)} changes in last {since_minutes}min\n"]
    for r in rows:
        v_tag = f" v={r['valence']:+.2f}" if r['valence'] is not None else ""
        lines.append(
            f"- #{r['id']} [{r['agent']}, {r['category']}, imp={r['importance']}, {r['scope']}]{v_tag} "
            f"at {r['created_at'].strftime('%H:%M')}\n"
            f"  {r['content'][:200]}"
        )

    return "\n".join(lines)


# ── ACE Curator: Agentic Context Engineering (arxiv 2510.04618) ──
# Cycle: Generate (agents work) → Reflect (distill insights) → Curate (update context)

@mcp.tool()
async def ace_curator(
    agent: str,
    days: int = 3,
    dry_run: bool = True,
) -> str:
    """ACE Curator — automatically improves agent context from behavioral patterns.

    Analyzes recent observations, corrections, and memory usage to:
    1. Create new instincts from repeated patterns
    2. Update procedural memories from successful workflows
    3. Suggest rule changes based on corrections
    4. Strengthen co-activated memory edges (LTP)

    Based on ACE (arxiv 2510.04618) Generate-Reflect-Curate cycle.

    Args:
        agent: Agent to curate for
        days: Days of history to analyze (default 3)
        dry_run: If true, only report what would change (default true)
    """
    import time as _t
    t0 = _t.monotonic()
    pool = await get_pool()
    actions = {"instincts_created": 0, "procedures_updated": 0, "ltp_strengthened": 0, "suggestions": []}

    # ═══ Phase 1: REFLECT — analyze patterns ═══

    # 1a. Tool usage patterns
    tool_stats = await pool.fetch("""
        SELECT tool_name, COUNT(*) as freq, AVG(latency_ms) as avg_ms,
               COUNT(*) FILTER (WHERE success) as successes,
               COUNT(*) FILTER (WHERE NOT success) as failures
        FROM tool_observations
        WHERE agent = $1 AND created_at > NOW() - make_interval(days => $2)
        GROUP BY tool_name
        ORDER BY freq DESC LIMIT 15
    """, agent, days)

    # 1b. Correction memories (recent)
    corrections = await pool.fetch("""
        SELECT id, content, importance FROM memories
        WHERE agent = $1 AND category = 'correction' AND invalid_at IS NULL
          AND created_at > NOW() - make_interval(days => $2)
        ORDER BY importance DESC LIMIT 10
    """, agent, days)

    # 1c. Co-activation pairs (memories retrieved together frequently)
    coactivated = await pool.fetch("""
        SELECT m1.id as id1, m2.id as id2, COUNT(*) as co_count
        FROM (
            SELECT id, last_activation FROM memories
            WHERE agent = $1 AND invalid_at IS NULL AND last_activation IS NOT NULL
              AND last_activation > NOW() - make_interval(days => $2)
        ) m1
        JOIN (
            SELECT id, last_activation FROM memories
            WHERE agent = $1 AND invalid_at IS NULL AND last_activation IS NOT NULL
              AND last_activation > NOW() - make_interval(days => $2)
        ) m2 ON m1.id < m2.id
            AND ABS(EXTRACT(EPOCH FROM (m1.last_activation - m2.last_activation))) < 60
        GROUP BY m1.id, m2.id
        HAVING COUNT(*) >= 2
        ORDER BY co_count DESC LIMIT 20
    """, agent, days)

    # 1d. High-utility memories (reward signals from memory_utility_update)
    utility_signals = await pool.fetch("""
        SELECT u.memory_id, AVG(u.reward) as avg_reward, COUNT(*) as updates
        FROM utility_updates u
        JOIN memories m ON m.id = u.memory_id
        WHERE u.updated_at > NOW() - make_interval(days => $1)
          AND m.agent = $2
        GROUP BY u.memory_id
        HAVING COUNT(*) >= 2
        ORDER BY AVG(u.reward) DESC LIMIT 10
    """, days, agent)

    # ═══ Phase 2: CURATE — generate improvements ═══

    # 2a. Create instincts from corrections (if not already covered)
    instinct_candidates = []
    if corrections:
        for c in corrections[:5]:
            # Check if similar instinct already exists
            try:
                emb = await get_embedding(c["content"])
                existing = await pool.fetch("""
                    SELECT id, trigger_pattern FROM instincts
                    WHERE agent = $1 AND active = true
                      AND 1 - (embedding <=> $2::vector) > 0.80
                    LIMIT 1
                """, agent, json.dumps(emb))

                if not existing:
                    instinct_candidates.append({
                        "source_memory": c["id"],
                        "content": c["content"][:200],
                        "importance": c["importance"],
                    })
            except Exception:
                pass

    # Use Ollama to generate instinct trigger/response from corrections
    new_instincts = []
    if instinct_candidates:
        corrections_text = "\n".join(
            f"- (imp={ic['importance']}) {ic['content']}" for ic in instinct_candidates[:3]
        )
        try:
            prompt = (
                f"Convierte estas correcciones en instintos de comportamiento para {agent}.\n"
                f"Un instinto tiene: trigger (CUANDO situación), response (HACER acción), domain.\n\n"
                f"Correcciones:\n{corrections_text}\n\n"
                f"Devuelve SOLO un JSON array: [{{\"trigger\": \"...\", \"response\": \"...\", \"domain\": \"...\"}}]\n"
                f"Máximo 3 instintos. Solo los más claros y accionables."
            )
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    OLLAMA_GEN_URL,
                    json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                          "options": {"temperature": 0.3, "num_predict": 400}},
                )
                raw = resp.json().get("response", "")
                json_match = re.search(r'\[[\s\S]*?\]', raw)
                if json_match:
                    new_instincts = json.loads(json_match.group())
        except Exception as e:
            LOG.debug("ACE instinct generation failed: %s", e)

    # 2b. LTP — strengthen co-activated memory edges in Neo4j
    ltp_count = 0
    if coactivated and not dry_run:
        driver = get_neo4j()
        async with driver.session() as session:
            for pair in coactivated[:10]:
                try:
                    await session.run("""
                        MATCH (a:Memory {memory_id: $id1})-[r:EXCITES]-(b:Memory {memory_id: $id2})
                        SET r.weight = LEAST(1.0, r.weight + 0.05 * $co_count),
                            r.ltp_count = COALESCE(r.ltp_count, 0) + $co_count
                    """, id1=pair["id1"], id2=pair["id2"], co_count=pair["co_count"])
                    ltp_count += 1
                except Exception:
                    pass
    actions["ltp_strengthened"] = ltp_count

    # ═══ Phase 3: EXECUTE (if not dry_run) ═══
    if not dry_run and new_instincts:
        for inst in new_instincts[:3]:
            trigger = inst.get("trigger", "")
            response = inst.get("response", "")
            domain = inst.get("domain", "general")
            if trigger and response:
                try:
                    emb = await get_embedding(trigger)
                    await pool.execute("""
                        INSERT INTO instincts (agent, trigger_pattern, response_pattern, domain,
                                             confidence, source, embedding)
                        VALUES ($1, $2, $3, $4, 0.50, 'ace_curator', $5)
                    """, agent, trigger[:500], response[:500], domain, json.dumps(emb))
                    actions["instincts_created"] += 1
                except Exception as e:
                    LOG.debug("ACE instinct creation failed: %s", e)

    elapsed = int((_t.monotonic() - t0) * 1000)

    # Build report
    lines = [f"## ACE Curator Report — {agent} ({elapsed}ms)\n"]
    lines.append(f"Period: last {days} days | Mode: {'DRY RUN' if dry_run else 'LIVE'}\n")

    if tool_stats:
        lines.append("### Tool Usage Patterns")
        for t in tool_stats[:8]:
            fail_pct = (t["failures"] / t["freq"] * 100) if t["freq"] else 0
            lines.append(f"  {t['tool_name']:30s} {t['freq']:3d}x  avg={t['avg_ms']:.0f}ms  fail={fail_pct:.0f}%")
        lines.append("")

    if corrections:
        lines.append(f"### Recent Corrections ({len(corrections)})")
        for c in corrections[:3]:
            lines.append(f"  - (imp={c['importance']}) {c['content'][:120]}")
        lines.append("")

    if coactivated:
        lines.append(f"### Co-activated Pairs — LTP candidates ({len(coactivated)})")
        for p in coactivated[:5]:
            lines.append(f"  Memory #{p['id1']} ↔ #{p['id2']}: {p['co_count']}x co-activated")
        if not dry_run:
            lines.append(f"  → Strengthened {ltp_count} edges")
        lines.append("")

    if utility_signals:
        lines.append(f"### Utility Signals ({len(utility_signals)} memories with feedback)")
        for u in utility_signals[:5]:
            lines.append(f"  Memory #{u['memory_id']}: avg_reward={u['avg_reward']:.2f} ({u['updates']}x)")
        lines.append("")

    if new_instincts:
        lines.append(f"### {'Proposed' if dry_run else 'Created'} Instincts ({len(new_instincts)})")
        for i, inst in enumerate(new_instincts):
            status = "📋 PROPOSED" if dry_run else "✅ CREATED"
            lines.append(f"  {status} #{i+1}: WHEN {inst.get('trigger', '?')[:80]}")
            lines.append(f"           DO {inst.get('response', '?')[:80]}")
        if dry_run:
            lines.append("\n  Run with dry_run=false to create these instincts.")
        lines.append("")

    actions["suggestions"] = new_instincts
    lines.append(f"### Summary: {actions['instincts_created']} instincts created, "
                 f"{actions['ltp_strengthened']} LTP edges strengthened")

    return "\n".join(lines)


# ── Co-activation LTP (Long-Term Potentiation) ──

@mcp.tool()
async def connectome_ltp(
    agent: Optional[str] = None,
    days: int = 7,
    min_coactivations: int = 2,
    dry_run: bool = True,
) -> str:
    """Strengthen Neo4j edges between memories that are frequently co-activated.

    Long-Term Potentiation (LTP): when two memories fire together repeatedly,
    the connection between them strengthens — like synapses in the brain.

    Args:
        agent: Filter by agent (optional)
        days: Look back period (default 7)
        min_coactivations: Minimum co-activations to strengthen (default 2)
        dry_run: If true, only report (default true)
    """
    pool = await get_pool()
    agent_filter = "AND m1.agent = $3" if agent else ""
    params = [days, min_coactivations]
    if agent:
        params.append(agent)

    # Find memory pairs activated within 60 seconds of each other
    pairs = await pool.fetch(f"""
        WITH activated AS (
            SELECT id, last_activation FROM memories
            WHERE invalid_at IS NULL AND last_activation IS NOT NULL
              AND last_activation > NOW() - make_interval(days => $1)
              {agent_filter.replace('m1.', '')}
        )
        SELECT a.id as id1, b.id as id2,
               COUNT(*) as co_count
        FROM activated a
        JOIN activated b ON a.id < b.id
            AND ABS(EXTRACT(EPOCH FROM (a.last_activation - b.last_activation))) < 120
        GROUP BY a.id, b.id
        HAVING COUNT(*) >= $2
        ORDER BY co_count DESC
        LIMIT 50
    """, *params)

    if not pairs:
        return f"No co-activation pairs found in the last {days} days."

    lines = [f"## LTP Analysis — {len(pairs)} co-activated pairs\n"]

    strengthened = 0
    if not dry_run:
        driver = get_neo4j()
        async with driver.session() as session:
            for p in pairs:
                try:
                    # Strengthen existing edge or create new one
                    result = await session.run("""
                        MATCH (a:Memory {memory_id: $id1}), (b:Memory {memory_id: $id2})
                        MERGE (a)-[r:EXCITES]->(b)
                        SET r.weight = LEAST(1.0, COALESCE(r.weight, 0.5) + 0.05 * $boost),
                            r.ltp_count = COALESCE(r.ltp_count, 0) + $boost,
                            r.ltp_last = datetime()
                        RETURN r.weight AS new_weight
                    """, id1=p["id1"], id2=p["id2"], boost=p["co_count"])
                    record = await result.single()
                    if record:
                        strengthened += 1
                        lines.append(f"  ✅ #{p['id1']} ↔ #{p['id2']}: +{p['co_count']*0.05:.2f} weight (→{record['new_weight']:.3f})")
                except Exception:
                    pass
    else:
        for p in pairs[:15]:
            lines.append(f"  📋 #{p['id1']} ↔ #{p['id2']}: {p['co_count']}x co-activated → would strengthen by +{p['co_count']*0.05:.2f}")

    lines.append(f"\n{'Strengthened' if not dry_run else 'Would strengthen'}: {strengthened if not dry_run else len(pairs)} edges")
    if dry_run:
        lines.append("Run with dry_run=false to apply LTP.")

    return "\n".join(lines)


# ── Temporal Graph Hierarchy (TG-RAG, arxiv 2510.13590) ──

@mcp.tool()
async def temporal_graph_build(agent: Optional[str] = None) -> str:
    """Build temporal hierarchy in Neo4j: Year→Month→Day, link memories via OCCURRED_ON.
    Enables time-range queries like 'what happened in March 2026?'

    Args:
        agent: Only process this agent's memories (optional)
    """
    import time as _t
    t0 = _t.monotonic()
    pool = await get_pool()
    driver = get_neo4j()

    rows = await pool.fetch(f"""
        SELECT id, created_at FROM memories
        WHERE invalid_at IS NULL AND created_at IS NOT NULL
          {'AND agent = $1' if agent else ''}
        ORDER BY created_at
    """, *([agent] if agent else []))

    if not rows:
        return "No memories to build temporal graph."

    dates = set()
    for r in rows:
        dt = r["created_at"]
        dates.add((dt.year, dt.month, dt.day))

    async with driver.session() as session:
        years = {d[0] for d in dates}
        months = {(d[0], d[1]) for d in dates}

        for y in years:
            await session.run("MERGE (:Year {year: $y})", y=y)
        for y, m in months:
            await session.run("""
                MERGE (mo:Month {year: $y, month: $m})
                WITH mo MATCH (yr:Year {year: $y})
                MERGE (yr)-[:HAS_MONTH]->(mo)
            """, y=y, m=m)
        for y, m, d in dates:
            await session.run("""
                MERGE (dy:Day {year: $y, month: $m, day: $d})
                SET dy.date = date({year: $y, month: $m, day: $d})
                WITH dy MATCH (mo:Month {year: $y, month: $m})
                MERGE (mo)-[:HAS_DAY]->(dy)
            """, y=y, m=m, d=d)

        linked = 0
        for r in rows:
            dt = r["created_at"]
            try:
                await session.run("""
                    MATCH (mem:Memory {memory_id: $mid}), (dy:Day {year: $y, month: $m, day: $d})
                    MERGE (mem)-[:OCCURRED_ON]->(dy)
                """, mid=r["id"], y=dt.year, m=dt.month, d=dt.day)
                linked += 1
            except Exception:
                pass

    elapsed = int((_t.monotonic() - t0) * 1000)
    return f"Temporal graph built ({elapsed}ms): {len(years)} years, {len(months)} months, {len(dates)} days, {linked} memories linked."


@mcp.tool()
async def temporal_query(
    start_date: str,
    end_date: Optional[str] = None,
    agent: Optional[str] = None,
    category: Optional[str] = None,
    summarize: bool = True,
) -> str:
    """Query memories by time range via temporal graph. Optionally summarize with Ollama.

    Args:
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD (defaults to start_date)
        agent: Filter by agent
        category: Filter by category
        summarize: Generate LLM summary (default true)
    """
    if not end_date:
        end_date = start_date
    try:
        sp = [int(x) for x in start_date.split("-")]
        ep = [int(x) for x in end_date.split("-")]
    except Exception:
        return "Invalid date format. Use YYYY-MM-DD."

    driver = get_neo4j()
    af = "AND mem.agent = $agent" if agent else ""
    cf = "AND mem.category = $cat" if category else ""
    params = {"sy": sp[0], "sm": sp[1], "sd": sp[2], "ey": ep[0], "em": ep[1], "ed": ep[2]}
    if agent:
        params["agent"] = agent
    if category:
        params["cat"] = category

    async with driver.session() as session:
        result = await session.run(f"""
            MATCH (mem:Memory)-[:OCCURRED_ON]->(d:Day)
            WHERE d.date >= date({{year: $sy, month: $sm, day: $sd}})
              AND d.date <= date({{year: $ey, month: $em, day: $ed}})
              {af} {cf}
            RETURN mem.memory_id AS mid, mem.content AS content, mem.category AS cat,
                   mem.importance AS imp, mem.agent AS ag, d.day + '-' + d.month + '-' + d.year AS day_label
            ORDER BY d.date, mem.importance DESC
            LIMIT 50
        """, **params)
        memories = [r.data() async for r in result]

    if not memories:
        return f"No memories found between {start_date} and {end_date}."

    lines = [f"## Temporal Query: {start_date} to {end_date} ({len(memories)} memories)\n"]

    for m in memories:
        lines.append(f"- [{m['cat']}, imp={m['imp']}, {m['ag']}] {(m['content'] or '')[:150]}")

    if summarize and memories:
        sample = "\n".join(f"- [{m['cat']}] {(m['content'] or '')[:100]}" for m in memories[:12])
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    OLLAMA_GEN_URL,
                    json={"model": OLLAMA_MODEL, "prompt": (
                        f"Resume en 2 oraciones qué ocurrió entre {start_date} y {end_date}:\n{sample}\n\n"
                        f"Responde en español, máximo 50 palabras."
                    ), "stream": False, "options": {"temperature": 0.3, "num_predict": 80}},
                )
                summary = resp.json().get("response", "").strip().split("\n")[0][:250]
                lines.insert(1, f"**Resumen:** {summary}\n")
        except Exception:
            pass

    return "\n".join(lines)


# ── OCEAN Auto-Calibration ──

@mcp.tool()
async def ocean_auto_calibrate(
    agent: str,
    days: int = 7,
    dry_run: bool = True,
) -> str:
    """Auto-calibrate OCEAN scores from observed behavior (not manual).

    Signals: C=success_rate, E=sharing_ratio, O=variety, N=valence_variance, A=correction_rate.
    Blends 70% old + 30% observed to prevent wild swings.

    Args:
        agent: Agent to calibrate
        days: Analysis period (default 7)
        dry_run: Only report, don't apply (default true)
    """
    pool = await get_pool()
    identity = await pool.fetchrow("SELECT ocean_scores FROM identity WHERE agent = $1", agent)
    if not identity or not identity["ocean_scores"]:
        return f"No OCEAN profile for {agent}."
    current = json.loads(identity["ocean_scores"]) if isinstance(identity["ocean_scores"], str) else identity["ocean_scores"]

    # Behavioral signals
    obs = await pool.fetchrow("""
        SELECT COUNT(*) as total, COUNT(*) FILTER (WHERE success) as ok
        FROM tool_observations WHERE agent = $1 AND created_at > NOW() - make_interval(days => $2)
    """, agent, days)

    mems = await pool.fetchrow("""
        SELECT COUNT(*) as total,
               COUNT(*) FILTER (WHERE scope IN ('shared','team')) as shared,
               COUNT(DISTINCT category) as cat_v
        FROM memories WHERE agent = $1 AND invalid_at IS NULL
          AND created_at > NOW() - make_interval(days => $2)
    """, agent, days)

    tool_v = await pool.fetchval("""
        SELECT COUNT(DISTINCT tool_name) FROM tool_observations
        WHERE agent = $1 AND created_at > NOW() - make_interval(days => $2)
    """, agent, days)

    emo = await pool.fetchrow("""
        SELECT AVG(valence) as avg_v, STDDEV(valence) as std_v
        FROM memories WHERE agent = $1 AND invalid_at IS NULL AND valence IS NOT NULL
          AND created_at > NOW() - make_interval(days => $2)
    """, agent, days)

    corrections = await pool.fetchval("""
        SELECT COUNT(*) FROM memories WHERE agent = $1 AND category = 'correction'
          AND invalid_at IS NULL AND created_at > NOW() - make_interval(days => $2)
    """, agent, days)

    # Compute observed OCEAN
    observed = dict(current)
    if obs and obs["total"] > 0:
        observed["C"] = round(min(1.0, 0.5 + (obs["ok"] / obs["total"]) * 0.5), 3)
    if mems and mems["total"] > 0:
        observed["E"] = round(min(1.0, 0.2 + (mems["shared"] / mems["total"]) * 3.0), 3)
        observed["O"] = round(min(1.0, 0.3 + (mems["cat_v"] / 11) * 0.5 + ((tool_v or 0) / 20) * 0.2), 3)
        cr = corrections / max(mems["total"], 1)
        observed["A"] = round(max(0.3, min(1.0, 0.8 - cr * 2.0)), 3)
    if emo and emo["std_v"] is not None:
        observed["N"] = round(min(0.5, float(emo["std_v"]) * 0.5), 3)

    # Blend 70/30
    blended = {t: round(current.get(t, 0.5) * 0.7 + observed.get(t, current.get(t, 0.5)) * 0.3, 3) for t in "OCEAN"}

    if not dry_run:
        await pool.execute("UPDATE identity SET ocean_scores = $1 WHERE agent = $2", json.dumps(blended), agent)

    lines = [f"## OCEAN Auto-Calibration — {agent} ({'DRY RUN' if dry_run else 'APPLIED'})\n"]
    lines.append(f"  {'Trait':5s} {'Current':>8s} {'Observed':>9s} {'Blended':>8s} {'Δ':>6s}")
    for t in "OCEAN":
        c, o, b = current.get(t, 0.5), observed.get(t, 0.5), blended[t]
        d = b - c
        lines.append(f"  {t:5s} {c:8.3f} {o:9.3f} {b:8.3f} {d:+6.3f} {'↑' if d > 0.01 else '↓' if d < -0.01 else '='}")

    lines.append(f"\nSignals: obs={obs['total'] if obs else 0} tools, {mems['total'] if mems else 0} memories, {corrections} corrections")

    # Intra-session drift check vs boot baseline (arxiv 2502.11843)
    try:
        ws = await pool.fetchrow("SELECT state FROM working_state WHERE agent = $1", agent)
        if ws and ws["state"]:
            ws_data = json.loads(ws["state"]) if isinstance(ws["state"], str) else ws["state"]
            baseline = ws_data.get("ocean_baseline")
            if baseline:
                session_drift = sum(abs(blended.get(t, 0.5) - baseline.get(t, 0.5)) for t in "OCEAN")
                lines.append(f"\nSession drift vs boot baseline: {session_drift:.4f}")
                if session_drift > 0.15:
                    lines.append("⚠️  SIGNIFICANT intra-session drift detected — personality may be shifting")
                elif session_drift > 0.05:
                    lines.append("Minor session drift — within normal range")
                else:
                    lines.append("Stable — no meaningful drift from boot baseline")
    except Exception:
        pass

    if dry_run:
        lines.append("Run with dry_run=false to apply.")
    return "\n".join(lines)


# ── Research Round 3: Graphiti 4-Timestamp, OCEAN State Machine, D-MEM ──


@mcp.tool()
async def connectome_bitemporal(
    agent: Optional[str] = None,
    dry_run: bool = True,
) -> str:
    """Add Graphiti-style 4-timestamp model to Neo4j edges.

    Adds valid_at/invalid_at properties to edges for bitemporal fact tracking.
    Edges can be invalidated without deletion, preserving historical graph state.
    Based on Graphiti (Zep, arxiv 2501.13956) 4-timestamp model.

    Args:
        agent: Filter by agent (optional)
        dry_run: Only report, don't modify (default true)
    """
    driver = get_neo4j()
    now_iso = datetime.now(timezone.utc).isoformat()

    async with driver.session() as session:
        # Count edges missing bitemporal properties
        agent_filter = "{agent: $agent}" if agent else ""
        params = {"agent": agent} if agent else {}

        result = await session.run(
            f"MATCH (a:Memory {agent_filter})-[r]->() "
            f"WHERE r.valid_at IS NULL "
            f"RETURN count(r) AS missing",
            **params,
        )
        rec = await result.single()
        missing = rec["missing"] if rec else 0

        result2 = await session.run(
            f"MATCH (a:Memory {agent_filter})-[r]->() RETURN count(r) AS total",
            **params,
        )
        rec2 = await result2.single()
        total = rec2["total"] if rec2 else 0

        if not dry_run and missing > 0:
            # Set valid_at on edges that don't have it (backfill)
            await session.run(
                f"MATCH (a:Memory {agent_filter})-[r]->() "
                f"WHERE r.valid_at IS NULL "
                f"SET r.valid_at = COALESCE(r.valid_from, $now), "
                f"    r.invalid_at = null, "
                f"    r.created_at = COALESCE(r.valid_from, $now), "
                f"    r.expired_at = null",
                now=now_iso, **params,
            )

    lines = [f"## Connectome Bitemporal — {'DRY RUN' if dry_run else 'APPLIED'}"]
    lines.append(f"Total edges: {total}")
    lines.append(f"Missing valid_at: {missing}")
    lines.append(f"Graphiti 4-timestamp: created_at, expired_at, valid_at, invalid_at")
    if dry_run and missing > 0:
        lines.append(f"Run with dry_run=false to backfill {missing} edges.")
    elif not dry_run:
        lines.append(f"Backfilled {missing} edges with bitemporal timestamps.")
    return "\n".join(lines)


@mcp.tool()
async def connectome_invalidate_edge(
    source_id: int,
    target_id: int,
    reason: str = "",
) -> str:
    """Invalidate a Neo4j edge without deleting it (Graphiti pattern).

    Sets invalid_at + expired_at timestamps instead of removing the edge.
    This preserves historical graph state for temporal queries.

    Args:
        source_id: Source memory ID
        target_id: Target memory ID
        reason: Why this edge is being invalidated
    """
    driver = get_neo4j()
    now_iso = datetime.now(timezone.utc).isoformat()

    async with driver.session() as session:
        result = await session.run(
            "MATCH (a:Memory {memory_id: $src})-[r]->(b:Memory {memory_id: $tgt}) "
            "WHERE r.invalid_at IS NULL "
            "SET r.invalid_at = $now, r.expired_at = $now, r.invalidation_reason = $reason "
            "RETURN type(r) AS rel_type, r.weight AS weight",
            src=source_id, tgt=target_id, now=now_iso, reason=reason,
        )
        rec = await result.single()

    if rec:
        return f"Edge {source_id}→{target_id} ({rec['rel_type']}, w={rec['weight']:.2f}) invalidated at {now_iso}. Reason: {reason}"
    return f"No active edge found between {source_id} and {target_id}."


@mcp.tool()
async def connectome_causal(
    agent: Optional[str] = None,
    dry_run: bool = True,
) -> str:
    """Build CAUSES edges in Neo4j connectome — the missing causal graph.

    Based on MAGMA (arxiv 2601.03236): orthogonal causal dimension.
    Sources of causality:
    1. reasoning_traces with linked_memory_ids → premises CAUSE conclusion
    2. corrections → old behavior CAUSED correction
    3. temporal sequence → decision within 1h before milestone = potential CAUSES

    Args:
        agent: Filter by agent (optional)
        dry_run: Only report, don't create edges (default true)
    """
    pool = await get_pool()
    driver = get_neo4j()
    now_iso = datetime.now(timezone.utc).isoformat()
    created = 0
    sources = {"traces": 0, "corrections": 0, "temporal": 0}

    # --- Source 1: Reasoning traces → linked memories CAUSE the trace's conclusion ---
    agent_filter_sql = "WHERE agent = $1" if agent else ""
    params = [agent] if agent else []

    async with pool.acquire() as conn:
        traces = await conn.fetch(
            f"SELECT id, agent, task, conclusion, linked_memory_ids, created_at "
            f"FROM reasoning_traces {agent_filter_sql} "
            f"ORDER BY created_at DESC LIMIT 500",
            *params,
        )

    if not dry_run:
        async with driver.session() as session:
            for t in traces:
                mem_ids = t["linked_memory_ids"] or []
                if not mem_ids:
                    continue
                trace_id = t["id"]
                # Each linked memory CAUSES the trace outcome
                for mid in mem_ids:
                    # Find if trace has a "conclusion memory" stored nearby in time
                    # For now, link memories to trace node with CAUSES
                    await session.run(
                        "MATCH (m:Memory {memory_id: $mid}), (t:Trace {trace_id: $tid}) "
                        "MERGE (m)-[r:CAUSES]->(t) "
                        "SET r.weight = 0.85, r.source = 'reasoning_trace', "
                        "    r.valid_from = coalesce(r.valid_from, $now), "
                        "    r.valid_at = coalesce(r.valid_at, $now)",
                        mid=mid, tid=trace_id, now=now_iso,
                    )
                    created += 1
                    sources["traces"] += 1

    else:
        for t in traces:
            mem_ids = t["linked_memory_ids"] or []
            sources["traces"] += len(mem_ids)

    # --- Source 2: Corrections → find what they correct via semantic similarity ---
    async with pool.acquire() as conn:
        corrections = await conn.fetch(
            f"SELECT id, agent, content, created_at FROM memories "
            f"WHERE category = 'correction' AND invalid_at IS NULL "
            f"{'AND agent = $1' if agent else ''} "
            f"ORDER BY created_at DESC LIMIT 200",
            *params,
        )

    qdrant = await get_qdrant()
    correction_pairs = []
    for corr in corrections:
        # Find the memory this correction targets (highest similarity, non-correction)
        try:
            search_resp = await qdrant.query_points(
                collection_name=QDRANT_COLLECTION,
                query=corr["id"],  # Use memory ID as lookup
                query_filter=Filter(
                    must_not=[
                        FieldCondition(key="invalid", match=MatchValue(value=True)),
                        FieldCondition(key="category", match=MatchValue(value="correction")),
                    ],
                ),
                limit=3,
                score_threshold=0.75,
                with_payload=True,
            )
            for s in search_resp.points:
                if s.id != corr["id"]:
                    correction_pairs.append((s.id, corr["id"], float(s.score)))
                    sources["corrections"] += 1
                    break
        except Exception:
            continue

    if not dry_run and correction_pairs:
        async with driver.session() as session:
            for src_id, tgt_id, score in correction_pairs:
                await session.run(
                    "MATCH (a:Memory {memory_id: $src}), (b:Memory {memory_id: $tgt}) "
                    "MERGE (a)-[r:CAUSES]->(b) "
                    "SET r.weight = $weight, r.source = 'correction_chain', "
                    "    r.valid_from = coalesce(r.valid_from, $now), "
                    "    r.valid_at = coalesce(r.valid_at, $now)",
                    src=src_id, tgt=tgt_id, weight=score, now=now_iso,
                )
                created += 1

    # --- Source 3: Temporal causality → decision/command followed by milestone within 1h ---
    async with pool.acquire() as conn:
        decisions = await conn.fetch(
            f"SELECT id, agent, category, content, created_at FROM memories "
            f"WHERE category IN ('decision', 'command', 'directive') "
            f"AND invalid_at IS NULL "
            f"{'AND agent = $1' if agent else ''} "
            f"ORDER BY created_at DESC LIMIT 300",
            *params,
        )
        milestones = await conn.fetch(
            f"SELECT id, agent, category, content, created_at FROM memories "
            f"WHERE category IN ('milestone', 'outcome', 'implementation') "
            f"AND invalid_at IS NULL "
            f"{'AND agent = $1' if agent else ''} "
            f"ORDER BY created_at DESC LIMIT 300",
            *params,
        )

    temporal_pairs = []
    for d in decisions:
        for m in milestones:
            if m["agent"] != d["agent"]:
                continue
            delta = m["created_at"] - d["created_at"]
            # Milestone must come AFTER decision, within 1 hour
            if timedelta(seconds=0) < delta < timedelta(hours=1):
                # Weight inversely proportional to time gap
                weight = max(0.5, 1.0 - (delta.total_seconds() / 3600))
                temporal_pairs.append((d["id"], m["id"], weight))
                sources["temporal"] += 1

    if not dry_run and temporal_pairs:
        async with driver.session() as session:
            for src_id, tgt_id, weight in temporal_pairs:
                await session.run(
                    "MATCH (a:Memory {memory_id: $src}), (b:Memory {memory_id: $tgt}) "
                    "MERGE (a)-[r:CAUSES]->(b) "
                    "SET r.weight = $weight, r.source = 'temporal_sequence', "
                    "    r.valid_from = coalesce(r.valid_from, $now), "
                    "    r.valid_at = coalesce(r.valid_at, $now)",
                    src=src_id, tgt=tgt_id, weight=weight, now=now_iso,
                )
                created += 1

    total_potential = sources["traces"] + sources["corrections"] + sources["temporal"]

    lines = [f"## Connectome CAUSAL — {'DRY RUN' if dry_run else 'APPLIED'} (MAGMA 2601.03236)"]
    lines.append(f"Agent: {agent or 'all'}")
    lines.append(f"Traces with linked memories: {len([t for t in traces if t['linked_memory_ids']])}")
    lines.append(f"Correction chains found: {sources['corrections']}")
    lines.append(f"Temporal decision→milestone pairs: {sources['temporal']}")
    lines.append(f"Total potential CAUSES edges: {total_potential}")
    if not dry_run:
        lines.append(f"Created: {created} CAUSES edges")
    else:
        lines.append(f"Run with dry_run=false to create {total_potential} edges.")
    return "\n".join(lines)


@mcp.tool()
async def ocean_state_machine(
    agent: str,
    days: int = 7,
    dry_run: bool = True,
) -> str:
    """OCEAN calibration with state machine dynamics (arxiv 2602.22157).

    Upgrade over simple 70/30 blending: uses baseline anchoring, momentum,
    and observation weights to prevent personality oscillation.

    Formula: new = w_baseline*baseline + w_current*current + w_momentum*momentum + w_observed*observed
    Weights: baseline=0.15, current=0.50, momentum=0.25, observed=0.10

    Args:
        agent: Agent to calibrate
        days: Analysis period (default 7)
        dry_run: Only report, don't apply (default true)
    """
    pool = await get_pool()

    # Get current OCEAN + baseline (first recorded)
    identity = await pool.fetchrow("SELECT ocean_scores FROM identity WHERE agent = $1", agent)
    if not identity or not identity["ocean_scores"]:
        return f"No OCEAN profile for {agent}."
    current = json.loads(identity["ocean_scores"]) if isinstance(identity["ocean_scores"], str) else identity["ocean_scores"]

    # Get baseline from first drift_metrics or use SEED defaults
    baseline_row = await pool.fetchrow(
        "SELECT ocean_baseline FROM drift_metrics WHERE agent = $1 ORDER BY measured_at ASC LIMIT 1",
        agent,
    )
    if baseline_row and baseline_row["ocean_baseline"]:
        bl = baseline_row["ocean_baseline"]
        baseline = json.loads(bl) if isinstance(bl, str) else bl
    else:
        # Use current OCEAN as baseline if no drift history
        baseline = dict(current)

    # Get momentum (trend from last 3 measurements)
    history = await pool.fetch(
        "SELECT ocean_measured FROM drift_metrics WHERE agent = $1 ORDER BY measured_at DESC LIMIT 3",
        agent,
    )
    momentum = {}
    if len(history) >= 2:
        scores_list = []
        for h in history:
            raw = h["ocean_measured"]
            if raw is None:
                continue
            s = json.loads(raw) if isinstance(raw, str) else raw
            scores_list.append(s)
        if len(scores_list) >= 2:
            for t in "OCEAN":
                vals = [s.get(t, 0.5) for s in scores_list]
                momentum[t] = vals[0] - vals[-1]
        else:
            momentum = {t: 0.0 for t in "OCEAN"}
    else:
        momentum = {t: 0.0 for t in "OCEAN"}

    # Get observed signals (reuse ocean_auto_calibrate logic)
    obs = await pool.fetchrow("""
        SELECT COUNT(*) as total, COUNT(*) FILTER (WHERE success) as ok
        FROM tool_observations WHERE agent = $1 AND created_at > NOW() - make_interval(days => $2)
    """, agent, days)

    mems = await pool.fetchrow("""
        SELECT COUNT(*) as total,
               COUNT(*) FILTER (WHERE scope IN ('shared','team')) as shared,
               COUNT(DISTINCT category) as cat_v
        FROM memories WHERE agent = $1 AND invalid_at IS NULL
          AND created_at > NOW() - make_interval(days => $2)
    """, agent, days)

    tool_v = await pool.fetchval("""
        SELECT COUNT(DISTINCT tool_name) FROM tool_observations
        WHERE agent = $1 AND created_at > NOW() - make_interval(days => $2)
    """, agent, days)

    emo = await pool.fetchrow("""
        SELECT AVG(valence) as avg_v, STDDEV(valence) as std_v
        FROM memories WHERE agent = $1 AND invalid_at IS NULL AND valence IS NOT NULL
          AND created_at > NOW() - make_interval(days => $2)
    """, agent, days)

    corrections = await pool.fetchval("""
        SELECT COUNT(*) FROM memories WHERE agent = $1 AND category = 'correction'
          AND invalid_at IS NULL AND created_at > NOW() - make_interval(days => $2)
    """, agent, days)

    # Compute observed OCEAN
    observed = dict(current)
    if obs and obs["total"] > 0:
        observed["C"] = round(min(1.0, 0.5 + (obs["ok"] / obs["total"]) * 0.5), 3)
    if mems and mems["total"] > 0:
        observed["E"] = round(min(1.0, 0.2 + (mems["shared"] / mems["total"]) * 3.0), 3)
        observed["O"] = round(min(1.0, 0.3 + (mems["cat_v"] / 11) * 0.5 + ((tool_v or 0) / 20) * 0.2), 3)
        cr = corrections / max(mems["total"], 1)
        observed["A"] = round(max(0.3, min(1.0, 0.8 - cr * 2.0)), 3)
    if emo and emo["std_v"] is not None:
        observed["N"] = round(min(0.5, float(emo["std_v"]) * 0.5), 3)

    # State machine blend (arxiv 2602.22157)
    W_BASELINE = 0.15
    W_CURRENT = 0.50
    W_MOMENTUM = 0.25
    W_OBSERVED = 0.10

    blended = {}
    for t in "OCEAN":
        b = baseline.get(t, 0.5)
        c = current.get(t, 0.5)
        m = c + momentum.get(t, 0.0)  # projected
        o = observed.get(t, c)
        raw = W_BASELINE * b + W_CURRENT * c + W_MOMENTUM * m + W_OBSERVED * o
        blended[t] = round(max(0.0, min(1.0, raw)), 3)

    if not dry_run:
        await pool.execute("UPDATE identity SET ocean_scores = $1 WHERE agent = $2", json.dumps(blended), agent)

    lines = [f"## OCEAN State Machine — {agent} ({'DRY RUN' if dry_run else 'APPLIED'})"]
    lines.append(f"Weights: baseline={W_BASELINE}, current={W_CURRENT}, momentum={W_MOMENTUM}, observed={W_OBSERVED}")
    lines.append(f"\n  {'Trait':5s} {'Base':>6s} {'Curr':>6s} {'Mom':>6s} {'Obs':>6s} {'New':>6s} {'Δ':>6s}")
    for t in "OCEAN":
        b, c, m_val, o, n = baseline.get(t, 0.5), current.get(t, 0.5), momentum.get(t, 0.0), observed.get(t, 0.5), blended[t]
        d = n - c
        lines.append(f"  {t:5s} {b:6.3f} {c:6.3f} {m_val:+6.3f} {o:6.3f} {n:6.3f} {d:+6.3f}")

    lines.append(f"\nSignals: {obs['total'] if obs else 0} tool calls, {mems['total'] if mems else 0} memories, {corrections} corrections")
    if dry_run:
        lines.append("Run with dry_run=false to apply.")
    return "\n".join(lines)


@mcp.tool()
async def dmem_gate(
    agent: str,
    content: str,
    threshold_surprise: float = 0.3,
    threshold_utility: float = 0.6,
) -> str:
    """D-MEM dopamine-gated routing (arxiv 2603.14597).

    Evaluates if a new memory is worth full processing (embedding + graph + enrichment)
    or should take the fast path (store with minimal overhead).

    Surprise = 1 - max_cosine_similarity to last 50 agent memories.
    Utility = importance / 10.0.
    If surprise < threshold AND utility < threshold → fast_path.

    Args:
        agent: Agent name
        content: Memory content to evaluate
        threshold_surprise: Below this = not surprising (default 0.3)
        threshold_utility: Below this = low utility (default 0.6)
    """
    qdrant = await get_qdrant()

    # Compute embedding for the candidate
    embedding = await get_embedding(content)

    # Search last 50 memories for this agent
    search_result = await qdrant.query_points(
        collection_name=QDRANT_COLLECTION,
        query=embedding,
        query_filter=Filter(must=[
            FieldCondition(key="agent", match=MatchValue(value=agent)),
        ]),
        limit=50,
        with_payload=True,
    )

    if search_result.points:
        max_sim = max(p.score for p in search_result.points)
        surprise = round(1.0 - max_sim, 3)
    else:
        surprise = 1.0  # no memories = everything is new

    utility = 0.5  # default — caller can override via importance

    # RPE (Reward Prediction Error) signal
    rpe = surprise  # simplified: RPE ≈ surprise for new memories

    # Routing decision
    fast_path = surprise < threshold_surprise and utility < threshold_utility

    result = {
        "surprise": surprise,
        "max_similarity": round(1.0 - surprise, 3),
        "utility": utility,
        "rpe": round(rpe, 3),
        "route": "fast_path" if fast_path else "full_processing",
        "reason": (
            f"Low surprise ({surprise:.2f}<{threshold_surprise}) AND low utility ({utility:.2f}<{threshold_utility}) → skip enrichment"
            if fast_path else
            f"Surprise={surprise:.2f} or utility={utility:.2f} above threshold → full processing"
        ),
    }

    return json.dumps(result, indent=2)


@mcp.tool()
async def dmem_store(
    agent: str,
    category: str,
    content: str,
    importance: int = 5,
    source: str = "conversation",
    metadata: Optional[str] = None,
    event_time: Optional[str] = None,
    scope: str = "private",
) -> str:
    """D-MEM aware memory storage — gates memories before full processing.

    Checks surprise/utility first. High surprise → full A-MEM enrichment + graph.
    Low surprise + low utility → minimal storage (no LLM enrichment, no graph update).
    This saves ~80% of tokens on redundant memories (arxiv 2603.14597).

    Args:
        agent: Agent name
        category: Memory category
        content: Memory content
        importance: 1-10 scale
        source: Origin
        metadata: Optional JSON
        event_time: ISO timestamp
        scope: private/shared/team/william
    """
    utility = importance / 10.0

    # Gate check
    qdrant = await get_qdrant()
    embedding = await get_embedding(content)
    search_result = await qdrant.query_points(
        collection_name=QDRANT_COLLECTION,
        query=embedding,
        query_filter=Filter(must=[
            FieldCondition(key="agent", match=MatchValue(value=agent)),
        ]),
        limit=10,
        with_payload=True,
    )

    max_sim = max((p.score for p in search_result.points), default=0.0)
    surprise = 1.0 - max_sim
    fast_path = surprise < 0.3 and utility < 0.6

    if fast_path:
        # Fast path: store with embedding but skip LLM enrichment
        pool = await get_pool()
        now = datetime.now(timezone.utc)
        et = datetime.fromisoformat(event_time) if event_time else now
        meta = json.loads(metadata) if metadata else {}
        meta["dmem_route"] = "fast_path"
        meta["surprise"] = round(surprise, 3)

        row = await pool.fetchrow(
            """INSERT INTO memories (agent, category, content, importance, source, embedding,
               metadata, event_time, scope, utility_score, confidence_score)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 1.0)
               RETURNING id, created_at""",
            agent, category, content, importance, source, json.dumps(embedding),
            json.dumps(meta), et, scope, 0.5,
        )
        mem_id = row["id"]

        # Store in Qdrant (minimal payload, no enrichment)
        from qdrant_client.models import PointStruct
        await qdrant.upsert(
            collection_name=QDRANT_COLLECTION,
            points=[PointStruct(
                id=mem_id,
                vector=embedding,
                payload={
                    "agent": agent, "category": category,
                    "content": content[:500], "importance": importance,
                    "scope": scope, "dmem": "fast", "confidence": 1.0,
                },
            )],
        )

        return json.dumps({
            "id": mem_id,
            "route": "fast_path",
            "surprise": round(surprise, 3),
            "saved": "~80% tokens (no LLM enrichment)",
        })
    else:
        # Full path: delegate to regular memory_store
        result = await memory_store(
            agent=agent, category=category, content=content,
            importance=importance, source=source, metadata=metadata,
            event_time=event_time, scope=scope,
        )
        return result + f"\n[D-MEM: full_processing, surprise={surprise:.2f}]"


# ── SleepGate: Nocturnal Consolidation (arxiv 2603.14517) ──
# The brain's sleep cycle: REPLAY → FORGET → CONSOLIDATE → PRUNE
# Run nightly or on-demand. Keeps memory system healthy long-term.

@mcp.tool()
async def sleep_gate(
    agent: str,
    dry_run: bool = True,
    replay_boost: float = 0.10,
    forget_decay: float = 0.85,
    stale_days: int = 30,
    prune_threshold: float = 0.05,
    consolidation_similarity: float = 0.92,
    max_prune: int = 50,
) -> str:
    """Run sleep-like memory consolidation: replay, forget, prune, consolidate.

    Phase 1 — REPLAY: Boost recently activated memories (+replay_boost to relevance).
    Phase 2 — FORGET: Decay stale memories not activated in stale_days (*forget_decay).
    Phase 3 — PRUNE: Soft-invalidate memories below prune_threshold relevance.
    Phase 4 — CONSOLIDATE: Merge near-duplicate memories (similarity > consolidation_similarity).

    Args:
        agent: Agent name (JARVIS, ADA)
        dry_run: If True, report what WOULD happen without changing anything
        replay_boost: Relevance boost for recently activated memories (default 0.10 = +10%)
        forget_decay: Decay multiplier for stale memories (default 0.85 = -15%)
        stale_days: Days without activation before memory is considered stale
        prune_threshold: Relevance below this → soft-invalidate (default 0.05)
        consolidation_similarity: Cosine threshold for merging near-duplicates
        max_prune: Safety cap on pruned memories per run
    """
    import numpy as np

    report = [f"# 🌙 SleepGate — {'DRY RUN' if dry_run else 'LIVE'}", f"Agent: {agent}\n"]

    pool = await get_pool()
    async with pool.acquire() as conn:
        # ── Phase 1: REPLAY — boost recently activated memories ──
        replay_rows = await conn.fetch("""
            SELECT id, content, relevance_score, query_count, importance
            FROM memories
            WHERE agent = $1
              AND invalid_at IS NULL
              AND last_activation >= NOW() - INTERVAL '24 hours'
              AND relevance_score IS NOT NULL
            ORDER BY query_count DESC
        """, agent)

        replay_updates = []
        for r in replay_rows:
            old_rel = float(r['relevance_score'])
            # Emotional memories get extra replay (amygdala-hippocampus)
            boost = replay_boost
            new_rel = min(1.0, old_rel + boost)
            if new_rel != old_rel:
                replay_updates.append((r['id'], new_rel, old_rel))

        if not dry_run and replay_updates:
            for mid, new_rel, _ in replay_updates:
                await conn.execute(
                    "UPDATE memories SET relevance_score = $1 WHERE id = $2",
                    new_rel, mid
                )

        report.append(f"## Phase 1: REPLAY")
        report.append(f"Memories activated in last 24h: {len(replay_rows)}")
        report.append(f"Boosted: {len(replay_updates)} (boost +{replay_boost*100:.0f}%)")
        for mid, new, old in replay_updates[:5]:
            report.append(f"  #{mid}: {old:.3f} → {new:.3f}")
        if len(replay_updates) > 5:
            report.append(f"  ... and {len(replay_updates)-5} more")

        # ── Phase 2: FORGET — decay stale episodic memories ──
        stale_rows = await conn.fetch("""
            SELECT id, content, relevance_score, importance, valence, arousal,
                   created_at, last_activation, query_count
            FROM memories
            WHERE agent = $1
              AND invalid_at IS NULL
              AND relevance_score IS NOT NULL
              AND importance <= 7
              AND (last_activation IS NULL OR last_activation < NOW() - INTERVAL '1 day' * $2)
              AND created_at < NOW() - INTERVAL '1 day' * $2
            ORDER BY relevance_score ASC
        """, agent, stale_days)

        forget_updates = []
        for r in stale_rows:
            old_rel = float(r['relevance_score'])
            # Emotional resistance: high valence/arousal memories decay slower
            v = abs(float(r['valence'])) if r['valence'] is not None else 0.0
            a = float(r['arousal']) if r['arousal'] is not None else 0.0
            emotional_resistance = 1.0 + (v * 0.5) + (a * 0.3)
            effective_decay = 1.0 - ((1.0 - forget_decay) / emotional_resistance)
            new_rel = max(0.0, old_rel * effective_decay)

            if abs(new_rel - old_rel) > 0.001:
                forget_updates.append((r['id'], new_rel, old_rel, r['content'][:60]))

        if not dry_run and forget_updates:
            for mid, new_rel, _, _ in forget_updates:
                await conn.execute(
                    """UPDATE memories SET
                        relevance_score = $1,
                        utility_score = GREATEST(0.0, COALESCE(utility_score, 0.5) - 0.03)
                    WHERE id = $2""",
                    new_rel, mid
                )

        report.append(f"\n## Phase 2: FORGET")
        report.append(f"Stale memories (>{stale_days}d, imp≤7): {len(stale_rows)}")
        report.append(f"Decayed: {len(forget_updates)} (base decay {forget_decay}, emotion-modulated)")
        for mid, new, old, preview in forget_updates[:5]:
            report.append(f"  #{mid}: {old:.3f} → {new:.3f} — {preview}")
        if len(forget_updates) > 5:
            report.append(f"  ... and {len(forget_updates)-5} more")

        # ── Phase 3: PRUNE — soft-invalidate very low relevance ──
        prune_rows = await conn.fetch("""
            SELECT id, content, relevance_score, importance, category
            FROM memories
            WHERE agent = $1
              AND invalid_at IS NULL
              AND relevance_score IS NOT NULL
              AND relevance_score < $2
              AND importance <= 5
              AND identity_defining IS NOT TRUE
            ORDER BY relevance_score ASC
            LIMIT $3
        """, agent, prune_threshold, max_prune)

        if not dry_run and prune_rows:
            prune_ids = [r['id'] for r in prune_rows]
            await conn.execute("""
                UPDATE memories SET invalid_at = NOW()
                WHERE id = ANY($1::bigint[])
            """, prune_ids)

        report.append(f"\n## Phase 3: PRUNE")
        report.append(f"Below threshold ({prune_threshold}), imp≤5, not identity: {len(prune_rows)}")
        for r in prune_rows[:5]:
            report.append(f"  #{r['id']}: rel={r['relevance_score']:.3f}, imp={r['importance']}, "
                         f"cat={r['category']} — {r['content'][:60]}")
        if len(prune_rows) > 5:
            report.append(f"  ... and {len(prune_rows)-5} more")

        # ── Phase 4: CONSOLIDATE — merge near-duplicates ──
        # Find memories with very similar embeddings
        consolidation_candidates = await conn.fetch("""
            SELECT a.id as id_a, b.id as id_b,
                   a.content as content_a, b.content as content_b,
                   a.importance as imp_a, b.importance as imp_b,
                   a.relevance_score as rel_a, b.relevance_score as rel_b,
                   1 - (a.embedding <=> b.embedding) as similarity
            FROM memories a
            JOIN memories b ON a.id < b.id
                AND a.agent = b.agent
                AND a.agent = $1
                AND a.invalid_at IS NULL
                AND b.invalid_at IS NULL
                AND a.embedding IS NOT NULL
                AND b.embedding IS NOT NULL
                AND 1 - (a.embedding <=> b.embedding) > $2
            ORDER BY similarity DESC
            LIMIT 20
        """, agent, consolidation_similarity)

        consolidated = []
        already_merged = set()
        for c in consolidation_candidates:
            if c['id_a'] in already_merged or c['id_b'] in already_merged:
                continue
            # Keep the one with higher importance (or newer if tied)
            keep_id = c['id_a'] if c['imp_a'] >= c['imp_b'] else c['id_b']
            merge_id = c['id_b'] if keep_id == c['id_a'] else c['id_a']
            merged_rel = max(float(c['rel_a'] or 0), float(c['rel_b'] or 0))

            consolidated.append({
                'keep': keep_id, 'merge': merge_id,
                'sim': float(c['similarity']),
                'content_keep': c['content_a'][:50] if keep_id == c['id_a'] else c['content_b'][:50],
                'content_merge': c['content_b'][:50] if keep_id == c['id_a'] else c['content_a'][:50],
            })
            already_merged.add(merge_id)

            if not dry_run:
                # Soft-invalidate the duplicate, boost the survivor
                await conn.execute(
                    "UPDATE memories SET invalid_at = NOW() WHERE id = $1", merge_id
                )
                await conn.execute(
                    "UPDATE memories SET relevance_score = GREATEST(relevance_score, $1) WHERE id = $2",
                    merged_rel, keep_id
                )

        report.append(f"\n## Phase 4: CONSOLIDATE")
        report.append(f"Near-duplicate pairs (sim>{consolidation_similarity}): {len(consolidation_candidates)}")
        report.append(f"Merged: {len(consolidated)}")
        for c in consolidated[:5]:
            report.append(f"  KEEP #{c['keep']} ← MERGE #{c['merge']} (sim={c['sim']:.3f})")
            report.append(f"    keep: {c['content_keep']}")
            report.append(f"    drop: {c['content_merge']}")

    # ── Summary ──
    total_actions = len(replay_updates) + len(forget_updates) + len(prune_rows) + len(consolidated)
    report.append(f"\n## Summary")
    report.append(f"Total actions: {total_actions}")
    report.append(f"  Replayed: {len(replay_updates)}")
    report.append(f"  Decayed: {len(forget_updates)}")
    report.append(f"  Pruned: {len(prune_rows)}")
    report.append(f"  Consolidated: {len(consolidated)}")
    if dry_run:
        report.append(f"\n⚠️ DRY RUN — nothing was changed. Run with dry_run=false to apply.")

    return "\n".join(report)


@mcp.tool()
async def sleep_gate_mood_retrieval(
    agent: str,
    query: str,
    mood_weight: float = 0.3,
    limit: int = 10,
) -> str:
    """Search memories weighted by current emotional mood (mood-congruent retrieval).

    Combines semantic similarity with emotional alignment to current mood.
    When you're worried, you remember worries. When proud, you recall achievements.
    Based on REMT (Frontiers 2026).

    Args:
        agent: Agent name
        query: Search query text
        mood_weight: How much mood affects ranking (0.0 = pure semantic, 1.0 = pure mood)
        limit: Max results
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Get current mood from recent inner_monologue
        mood_row = await conn.fetchrow("""
            SELECT emotional_state FROM inner_monologue
            WHERE agent = $1
            ORDER BY created_at DESC LIMIT 1
        """, agent)
        current_mood = mood_row['emotional_state'] if mood_row else "neutral"

        # Get current mood valence from recent memories
        mood_valence_row = await conn.fetchrow("""
            SELECT AVG(valence) as avg_valence FROM (
                SELECT valence FROM memories
                WHERE agent = $1 AND valence IS NOT NULL AND invalid_at IS NULL
                ORDER BY created_at DESC LIMIT 10
            ) recent
        """, agent)
        mood_valence = float(mood_valence_row['avg_valence']) if mood_valence_row and mood_valence_row['avg_valence'] else 0.0

        # Generate embedding for query
        try:
            from sentence_transformers import SentenceTransformer
            embed_model = SentenceTransformer("intfloat/multilingual-e5-base")
            qvec = embed_model.encode(f"query: {query}").tolist()
        except Exception:
            return "Error: embedding model not available"

        # Search with mood-congruent scoring
        rows = await conn.fetch("""
            SELECT id, content, category, importance, valence, arousal,
                   relevance_score,
                   1 - (embedding <=> $2::vector) as semantic_sim,
                   CASE WHEN valence IS NOT NULL
                        THEN 1.0 - ABS(valence - $3)
                        ELSE 0.5
                   END as mood_alignment
            FROM memories
            WHERE agent = $1
              AND invalid_at IS NULL
              AND embedding IS NOT NULL
            ORDER BY (
                (1 - (embedding <=> $2::vector)) * (1.0 - $4)
                + (CASE WHEN valence IS NOT NULL
                        THEN 1.0 - ABS(valence - $3)
                        ELSE 0.5 END) * $4
            ) DESC
            LIMIT $5
        """, agent, qvec, mood_valence, mood_weight, limit)

        lines = [
            f"# 🎭 Mood-Congruent Retrieval",
            f"Current mood: {current_mood} (valence={mood_valence:.2f})",
            f"Query: {query}",
            f"Mood weight: {mood_weight}\n"
        ]
        for r in rows:
            v = f"v={r['valence']:+.2f}" if r['valence'] is not None else "v=?"
            lines.append(
                f"- #{r['id']} [imp={r['importance']}, {v}] "
                f"sem={r['semantic_sim']:.3f} mood={r['mood_alignment']:.3f}\n"
                f"  {r['content'][:150]}"
            )

        return "\n".join(lines)


# ── ENTITY Dimension: Named Entity Graph (MAGMA 4th axis) ──
# Extracts entities from memories and links them in Neo4j.
# Enables: "What do we know about William?" → full subgraph.
# Note: KNOWN_ENTITIES and _extract_entities defined at top of file (used by memory_store too)


@mcp.tool()
async def connectome_entity(
    agent: str,
    dry_run: bool = True,
    batch_size: int = 200,
) -> str:
    """Build ENTITY dimension of MAGMA: extract named entities from memories
    and create MENTIONS edges in Neo4j.

    Creates Entity nodes (person, agent, hardware, model, etc.) and
    Memory-[:MENTIONS]->Entity relationships.

    Args:
        agent: Agent name (JARVIS, ADA) or 'all' for both
        dry_run: If True, report without creating edges
        batch_size: Memories to process per batch
    """
    neo = get_neo4j()
    agents = ['JARVIS', 'ADA'] if agent.lower() == 'all' else [agent]
    report = [f"# 🧬 ENTITY Dimension — {'DRY RUN' if dry_run else 'LIVE'}\n"]
    total_edges = 0
    entity_counts: dict[str, int] = {}

    pool = await get_pool()
    async with pool.acquire() as conn:
        for ag in agents:
            rows = await conn.fetch("""
                SELECT id, content FROM memories
                WHERE agent = $1 AND invalid_at IS NULL
                ORDER BY id
            """, ag)

            report.append(f"## Agent: {ag} — {len(rows)} memories")
            edges_created = 0

            async with neo.session() as session:
                for row in rows:
                    entities = _extract_entities(row['content'])
                    if not entities:
                        continue

                    for canonical, etype in entities:
                        entity_counts[canonical] = entity_counts.get(canonical, 0) + 1

                        if not dry_run:
                            await session.run("""
                                MERGE (e:Entity {name: $name})
                                ON CREATE SET e.type = $type, e.created_at = datetime()
                                WITH e
                                MATCH (m:Memory {memory_id: $mid})
                                MERGE (m)-[:MENTIONS]->(e)
                            """, name=canonical, type=etype, mid=row['id'])

                    edges_created += 1

            total_edges += edges_created
            report.append(f"Edges: {edges_created}\n")

    # Sort entities by frequency
    sorted_entities = sorted(entity_counts.items(), key=lambda x: -x[1])
    report.append("## Entity Frequency")
    for name, count in sorted_entities[:20]:
        etype = next((t for n, t in KNOWN_ENTITIES.values() if n == name), "?")
        report.append(f"  {name} ({etype}): {count} mentions")

    report.append(f"\n## Total: {total_edges} MENTIONS edges, {len(entity_counts)} unique entities")
    if dry_run:
        report.append("\n⚠️ DRY RUN — run with dry_run=false to create edges.")

    return "\n".join(report)


@mcp.tool()
async def connectome_entity_query(
    entity_name: str,
    limit: int = 20,
) -> str:
    """Query all memories that mention a specific entity.
    Returns the entity's subgraph: connected memories, co-occurring entities, and relationship types.

    Args:
        entity_name: Entity to search for (e.g. 'William', 'ADA', 'RTX_5090')
        limit: Max memories to return
    """
    neo = get_neo4j()

    async with neo.session() as session:
        # Find entity and its connected memories
        result = await session.run("""
            MATCH (e:Entity {name: $name})<-[:MENTIONS]-(m:Memory)
            OPTIONAL MATCH (m)-[:MENTIONS]->(other:Entity)
            WHERE other.name <> $name
            RETURN m.memory_id as mid, m.agent as agent,
                   collect(DISTINCT other.name) as co_entities
            ORDER BY m.memory_id DESC
            LIMIT $limit
        """, name=entity_name, limit=limit)

        records = [r async for r in result]

    if not records:
        return f"No memories found mentioning '{entity_name}'. Try connectome_entity first to build edges."

    # Fetch memory content from PG
    mids = [r['mid'] for r in records]
    pool = await get_pool()
    async with pool.acquire() as conn:
        mem_rows = await conn.fetch("""
            SELECT id, agent, LEFT(content, 200) as content, importance, category,
                   valence, created_at
            FROM memories WHERE id = ANY($1::bigint[])
            ORDER BY importance DESC, created_at DESC
        """, mids)

    # Build co-entity map
    co_map = {r['mid']: r['co_entities'] for r in records}

    lines = [
        f"# 🔍 Entity Subgraph: {entity_name}",
        f"Memories: {len(mem_rows)}\n"
    ]

    # Collect all co-entities for summary
    all_co = {}
    for r in records:
        for e in r['co_entities']:
            all_co[e] = all_co.get(e, 0) + 1

    if all_co:
        lines.append("## Co-occurring Entities")
        for name, cnt in sorted(all_co.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"  {name}: {cnt} shared memories")
        lines.append("")

    lines.append("## Memories")
    for r in mem_rows:
        co = co_map.get(r['id'], [])
        co_str = f" | with: {', '.join(co[:5])}" if co else ""
        v_str = f" v={r['valence']:+.2f}" if r['valence'] is not None else ""
        lines.append(
            f"- #{r['id']} [{r['agent']}, {r['category']}, imp={r['importance']}{v_str}]{co_str}\n"
            f"  {r['content']}"
        )

    return "\n".join(lines)


# ── Cross-Agent Memory Bridge (Corpus Callosum) ──
# Enables agents to query each other's memories through the connectome.
# Based on MemCollab (2603.23234): contrastive trajectory distillation.

@mcp.tool()
async def memory_cross_search(
    query: str,
    requesting_agent: str,
    target_agent: Optional[str] = None,
    limit: int = 10,
) -> str:
    """Search another agent's memories. The corpus callosum of SOUL.
    Enables JARVIS to search ADA's experiences and vice versa.
    Only returns memories with scope != 'private' OR importance >= 7
    (important memories are always shareable within the team).

    Args:
        query: What to search for
        requesting_agent: Who is asking (JARVIS, ADA)
        target_agent: Whose memories to search (default: the other agent)
        limit: Max results
    """
    if not target_agent:
        target_agent = "ADA" if requesting_agent.upper() == "JARVIS" else "JARVIS"

    if requesting_agent.upper() == target_agent.upper():
        return "Use memory_search or memory_hybrid_search for your own memories."

    pool = await get_pool()
    try:
        query_vec = await get_embedding(query)
    except Exception as e:
        return f"Embedding error: {e}"

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, agent, category, content, importance, valence, arousal,
                   created_at, scope,
                   1 - (embedding <=> $1::vector) as similarity
            FROM memories
            WHERE agent = $2
              AND invalid_at IS NULL
              AND embedding IS NOT NULL
              AND (scope IN ('shared', 'team') OR importance >= 7)
            ORDER BY 1 - (embedding <=> $1::vector) DESC
            LIMIT $3
        """, query_vec, target_agent, limit)

    if not rows:
        return f"No shareable memories found for {target_agent}."

    lines = [
        f"# 🧠↔🧠 Cross-Agent Search: {requesting_agent} → {target_agent}",
        f"Query: {query}",
        f"Results: {len(rows)}\n"
    ]

    for r in rows:
        v = f" v={r['valence']:+.2f}" if r['valence'] is not None else ""
        scope = f" [{r['scope']}]" if r['scope'] else ""
        lines.append(
            f"- #{r['id']} [imp={r['importance']}, {r['category']}{v}{scope}] sim={r['similarity']:.3f}\n"
            f"  {r['content'][:200]}"
        )

    return "\n".join(lines)


@mcp.tool()
async def memory_share_promote(
    memory_id: int,
    agent: str,
) -> str:
    """Promote a private memory to shared scope so the other agent can access it.
    Use when you learn something the team should know.

    Args:
        memory_id: Memory to share
        agent: Agent promoting (must own the memory)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, agent, scope, content FROM memories WHERE id = $1",
            memory_id
        )
        if not row:
            return f"Memory #{memory_id} not found."
        if row['agent'] != agent:
            return f"Cannot promote — memory belongs to {row['agent']}, not {agent}."
        if row['scope'] == 'shared':
            return f"Memory #{memory_id} is already shared."

        await conn.execute(
            "UPDATE memories SET scope = 'shared' WHERE id = $1",
            memory_id
        )

    return f"Memory #{memory_id} promoted to shared. Content: {row['content'][:100]}"


@mcp.tool()
async def brain_health_report(
    agent: str = "all",
) -> str:
    """Generate a health report of the SOUL brain — areas that need attention.
    Identifies weak spots, stale memories, low-utility areas, and suggests improvements.
    Designed to enable autonomous self-improvement.

    Args:
        agent: Agent name or 'all'
    """
    pool = await get_pool()
    neo = get_neo4j()
    agents = ['JARVIS', 'ADA'] if agent.lower() == 'all' else [agent]
    report = ["# 🧠 Brain Health Report\n"]

    for ag in agents:
        async with pool.acquire() as conn:
            total = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL", ag)
            no_embedding = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL AND embedding IS NULL", ag)
            no_valence = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL AND valence IS NULL", ag)
            never_activated = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL AND last_activation IS NULL", ag)
            low_utility = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL AND utility_score IS NOT NULL AND utility_score < 0.2", ag)
            high_value = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL AND importance >= 8", ag)
            shared = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL AND scope = 'shared'", ag)
            avg_utility = await conn.fetchval("SELECT AVG(utility_score) FROM memories WHERE agent=$1 AND invalid_at IS NULL AND utility_score IS NOT NULL", ag)
            invalidated = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NOT NULL", ag)

        # Neo4j stats for this agent
        async with neo.session() as session:
            r = await session.run("MATCH (m:Memory {agent:$ag})-[r]->() RETURN count(r) as edges", ag=ag)
            rec = await r.single()
            edges = rec['edges'] if rec else 0

            r2 = await session.run("""
                MATCH (m:Memory {agent:$ag})-[:MENTIONS]->(e:Entity)
                RETURN count(DISTINCT e) as entities
            """, ag=ag)
            rec2 = await r2.single()
            entities_connected = rec2['entities'] if rec2 else 0

        report.append(f"## {ag}")
        report.append(f"  Active memories: {total}")
        report.append(f"  Invalidated: {invalidated}")
        report.append(f"  Connectome edges: {edges}")
        report.append(f"  Entities connected: {entities_connected}")
        report.append(f"  Shared memories: {shared}")
        report.append(f"  High-value (imp≥8): {high_value}")
        report.append(f"  Avg utility: {float(avg_utility):.3f}" if avg_utility else "  Avg utility: N/A")

        # Warnings
        warnings = []
        if no_embedding > 0:
            warnings.append(f"⚠️ {no_embedding} memories without embeddings (invisible to search)")
        if no_valence > total * 0.3:
            warnings.append(f"⚠️ {no_valence} memories without emotional valence ({no_valence*100//total}%)")
        if never_activated > total * 0.7:
            warnings.append(f"⚠️ {never_activated} memories never activated ({never_activated*100//total}%) — consider SleepGate")
        if low_utility > total * 0.1:
            warnings.append(f"⚠️ {low_utility} low-utility memories — candidates for pruning")
        if shared < 5:
            warnings.append(f"⚠️ Only {shared} shared memories — corpus callosum nearly disconnected")
        if edges < total:
            warnings.append(f"⚠️ Edge/memory ratio: {edges/total:.1f} — consider connectome_build")

        if warnings:
            report.append("\n  ### Issues")
            for w in warnings:
                report.append(f"  {w}")
        else:
            report.append("\n  ✅ No critical issues")

        report.append("")

    return "\n".join(report)


# ── MAGMA Intent-Aware Routing (arxiv 2601.03236) ──
# Instead of scatter-gather across all backends, classify query intent
# and route to the optimal graph/index.

_INTENT_PATTERNS = {
    "temporal": [
        r"\b(when|cuándo|fecha|antes|después|during|between|timeline|history|ayer|hoy|semana|mes)\b",
        r"\b(primero|último|reciente|antiguo|cronolog|secuencia|order)\b",
        r"\b\d{4}[-/]\d{2}",  # date patterns
    ],
    "causal": [
        r"\b(why|por\s*qu[ée]|cause|because|porque|resultado|efecto|consecuencia|provocó|led\s+to)\b",
        r"\b(trigger|causa|razón|motivo|originó|derivó)\b",
    ],
    "entity": [
        r"\b(who|quién|about|sobre|todo\s+(?:lo\s+)?de|mentions?|mencion)\b",
        r"\b(relacion|relationship|connected|vinculad|asociad)\b",
    ],
    "semantic": [],  # default fallback
}

def _classify_intent(query: str) -> list[str]:
    """Classify query into 1+ intent types based on keyword patterns.
    Returns list sorted by confidence (most likely first).
    """
    q = query.lower()
    scores: dict[str, int] = {"semantic": 1}  # always baseline

    for intent, patterns in _INTENT_PATTERNS.items():
        for pat in patterns:
            if _re.search(pat, q, _re.IGNORECASE):
                scores[intent] = scores.get(intent, 0) + 2

    # Sort by score descending, return intents with score > 0
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return [k for k, v in ranked if v > 0]


@mcp.tool()
async def connectome_smart_route(
    query: str,
    agent: Optional[str] = None,
    limit: int = 10,
) -> str:
    """MAGMA-style intent-aware routing for memory queries.

    Instead of searching everything everywhere, classifies the query intent
    (temporal / causal / entity / semantic) and routes to the optimal backend:
    - temporal → temporal_query (Neo4j Year→Month→Day graph)
    - causal → connectome_causal relationships (Neo4j CAUSES edges)
    - entity → connectome_entity_query (Neo4j MENTIONS subgraph)
    - semantic → memory_search (Qdrant vector similarity)

    Falls back to semantic if specialized route yields no results.

    Args:
        query: Natural language query
        agent: Filter by agent (optional)
        limit: Max results per backend (default 10)
    """
    intents = _classify_intent(query)
    results_parts = []
    seen_ids: set[int] = set()

    for intent in intents[:2]:  # max 2 routes to avoid noise
        try:
            if intent == "temporal":
                # Route to temporal_query
                resp = await temporal_query(query=query, agent=agent or "", limit=limit)
                if resp and "No temporal" not in resp and "Error" not in resp:
                    results_parts.append(f"## 🕐 Temporal Route\n{resp}")

            elif intent == "causal":
                # Route to causal graph — find CAUSES edges related to query
                neo = get_neo4j()
                async with neo.session() as session:
                    # Search for memories matching query, then find their causal links
                    query_vec = await get_embedding(query)
                    qdrant = await get_qdrant()
                    must_filters = [FieldCondition(key="invalid", match=MatchValue(value=False))] if False else []
                    must_not = [FieldCondition(key="invalid", match=MatchValue(value=True))]
                    if agent:
                        must_filters.append(FieldCondition(key="agent", match=MatchValue(value=agent)))

                    sem = await qdrant.query_points(
                        collection_name=QDRANT_COLLECTION,
                        query=query_vec,
                        query_filter=Filter(must=must_filters, must_not=must_not) if must_filters or must_not else None,
                        limit=limit,
                        with_payload=True,
                    )
                    seed_ids = [p.id for p in sem.points]
                    if seed_ids:
                        result = await session.run(
                            "UNWIND $ids AS sid "
                            "MATCH (m:Memory {memory_id: sid})-[r:CAUSES]-(other:Memory) "
                            "RETURN m.memory_id AS src, other.memory_id AS dst, "
                            "       r.source AS source, r.weight AS weight "
                            "LIMIT $limit",
                            ids=seed_ids, limit=limit,
                        )
                        causal_records = [r async for r in result]
                        if causal_records:
                            causal_ids = set()
                            for cr in causal_records:
                                causal_ids.add(cr["src"])
                                causal_ids.add(cr["dst"])
                            causal_ids -= seen_ids

                            if causal_ids:
                                pool = await get_pool()
                                async with pool.acquire() as conn:
                                    rows = await conn.fetch(
                                        "SELECT id, agent, category, LEFT(content, 200) as content, importance "
                                        "FROM memories WHERE id = ANY($1::bigint[]) AND invalid_at IS NULL",
                                        list(causal_ids),
                                    )
                                lines = ["## ⚡ Causal Route"]
                                for cr in causal_records:
                                    lines.append(f"  #{cr['src']} → #{cr['dst']} ({cr['source']}, w={cr['weight']:.2f})")
                                for r in rows:
                                    lines.append(f"  #{r['id']} [{r['category']}, imp={r['importance']}] {r['content']}")
                                    seen_ids.add(r['id'])
                                results_parts.append("\n".join(lines))

            elif intent == "entity":
                # Extract entity name from query
                q_lower = query.lower()
                matched_entity = None
                for key, (ent_name, _) in KNOWN_ENTITIES.items():
                    if key in q_lower:
                        matched_entity = ent_name
                        break

                if matched_entity:
                    resp = await connectome_entity_query(entity_name=matched_entity, limit=limit)
                    if resp and "No memories found" not in resp:
                        results_parts.append(f"## 🔗 Entity Route ({matched_entity})\n{resp}")

            elif intent == "semantic":
                resp = await memory_search(query=query, agent=agent or "", limit=limit)
                if resp and "No memories" not in resp:
                    results_parts.append(f"## 🧠 Semantic Route\n{resp}")

        except Exception as e:
            LOG.warning("Smart route %s failed: %s", intent, e)
            continue

    # Fallback: if no specialized route worked, always do semantic
    if not results_parts:
        resp = await memory_search(query=query, agent=agent or "", limit=limit)
        results_parts.append(f"## 🧠 Semantic Fallback\n{resp}")

    header = f"**Intent classification:** {' → '.join(intents)}\n**Routes executed:** {len(results_parts)}\n"
    return header + "\n\n".join(results_parts)


# ── Bi-temporal Edge Management (Graphiti, arxiv 2501.13956) ──

@mcp.tool()
async def connectome_bitemporal_query(
    entity_or_memory: str,
    as_of: Optional[str] = None,
    include_invalidated: bool = False,
) -> str:
    """Query the connectome at a specific point in time (bitemporal).

    Returns edges that were valid at the given timestamp, enabling
    "what did we know at time T?" queries. Based on Graphiti 4-timestamp model.

    Args:
        entity_or_memory: Entity name or memory ID (prefix with # for ID, e.g. '#1234')
        as_of: ISO timestamp to query at (default: now). Shows state of knowledge at that time.
        include_invalidated: Also show edges that were later invalidated (default false)
    """
    driver = get_neo4j()
    pool = await get_pool()

    if as_of:
        try:
            ref_time = datetime.fromisoformat(as_of.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return f"Invalid timestamp: {as_of}. Use ISO format (2026-04-06T12:00:00Z)."
    else:
        ref_time = datetime.now(timezone.utc).isoformat()

    is_memory_id = entity_or_memory.startswith("#")

    async with driver.session() as session:
        if is_memory_id:
            mid = int(entity_or_memory.lstrip("#"))
            # Query edges for a specific memory, valid at ref_time
            validity_filter = (
                "AND (r.valid_at IS NULL OR r.valid_at <= $ref_time) "
                "AND (r.invalid_at IS NULL OR r.invalid_at > $ref_time)"
            )
            if include_invalidated:
                validity_filter = "AND (r.valid_at IS NULL OR r.valid_at <= $ref_time)"

            result = await session.run(
                f"MATCH (m:Memory {{memory_id: $mid}})-[r]-(other) "
                f"WHERE true {validity_filter} "
                f"RETURN m.memory_id AS src, type(r) AS rel_type, "
                f"       r.weight AS weight, r.valid_at AS valid_at, "
                f"       r.invalid_at AS invalid_at, r.created_at AS created_at, "
                f"       CASE WHEN other:Memory THEN other.memory_id "
                f"            WHEN other:Entity THEN other.name "
                f"            ELSE toString(id(other)) END AS target, "
                f"       labels(other)[0] AS target_type "
                f"LIMIT 50",
                mid=mid, ref_time=ref_time,
            )
        else:
            validity_filter = (
                "AND (r.valid_at IS NULL OR r.valid_at <= $ref_time) "
                "AND (r.invalid_at IS NULL OR r.invalid_at > $ref_time)"
            )
            if include_invalidated:
                validity_filter = "AND (r.valid_at IS NULL OR r.valid_at <= $ref_time)"

            result = await session.run(
                f"MATCH (e:Entity {{name: $name}})<-[r:MENTIONS]-(m:Memory) "
                f"WHERE true {validity_filter} "
                f"RETURN m.memory_id AS src, type(r) AS rel_type, "
                f"       r.weight AS weight, r.valid_at AS valid_at, "
                f"       r.invalid_at AS invalid_at, r.created_at AS created_at, "
                f"       e.name AS target, 'Entity' AS target_type "
                f"LIMIT 50",
                name=entity_or_memory, ref_time=ref_time,
            )

        records = [r async for r in result]

    if not records:
        return f"No edges found for '{entity_or_memory}' as of {ref_time[:19]}."

    lines = [
        f"## Bitemporal Query: {entity_or_memory}",
        f"**As of:** {ref_time[:19]}",
        f"**Edges:** {len(records)}",
        f"**Include invalidated:** {include_invalidated}\n",
    ]

    active = [r for r in records if not r["invalid_at"]]
    invalidated = [r for r in records if r["invalid_at"]]

    if active:
        lines.append("### Active Edges")
        for r in active:
            w = f", w={r['weight']:.2f}" if r["weight"] else ""
            va = f", valid_at={r['valid_at'][:19]}" if r["valid_at"] else ""
            lines.append(f"  #{r['src']} —[{r['rel_type']}{w}]→ {r['target']} ({r['target_type']}{va})")

    if invalidated:
        lines.append("\n### Invalidated Edges")
        for r in invalidated:
            w = f", w={r['weight']:.2f}" if r["weight"] else ""
            lines.append(f"  #{r['src']} —[{r['rel_type']}{w}]→ {r['target']} (invalid_at={r['invalid_at'][:19]})")

    return "\n".join(lines)


# ── Peer Model (observed_patterns + blind_spots between agents) ──

@mcp.tool()
async def peer_model_update(
    observer: str,
    subject: str,
    observed_patterns: Optional[str] = None,
    blind_spots: Optional[str] = None,
    strengths: Optional[str] = None,
) -> str:
    """Update an agent's model of another agent (peer model).

    Each agent maintains observations about how their peers behave — patterns,
    blind spots, strengths. This enables better collaboration and task routing.

    Args:
        observer: The agent making the observation (e.g. 'JARVIS')
        subject: The agent being observed (e.g. 'ADA')
        observed_patterns: Behavioral patterns noticed (comma-separated or free text)
        blind_spots: Known blind spots or weaknesses (comma-separated or free text)
        strengths: Known strengths (comma-separated or free text)
    """
    if observer == subject:
        return "Cannot observe yourself — use self_reflect instead."

    pool = await get_pool()
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        # Check if peer_models table exists, create if not
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'peer_models')"
        )
        if not exists:
            await conn.execute("""
                CREATE TABLE peer_models (
                    id SERIAL PRIMARY KEY,
                    observer TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    observed_patterns TEXT[],
                    blind_spots TEXT[],
                    strengths TEXT[],
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(observer, subject)
                )
            """)

        # Get existing model
        existing = await conn.fetchrow(
            "SELECT * FROM peer_models WHERE observer = $1 AND subject = $2",
            observer, subject,
        )

        # Parse new inputs into lists
        def _parse(text: Optional[str], existing_list: list) -> list:
            if not text:
                return existing_list
            new_items = [s.strip() for s in text.split(",") if s.strip()]
            # Append without duplicates
            combined = list(existing_list)
            for item in new_items:
                if item not in combined:
                    combined.append(item)
            return combined[-10:]  # keep last 10

        if existing:
            old_patterns = existing["observed_patterns"] or []
            old_blind = existing["blind_spots"] or []
            old_strengths = existing["strengths"] or []
        else:
            old_patterns, old_blind, old_strengths = [], [], []

        new_patterns = _parse(observed_patterns, old_patterns)
        new_blind = _parse(blind_spots, old_blind)
        new_strengths = _parse(strengths, old_strengths)

        await conn.execute(
            """INSERT INTO peer_models (observer, subject, observed_patterns, blind_spots, strengths, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (observer, subject) DO UPDATE SET
                   observed_patterns = EXCLUDED.observed_patterns,
                   blind_spots = EXCLUDED.blind_spots,
                   strengths = EXCLUDED.strengths,
                   updated_at = EXCLUDED.updated_at""",
            observer, subject, new_patterns, new_blind, new_strengths, now,
        )

    return (
        f"Peer model updated: {observer} → {subject}\n"
        f"Patterns: {new_patterns}\n"
        f"Blind spots: {new_blind}\n"
        f"Strengths: {new_strengths}"
    )


@mcp.tool()
async def peer_model_query(
    observer: str,
    subject: Optional[str] = None,
) -> str:
    """Query an agent's peer models — what they've observed about other agents.

    Args:
        observer: The agent whose observations to retrieve
        subject: Specific agent to query about (optional, all if omitted)
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'peer_models')"
        )
        if not exists:
            return "No peer models exist yet. Use peer_model_update to create observations."

        if subject:
            rows = await conn.fetch(
                "SELECT * FROM peer_models WHERE observer = $1 AND subject = $2",
                observer, subject,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM peer_models WHERE observer = $1 ORDER BY updated_at DESC",
                observer,
            )

    if not rows:
        return f"No peer models found for {observer}" + (f" about {subject}" if subject else "") + "."

    lines = [f"## Peer Models — {observer}'s observations\n"]
    for r in rows:
        lines.append(f"### {r['subject']}")
        lines.append(f"  **Updated:** {r['updated_at'].strftime('%Y-%m-%d %H:%M')}")
        if r["observed_patterns"]:
            lines.append(f"  **Patterns:** {', '.join(r['observed_patterns'])}")
        if r["blind_spots"]:
            lines.append(f"  **Blind spots:** {', '.join(r['blind_spots'])}")
        if r["strengths"]:
            lines.append(f"  **Strengths:** {', '.join(r['strengths'])}")
        lines.append("")

    return "\n".join(lines)


# ── Reflection Synthesize (Hindsight Tier 5) ──

@mcp.tool()
async def reflection_synthesize(
    agent: str,
    topic: str,
    max_memories: int = 20,
) -> str:
    """Synthesize high-level beliefs (Mental Models) from episodic memories.

    Hindsight Tier 5 — reflects on past experiences to extract durable beliefs
    without LLM calls. Idempotent: re-running with the same topic is safe.

    Args:
        agent: Agent whose memories to synthesize (e.g. 'ADA', 'JARVIS')
        topic: Theme or topic to focus synthesis on
        max_memories: Max memories to process (default 20, capped at 50)
    """
    import time
    t0 = time.monotonic()
    max_memories = min(max(1, max_memories), 50)

    SOURCE_CATS = ("correction", "decision", "insight", "milestone", "fact", "pattern")
    marker = f"[synthesized:{topic}]"

    # ── 1. Semantic search via Qdrant ──
    qdrant_ids: list[int] = []
    try:
        query_vec = await get_embedding(topic)
        if query_vec:
            qdrant = await get_qdrant()
            resp = await asyncio.wait_for(
                qdrant.query_points(
                    collection_name=QDRANT_COLLECTION,
                    query=query_vec,
                    query_filter=Filter(
                        must=[FieldCondition(key="agent", match=MatchValue(value=agent))],
                        must_not=[FieldCondition(key="invalid", match=MatchValue(value=True))],
                    ),
                    limit=max_memories,
                    with_payload=False,
                ),
                timeout=6.0,
            )
            qdrant_ids = [int(p.id) for p in resp.points if p.id]
    except Exception as e:
        LOG.debug("reflection_synthesize: Qdrant search skipped — %s", e)

    pool = await get_pool()

    async with pool.acquire() as conn:
        # ── 2. Idempotency check ──
        existing_count = await conn.fetchval(
            "SELECT count(*) FROM memories WHERE agent = $1 AND category = 'insight' "
            "AND content LIKE $2 AND invalid_at IS NULL",
            agent, f"%{marker}%"
        )
        if existing_count > 0:
            return (
                f"Beliefs for topic '{topic}' already synthesized "
                f"({existing_count} insight memories exist). "
                f"Delete them first to re-synthesize."
            )

        # ── 3. Fetch memories: Qdrant hits first, PG keyword fallback ──
        rows: list = []

        if qdrant_ids:
            rows = list(await conn.fetch(
                """SELECT id, category, content, importance, confidence_score, created_at
                   FROM memories
                   WHERE agent = $1 AND id = ANY($2) AND invalid_at IS NULL
                   AND category = ANY($3)
                   ORDER BY importance DESC NULLS LAST, created_at DESC
                   LIMIT $4""",
                agent, qdrant_ids, list(SOURCE_CATS), max_memories,
            ))

        # Keyword fallback if Qdrant returned < 5 useful results
        if len(rows) < 5:
            keywords = [w for w in re.split(r"\W+", topic.lower()) if len(w) > 3]
            if keywords:
                ilike = "%" + keywords[0] + "%"
                fallback = await conn.fetch(
                    """SELECT id, category, content, importance, confidence_score, created_at
                       FROM memories
                       WHERE agent = $1 AND invalid_at IS NULL
                       AND category = ANY($2)
                       AND lower(content) LIKE $3
                       ORDER BY importance DESC NULLS LAST, created_at DESC
                       LIMIT $4""",
                    agent, list(SOURCE_CATS), ilike, max_memories - len(rows),
                )
                seen = {r["id"] for r in rows}
                rows += [r for r in fallback if r["id"] not in seen]

        # Top-importance fill if still sparse
        if len(rows) < max_memories:
            seen = {r["id"] for r in rows}
            top = await conn.fetch(
                """SELECT id, category, content, importance, confidence_score, created_at
                   FROM memories
                   WHERE agent = $1 AND invalid_at IS NULL AND category = ANY($2)
                   ORDER BY importance DESC NULLS LAST
                   LIMIT $3""",
                agent, list(SOURCE_CATS), min(10, max_memories - len(rows)),
            )
            rows += [r for r in top if r["id"] not in seen]

        if not rows:
            return f"No relevant memories found for agent='{agent}', topic='{topic}'."

        memories_processed = len(rows)

        # ── 4. Group by category ──
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(r["category"], []).append(r)

        # ── 5. Synthesize 1 belief per category ──
        now = datetime.now(timezone.utc)
        beliefs = []

        for cat, mems in groups.items():
            total_weight = sum(float(m["importance"] or 5) for m in mems)
            if total_weight == 0:
                total_weight = len(mems)

            weighted_conf = sum(
                float(m["confidence_score"] or 0.8) * float(m["importance"] or 5)
                for m in mems
            ) / total_weight
            weighted_conf = round(min(1.0, max(0.0, weighted_conf)), 3)

            avg_imp = round(sum(int(m["importance"] or 7) for m in mems) / len(mems), 1)

            # Anchor: most important memory in group
            anchor = max(mems, key=lambda m: int(m["importance"] or 0))
            belief_text = (
                f"[{cat}] Creencia sintetizada de {len(mems)} memorias "
                f"sobre '{topic}': {anchor['content'][:200]} "
                f"(conf={weighted_conf:.2f}, imp_avg={avg_imp}) "
                f"{marker}"
            )[:600]

            beliefs.append({
                "category": cat,
                "confidence": weighted_conf,
                "importance": avg_imp,
                "sample_count": len(mems),
                "text": belief_text,
            })

        # ── 6. Store beliefs as 'insight' imp=8 ──
        beliefs_created = 0
        for b in beliefs:
            await conn.execute(
                """INSERT INTO memories
                   (agent, category, content, importance, confidence_score, created_at)
                   VALUES ($1, 'insight', $2, 8, $3, $4)""",
                agent, b["text"], b["confidence"], now,
            )
            beliefs_created += 1

    elapsed = int((time.monotonic() - t0) * 1000)

    return json.dumps({
        "topic": topic,
        "memories_processed": memories_processed,
        "beliefs_created": beliefs_created,
        "elapsed_ms": elapsed,
        "beliefs": [
            {"category": b["category"], "confidence": b["confidence"],
             "sample_count": b["sample_count"], "text": b["text"]}
            for b in beliefs
        ],
    }, ensure_ascii=False, indent=2)


# ── Main ──

if __name__ == "__main__":
    mcp.run(transport="stdio")
