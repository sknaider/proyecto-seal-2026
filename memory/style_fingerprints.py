"""SEAL Style Fingerprints — behavioral style scoring per agent.

Spec: memory/spec_style_fingerprints_v1.md (JARVIS, aprobado William 09-may-2026)
Implementa: ALICE

Tres scores (0.0–1.0):
  directness_score    — asertivo vs hedging (A+N inverse)
  formality_score     — formal vs coloquial (C+E inverse)
  vocabulary_richness — type-token ratio (O)

Uso:
  update_style_fingerprint(agent)  — toma snapshot y guarda en soul_v3.style_fingerprints
  snapshot_all_agents()            — baseline inicial para todos los agentes activos
  get_latest_fingerprint(agent)    — último snapshot
  detect_style_drift(agent, threshold=0.1) — compara últimos 2 snapshots
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    from .db import get_pool
except ImportError:
    from db import get_pool

MESSAGES_DIR = Path(__file__).parent.parent / "messages"

HEDGING_PHRASES = [
    "quizás", "podría", "me parece", "creo que", "tal vez",
    "posiblemente", "no estoy seguro", "puede que", "debería",
    "would", "might", "perhaps", "could be",
]
ASSERTIVE_PHRASES = [
    "esto es", "confirmo", "definitivamente", "está claro",
    "el resultado es", "verificado", "aprobado",
]
INFORMAL_MARKERS = ["ok", "jaja", "xd", "ntp", "ya", "dale", "wil", "sí!", "genial"]
FORMAL_MARKERS = ["confirmo", "verificado", "aprobado", "implementado", "auditado", "spec"]

ACTIVE_AGENTS = ["JARVIS", "ALICE", "ADA", "NEXUS", "DUM"]


def _load_messages(agent: str, limit: int = 200) -> list[str]:
    """Carga mensajes recientes del agente desde su JSONL."""
    agent_lower = agent.lower()
    candidates = [
        MESSAGES_DIR / f"{agent_lower}_messages.jsonl",
        MESSAGES_DIR / f"{agent}_messages.jsonl",
    ]
    for path in candidates:
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
            messages = []
            for line in lines[-limit:]:
                try:
                    obj = json.loads(line)
                    content = obj.get("content") or obj.get("message") or obj.get("text", "")
                    if content:
                        messages.append(str(content))
                except (json.JSONDecodeError, AttributeError):
                    continue
            return messages
    return []


def _directness_score(messages: list[str]) -> float:
    if not messages:
        return 0.5
    text = " ".join(messages).lower()
    hedging = sum(1 for p in HEDGING_PHRASES if p in text)
    assertive = sum(1 for p in ASSERTIVE_PHRASES if p in text)
    total = hedging + assertive + 1
    return min(1.0, max(0.0, assertive / total))


def _formality_score(messages: list[str]) -> float:
    if not messages:
        return 0.5
    text = " ".join(messages).lower()
    words = text.split()
    sentence_count = max(1, text.count(".") + text.count("\n"))
    avg_sentence_len = len(words) / sentence_count
    informal = sum(1 for m in INFORMAL_MARKERS if m in text)
    formal = sum(1 for m in FORMAL_MARKERS if m in text)
    length_score = min(1.0, avg_sentence_len / 20.0)
    ratio_score = min(1.0, formal / max(1, formal + informal))
    return (length_score + ratio_score) / 2


def _vocabulary_richness(messages: list[str]) -> float:
    if not messages:
        return 0.5
    words = " ".join(messages).lower().split()
    if not words:
        return 0.5
    return min(1.0, len(set(words)) / len(words))


async def update_style_fingerprint(agent: str, limit: int = 200) -> dict:
    """Toma snapshot de estilo del agente y guarda en soul_v3.style_fingerprints.

    Retorna los scores calculados.
    """
    messages = _load_messages(agent, limit)
    scores = {
        "directness_score": _directness_score(messages),
        "formality_score": _formality_score(messages),
        "vocabulary_richness": _vocabulary_richness(messages),
    }
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO soul_v3.style_fingerprints
                (agent, directness_score, formality_score, vocabulary_richness)
            VALUES ($1, $2, $3, $4)
            """,
            agent,
            scores["directness_score"],
            scores["formality_score"],
            scores["vocabulary_richness"],
        )
    return {"agent": agent, "n_messages": len(messages), **scores}


async def snapshot_all_agents(agents: list[str] | None = None) -> list[dict]:
    """Toma baseline de todos los agentes activos."""
    targets = agents or ACTIVE_AGENTS
    results = []
    for agent in targets:
        result = await update_style_fingerprint(agent)
        results.append(result)
    return results


async def get_latest_fingerprint(agent: str) -> dict | None:
    """Retorna el último snapshot de estilo del agente."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT agent, directness_score, formality_score, vocabulary_richness, created_at
            FROM soul_v3.style_fingerprints
            WHERE agent = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            agent,
        )
        return dict(row) if row else None


async def detect_style_drift(agent: str, threshold: float = 0.1) -> dict | None:
    """Compara los 2 últimos snapshots. Retorna drift si supera threshold, None si estable."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT directness_score, formality_score, vocabulary_richness, created_at
            FROM soul_v3.style_fingerprints
            WHERE agent = $1
            ORDER BY created_at DESC
            LIMIT 2
            """,
            agent,
        )
    if len(rows) < 2:
        return None
    current, previous = rows[0], rows[1]
    deltas = {
        "delta_directness": abs(current["directness_score"] - previous["directness_score"]),
        "delta_formality": abs(current["formality_score"] - previous["formality_score"]),
        "delta_vocab": abs(current["vocabulary_richness"] - previous["vocabulary_richness"]),
    }
    max_delta = max(deltas.values())
    if max_delta > threshold:
        return {
            "agent": agent,
            "drift_detected": True,
            "max_delta": round(max_delta, 4),
            **{k: round(v, 4) for k, v in deltas.items()},
            "from": str(previous["created_at"]),
            "to": str(current["created_at"]),
        }
    return None
