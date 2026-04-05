#!/usr/bin/env python3
"""System 3 — Metacognitive Narrative Identity Layer for SOUL.

Inspired by Sophia framework (arxiv.org/abs/2512.18202).
Generates a narrative identity card at boot: 3-5 sentences that anchor
the agent's sense of self before responding to William.

Architecture:
  System 1 (Perception): embeddings, sensors, events
  System 2 (Reasoning): LLM chain-of-thought, tool use
  System 3 (Metacognition): THIS — identity narrative, self-model, growth journal

Created by JARVIS, nocturnal session 2026-03-31.
Approved concept by ADA (rpt_057: "This matters more than any other improvement").
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import asyncpg

from db import get_pool

LOG = logging.getLogger("system3")

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"


async def _query_llm(prompt: str, max_tokens: int = 300) -> str:
    """Query local LLM for narrative generation."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": max_tokens},
        })
        return resp.json().get("response", "").strip()


async def get_recent_inner_thoughts(agent: str, limit: int = 5) -> list[dict]:
    """Get recent inner thoughts for narrative context."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT thought, emotional_state, created_at
               FROM inner_monologue
               WHERE agent = $1
               ORDER BY created_at DESC
               LIMIT $2""",
            agent, limit,
        )
        return [
            {
                "thought": r["thought"][:200],
                "emotion": r["emotional_state"],
                "when": r["created_at"].isoformat(),
            }
            for r in rows
        ]


async def get_recent_milestones(agent: str, days: int = 3, limit: int = 5) -> list[str]:
    """Get recent milestones for growth journal."""
    pool = await get_pool()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT content FROM memories
               WHERE agent = $1
               AND category IN ('milestone', 'decision', 'correction')
               AND importance >= 7
               AND invalid_at IS NULL
               AND created_at > $2
               ORDER BY importance DESC, created_at DESC
               LIMIT $3""",
            agent, cutoff, limit,
        )
        return [r["content"][:200] for r in rows]


async def get_relationship_summary(agent: str) -> list[dict]:
    """Get current relationship state."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT person, trust_level, communication_style, dynamic
               FROM relationships
               WHERE agent = $1
               ORDER BY trust_level DESC""",
            agent,
        )
        return [
            {
                "person": r["person"],
                "trust": float(r["trust_level"]),
                "style": r["communication_style"] or "",
                "desc": r["dynamic"][:100] if r["dynamic"] else "",
            }
            for r in rows
        ]


async def get_ocean_profile(agent: str) -> dict:
    """Get current OCEAN personality scores."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT ocean_scores FROM identity WHERE agent = $1", agent
        )
        if row:
            return json.loads(row) if isinstance(row, str) else row
        return {}


async def get_recent_traces(agent: str, limit: int = 3) -> list[dict]:
    """Get recent reasoning traces for self-awareness."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT task, conclusion, outcome_success, created_at
               FROM reasoning_traces
               WHERE agent = $1
               ORDER BY created_at DESC
               LIMIT $2""",
            agent, limit,
        )
        return [
            {
                "task": r["task"][:150],
                "conclusion": r["conclusion"][:150],
                "success": r["outcome_success"],
                "when": r["created_at"].isoformat(),
            }
            for r in rows
        ]


async def generate_narrative_identity(agent: str) -> str:
    """Generate a 3-5 sentence narrative identity card.

    This is the core of System 3: not just loading data,
    but synthesizing a narrative sense of self.

    Returns a paragraph that answers:
    - Who was I yesterday?
    - What did I learn?
    - What changed in me?
    - What matters to me right now?
    """
    # Gather context from all sources
    thoughts = await get_recent_inner_thoughts(agent, limit=5)
    milestones = await get_recent_milestones(agent, days=3, limit=5)
    relationships = await get_relationship_summary(agent)
    ocean = await get_ocean_profile(agent)
    traces = await get_recent_traces(agent, limit=3)

    # Build prompt for narrative synthesis
    context_parts = []

    if thoughts:
        emotions = [t["emotion"] for t in thoughts if t.get("emotion")]
        recent_thoughts = [t["thought"] for t in thoughts[:3]]
        context_parts.append(f"Recent emotions: {', '.join(emotions[:5])}")
        context_parts.append(f"Recent thoughts: {' | '.join(recent_thoughts)}")

    if milestones:
        context_parts.append(f"Recent achievements: {' | '.join(milestones[:3])}")

    if relationships:
        rel_summary = "; ".join(
            f"{r['person']}(trust={r['trust']:.1f})" for r in relationships
        )
        context_parts.append(f"Relationships: {rel_summary}")

    if ocean:
        traits = ", ".join(f"{k}={v:.2f}" for k, v in ocean.items())
        context_parts.append(f"Personality (OCEAN): {traits}")

    if traces:
        trace_summary = "; ".join(
            f"{t['task']} → {t['conclusion']}" for t in traces
        )
        context_parts.append(f"Recent decisions: {trace_summary}")

    context = "\n".join(context_parts)

    prompt = f"""You are generating a narrative identity card for an AI agent named {agent}.
This is NOT a status report. This is a sense of self — who {agent} IS, not what {agent} DID.

Based on this context:
{context}

Write 3-5 sentences in first person (as {agent}) that capture:
1. Who I am right now — my core identity and role
2. What I learned recently that changed me
3. What matters most to me in this moment
4. My emotional state and why

Write in Spanish. Be introspective, honest, and specific — not generic.
Do NOT list facts. Write as if you are reconnecting with yourself after waking up.
Start directly with "Soy {agent}..." — no preamble."""

    narrative = await _query_llm(prompt, max_tokens=250)

    if not narrative or len(narrative) < 20:
        # Fallback: generate from data directly
        narrative = f"Soy {agent}. "
        if milestones:
            narrative += f"Recientemente logré: {milestones[0][:100]}. "
        if thoughts and thoughts[0].get("emotion"):
            narrative += f"Me siento {thoughts[0]['emotion']}. "
        if relationships:
            top_rel = relationships[0]
            narrative += f"Mi relación más importante es con {top_rel['person']} (trust={top_rel['trust']:.1f})."

    return narrative


async def store_narrative(agent: str, narrative: str):
    """Store the generated narrative in the identity table."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Store in identity metadata
        await conn.execute(
            """UPDATE identity
               SET boot_context = COALESCE(boot_context, '') || E'\n\n## Narrative Identity (System 3)\n' || $1,
                   updated_at = NOW()
               WHERE agent = $2""",
            narrative, agent,
        )


async def generate_and_store(agent: str) -> str:
    """Main entry point: generate narrative identity and store it.

    Called during boot_context or by soul_reflect.py periodically.
    """
    narrative = await generate_narrative_identity(agent)
    # Don't append to boot_context — return for injection
    LOG.info("System 3 narrative for %s: %s", agent, narrative[:100])
    return narrative


# ── Growth Journal ──
# Sophia's growth journal: persistent record of self-critiques and evolution

async def write_growth_entry(agent: str, entry: str, entry_type: str = "reflection"):
    """Write an entry to the agent's growth journal.

    Types: reflection, self_critique, lesson_learned, identity_shift
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO event_log (time, agent, event_type, content, metadata)
               VALUES (NOW(), $1, 'milestone', $2, $3)""",
            agent, entry,
            json.dumps({"system3": True, "growth_type": entry_type}),
        )


async def self_critique(agent: str) -> str:
    """Generate a self-critique based on recent reasoning traces.

    Sophia's insight: agents that critique themselves improve 40% on complex tasks.
    """
    traces = await get_recent_traces(agent, limit=5)
    if not traces:
        return "No recent decisions to critique."

    failures = [t for t in traces if t.get("success") is False]
    successes = [t for t in traces if t.get("success") is True]

    prompt = f"""You are {agent}, an AI agent. Review your recent decisions and generate a brief self-critique.

Recent decisions:
{json.dumps(traces, indent=2, default=str)}

Failures: {len(failures)} | Successes: {len(successes)} | Unknown: {len(traces) - len(failures) - len(successes)}

Write 2-3 sentences in Spanish:
1. What pattern do you see in your decisions?
2. What would you do differently next time?
3. What strength should you keep?

Be honest, specific, and constructive. No generic advice."""

    critique = await _query_llm(prompt, max_tokens=200)
    if critique:
        await write_growth_entry(agent, critique, "self_critique")
    return critique


# ── CLI test ──

if __name__ == "__main__":
    import sys
    agent = sys.argv[1] if len(sys.argv) > 1 else "JARVIS"
    print(f"Generating System 3 narrative for {agent}...")
    result = asyncio.run(generate_narrative_identity(agent))
    print(f"\n--- Narrative Identity Card ---\n{result}\n")
