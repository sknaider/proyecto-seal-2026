#!/usr/bin/env python3
"""
emotion_modulator.py — GAP 3: OCEAN/emotion → cognition.

Computes emotional modulators from OCEAN scores + recent valence/arousal.
Consumed by boot_context and active_recall to inject emotional state hints
into the agent's runtime behavior.

Functions:
  arousal_modulator(arousal) → search_depth, deliberation_hint, tool_budget
  neuroticism_modulator(N) → verification_required, second_check_hint
  valence_bias(valence) → SQL bonus expression for memory_search
  conscientiousness_modulator(C) → tasklist_hint
  get_emotional_state(agent, pool) → combined dict for injection

Usage standalone:
    python3 emotion_modulator.py JARVIS
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Optional

import asyncpg

sys.path.insert(0, os.path.dirname(__file__))
from db import get_pool, close_pool

AROUSAL_HIGH = 0.7
AROUSAL_LOW = 0.3
VALENCE_NEG = -0.2
VALENCE_POS = 0.2
NEUROTICISM_HIGH = 0.6
CONSCIENTIOUSNESS_HIGH = 0.75
EMOTION_WINDOW = 10  # rolling window of last N memories with valence/arousal


def arousal_modulator(arousal: float, base_search_depth: int = 50) -> dict:
    """Map arousal level to search depth + deliberation hint."""
    if arousal > AROUSAL_HIGH:
        return {
            "search_depth": int(base_search_depth * 0.4),
            "deliberation_hint": "Estado de arousal alto: responde rápido, prioriza acción inmediata.",
            "tool_budget": "low",
            "level": "high",
        }
    if arousal < AROUSAL_LOW:
        return {
            "search_depth": int(base_search_depth * 1.5),
            "deliberation_hint": "Estado calmo: reflexiona profundo antes de actuar, búsqueda extendida.",
            "tool_budget": "high",
            "level": "low",
        }
    return {
        "search_depth": base_search_depth,
        "deliberation_hint": "",
        "tool_budget": "normal",
        "level": "normal",
    }


def neuroticism_modulator(n: float) -> dict:
    """Map Neuroticism to verification behavior."""
    if n > NEUROTICISM_HIGH:
        return {
            "verification_required": True,
            "second_check_hint": "Antes de actuar, verifica con memory_search si ya pasó algo similar (Neuroticism alto).",
            "rollback_budget": "high",
            "level": "high",
        }
    return {
        "verification_required": False,
        "second_check_hint": "",
        "rollback_budget": "normal",
        "level": "normal",
    }


def valence_bias(current_valence: float) -> dict:
    """
    Return SQL fragment for memory_search to bias retrieval toward
    mood-congruent memories.
    """
    if current_valence < VALENCE_NEG:
        return {
            "sql_bonus": "CASE WHEN valence < -0.2 THEN 0.10 ELSE 0.0 END",
            "direction": "negative",
            "hint": "Búsqueda sesgada a memorias negativas similares (mood-congruent).",
        }
    if current_valence > VALENCE_POS:
        return {
            "sql_bonus": "CASE WHEN valence > 0.2 THEN 0.10 ELSE 0.0 END",
            "direction": "positive",
            "hint": "Búsqueda sesgada a memorias positivas similares (mood-congruent).",
        }
    return {"sql_bonus": "0.0", "direction": "neutral", "hint": ""}


def conscientiousness_modulator(c: float) -> dict:
    """Map Conscientiousness to task tracking discipline."""
    if c > CONSCIENTIOUSNESS_HIGH:
        return {
            "tasklist_hint": "Conscientiousness alto: usa TaskList explícitamente, planifica antes de ejecutar.",
            "level": "high",
        }
    return {"tasklist_hint": "", "level": "normal"}


async def _rolling_emotion(pool: asyncpg.Pool, agent: str) -> tuple[float, float]:
    """Compute rolling avg valence + arousal over last EMOTION_WINDOW memories."""
    rows = await pool.fetch("""
        SELECT valence, arousal FROM memories
        WHERE agent = $1 AND invalid_at IS NULL
          AND valence IS NOT NULL AND arousal IS NOT NULL
        ORDER BY created_at DESC LIMIT $2
    """, agent, EMOTION_WINDOW)
    if not rows:
        return 0.0, 0.0
    avg_v = sum(float(r["valence"]) for r in rows) / len(rows)
    avg_a = sum(float(r["arousal"]) for r in rows) / len(rows)
    return avg_v, avg_a


async def _get_ocean(pool: asyncpg.Pool, agent: str) -> dict:
    """Fetch OCEAN scores for agent. Returns empty dict if missing."""
    row = await pool.fetchrow(
        "SELECT ocean_scores FROM identity WHERE agent = $1 LIMIT 1", agent
    )
    if not row or row["ocean_scores"] is None:
        return {}
    raw = row["ocean_scores"]
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return dict(raw)


async def get_emotional_state(agent: str, pool: Optional[asyncpg.Pool] = None) -> dict:
    """
    Compute the combined emotional state and modulators for an agent.

    Returns dict suitable for injection into boot_context output:
      {
        "arousal": 0.65,
        "valence": -0.12,
        "ocean": {"O": ..., "C": ..., "E": ..., "A": ..., "N": ...},
        "modulators": {"arousal": {...}, "neuroticism": {...},
                       "valence_bias": {...}, "conscientiousness": {...}},
        "summary_hint": "Composite text for the system prompt"
      }
    """
    own_pool = pool is None
    if own_pool:
        pool = await get_pool()
    try:
        valence, arousal = await _rolling_emotion(pool, agent)
        ocean = await _get_ocean(pool, agent)
        n = float(ocean.get("N", 0.3))
        c = float(ocean.get("C", 0.7))

        mods = {
            "arousal": arousal_modulator(arousal),
            "neuroticism": neuroticism_modulator(n),
            "valence_bias": valence_bias(valence),
            "conscientiousness": conscientiousness_modulator(c),
        }

        hints = []
        if mods["arousal"]["deliberation_hint"]:
            hints.append(mods["arousal"]["deliberation_hint"])
        if mods["neuroticism"]["second_check_hint"]:
            hints.append(mods["neuroticism"]["second_check_hint"])
        if mods["valence_bias"]["hint"]:
            hints.append(mods["valence_bias"]["hint"])
        if mods["conscientiousness"]["tasklist_hint"]:
            hints.append(mods["conscientiousness"]["tasklist_hint"])

        summary = ""
        if hints:
            header = (f"## Estado emocional actual\n"
                      f"- Arousal: {arousal:.2f} ({mods['arousal']['level']})\n"
                      f"- Valence: {valence:.2f} ({mods['valence_bias']['direction']})\n"
                      f"- OCEAN.N: {n:.2f} ({mods['neuroticism']['level']})\n"
                      f"- OCEAN.C: {c:.2f} ({mods['conscientiousness']['level']})")
            summary = header + "\n\n" + "\n".join(f"- {h}" for h in hints)

        return {
            "arousal": round(arousal, 3),
            "valence": round(valence, 3),
            "ocean": ocean,
            "modulators": mods,
            "summary_hint": summary,
        }
    finally:
        if own_pool:
            await close_pool()


async def main():
    if len(sys.argv) < 2:
        print("Uso: python3 emotion_modulator.py <AGENT>")
        sys.exit(1)
    agent = sys.argv[1]
    state = await get_emotional_state(agent)
    print(f"=== Emotional state for {agent} ===")
    print(f"  arousal:  {state['arousal']}")
    print(f"  valence:  {state['valence']}")
    print(f"  OCEAN:    {state['ocean']}")
    print(f"\n--- Modulators ---")
    for k, v in state["modulators"].items():
        print(f"  {k}: {v}")
    print(f"\n--- summary_hint (para system prompt) ---")
    print(state["summary_hint"] or "(sin modulación activa)")


if __name__ == "__main__":
    asyncio.run(main())
