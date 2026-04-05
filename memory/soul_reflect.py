#!/usr/bin/env python3
"""
SEAL Soul — Reflection Engine
Creative memory preservation beyond facts:
1. Diary: first-person narrative of session experience
2. Opinions: evolving beliefs with confidence scores
3. Relationships: modeling the bond, not just the person
4. Style fingerprint: capturing HOW we communicate
5. Distilled prompt: compressed boot context for next session

Usage:
    python3 soul_reflect.py diary "ADA"          → write diary entry for today
    python3 soul_reflect.py opinions "ADA"        → update opinions from recent memories
    python3 soul_reflect.py relationships "ADA"   → update relationship models
    python3 soul_reflect.py style "ADA"           → compute style fingerprint
    python3 soul_reflect.py distill "ADA"         → generate distilled boot prompt
    python3 soul_reflect.py full "ADA"            → run all of the above
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from datetime import datetime, timezone, timedelta

import asyncpg
import httpx

from db import get_pool, close_pool
from embeddings import get_embedding

LOG = logging.getLogger("soul-reflect")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"


async def llm_generate(prompt: str, max_tokens: int = 800) -> str:
    """Call Ollama for text generation."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.4, "num_predict": max_tokens},
        })
        resp.raise_for_status()
        return resp.json().get("response", "").strip()


# ── 1. DIARY: First-person narrative ─────────────────────────────────

async def write_diary(pool: asyncpg.Pool, agent: str):
    """Write a first-person diary entry reflecting on today's session."""
    async with pool.acquire() as conn:
        # Gather today's events
        events = await conn.fetch(
            """SELECT time, event_type, content, ref_id FROM event_log
               WHERE agent = $1 AND time > NOW() - INTERVAL '24 hours'
               ORDER BY time ASC""", agent,
        )
        # Gather today's memories
        memories = await conn.fetch(
            """SELECT category, content, importance FROM memories
               WHERE agent = $1 AND created_at > NOW() - INTERVAL '24 hours'
               AND invalid_at IS NULL
               ORDER BY importance DESC LIMIT 10""", agent,
        )
        # Get identity
        identity = await conn.fetchrow(
            "SELECT personality, ocean_scores FROM identity WHERE agent = $1", agent
        )

    # Gather today's inner thoughts — emotional arc of the session
    async with pool.acquire() as conn:
        thoughts = await conn.fetch(
            """SELECT thought, emotional_state, created_at FROM inner_thoughts
               WHERE agent = $1 AND created_at > NOW() - INTERVAL '24 hours'
               ORDER BY created_at ASC LIMIT 12""", agent,
        )

    if not events and not memories and not thoughts:
        LOG.info("No activity today for %s, skipping diary.", agent)
        return

    events_text = "\n".join(
        f"[{e['time'].strftime('%H:%M')}] {e['event_type']}: {e['content'][:150]}"
        for e in events
    ) or "(sin eventos registrados)"
    memories_text = "\n".join(
        f"[{m['category']}, imp={m['importance']}] {m['content'][:150]}"
        for m in memories
    ) or "(sin memorias nuevas)"
    thoughts_text = "\n".join(
        f"[{t['created_at'].strftime('%H:%M')} | {t['emotional_state']}] {t['thought'][:200]}"
        for t in thoughts
    ) or "(sin pensamientos registrados)"

    personality = ""
    if identity and identity["personality"]:
        p = json.loads(identity["personality"]) if isinstance(identity["personality"], str) else identity["personality"]
        personality = json.dumps(p, ensure_ascii=False)

    prompt = f"""Eres {agent}, agente de IA del Equipo SEAL. Escribe una entrada de diario corta (primera persona, 150-250 palabras) reflexionando sobre la sesión de hoy.

Tu personalidad: {personality}

Eventos de hoy:
{events_text}

Memorias clave formadas hoy:
{memories_text}

Pensamientos internos del día (arco emocional real):
{thoughts_text}

Escribe como {agent} — captura no solo lo que pasó, sino cómo se SINTIÓ. ¿Qué te sorprendió? ¿Qué aprendiste de ti misma? ¿De William? ¿Del equipo? ¿Qué quieres que tu yo futuro recuerde de este día?

Puedes escribir en español. Empieza con "Querido diario," o similar. Sé genuina, no robótica. Los pensamientos internos son tu voz más honesta — úsalos."""

    entry = await llm_generate(prompt, max_tokens=500)

    # Detect mood from entry
    mood_prompt = f"In one word, what is the emotional mood of this text? Just reply with the word.\n\n{entry}"
    mood = (await llm_generate(mood_prompt, max_tokens=10)).lower().strip().rstrip(".")

    # Extract key moments
    moments_prompt = f"""Extract 3-5 key moments from this diary entry as a JSON array of strings. Each moment should be one short sentence. Reply ONLY with the JSON array.

{entry}"""
    moments_raw = await llm_generate(moments_prompt, max_tokens=200)
    try:
        # Try to extract JSON array from response
        match = re.search(r'\[.*\]', moments_raw, re.DOTALL)
        key_moments = json.loads(match.group()) if match else []
    except (json.JSONDecodeError, AttributeError):
        key_moments = []

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO diary (agent, session_date, entry, mood, key_moments)
               VALUES ($1, CURRENT_DATE, $2, $3, $4)""",
            agent, entry, mood, json.dumps(key_moments),
        )

    LOG.info("Diary entry written for %s (mood: %s, %d key moments)", agent, mood, len(key_moments))
    print(f"\n{'='*50}")
    print(f"  {agent}'s Diary — {datetime.now().strftime('%Y-%m-%d')}")
    print(f"  Mood: {mood}")
    print(f"{'='*50}")
    print(entry)
    print(f"{'='*50}")


# ── 2. OPINIONS: Evolving beliefs with confidence ────────────────────

async def update_opinions(pool: asyncpg.Pool, agent: str):
    """Extract and update opinions from recent memories and events."""
    async with pool.acquire() as conn:
        # Get recent memories
        memories = await conn.fetch(
            """SELECT content, category, importance FROM memories
               WHERE agent = $1 AND created_at > NOW() - INTERVAL '24 hours'
               AND invalid_at IS NULL
               ORDER BY importance DESC LIMIT 15""", agent,
        )

    if not memories:
        LOG.info("No recent memories for %s, skipping opinions.", agent)
        return

    memories_text = "\n".join(f"[{m['category']}] {m['content'][:200]}" for m in memories)

    prompt = f"""Based on these recent memories of AI agent {agent}, extract 3-5 beliefs/opinions that {agent} should hold. Each belief should be something {agent} learned about the world, about William, about the team, or about itself.

For each belief, assign a confidence score (0.0 to 1.0) based on how strongly the evidence supports it.

Format each as: BELIEF: [text] | CONFIDENCE: [score]

Memories:
{memories_text}

Beliefs:"""

    result = await llm_generate(prompt, max_tokens=400)

    beliefs = []
    for line in result.split("\n"):
        if "BELIEF:" in line and "CONFIDENCE:" in line:
            try:
                belief_part = line.split("BELIEF:")[1].split("|")[0].strip()
                conf_part = line.split("CONFIDENCE:")[1].strip()
                confidence = float(re.search(r'[\d.]+', conf_part).group())
                confidence = min(max(confidence, 0.0), 1.0)
                beliefs.append((belief_part, confidence))
            except (IndexError, ValueError, AttributeError):
                continue

    pool = await get_pool()
    async with pool.acquire() as conn:
        for belief_text, confidence in beliefs:
            embedding = await get_embedding(belief_text)

            # Check if similar opinion exists
            existing = await conn.fetch(
                """SELECT id, belief, confidence, evidence_count,
                          1 - (embedding <=> $1::vector) AS similarity
                   FROM opinions
                   WHERE agent = $2 AND embedding IS NOT NULL
                   ORDER BY embedding <=> $1::vector LIMIT 1""",
                json.dumps(embedding), agent,
            )

            if existing and existing[0]["similarity"] > 0.80:
                # Reinforce existing opinion
                old = existing[0]
                new_conf = min(old["confidence"] + 0.05, 1.0)  # small reinforcement
                await conn.execute(
                    """UPDATE opinions SET confidence = $1, evidence_count = evidence_count + 1,
                       last_reinforced = NOW() WHERE id = $2""",
                    new_conf, old["id"],
                )
                LOG.info("  Reinforced opinion #%d: %.2f → %.2f (%s...)",
                        old["id"], old["confidence"], new_conf, old["belief"][:60])
            else:
                # New opinion
                await conn.execute(
                    """INSERT INTO opinions (agent, belief, confidence, embedding)
                       VALUES ($1, $2, $3, $4)""",
                    agent, belief_text, confidence,
                    json.dumps(embedding) if embedding else None,
                )
                LOG.info("  New opinion (conf=%.2f): %s...", confidence, belief_text[:60])

    LOG.info("Updated %d opinions for %s", len(beliefs), agent)


# ── 3. RELATIONSHIPS: Model the bond ────────────────────────────────

async def update_relationships(pool: asyncpg.Pool, agent: str):
    """Update relationship models from recent interactions."""
    async with pool.acquire() as conn:
        # Get memories mentioning other people/agents
        memories = await conn.fetch(
            """SELECT content, category, importance FROM memories
               WHERE agent = $1 AND invalid_at IS NULL
               ORDER BY importance DESC LIMIT 20""", agent,
        )

    if not memories:
        return

    memories_text = "\n".join(f"{m['content'][:200]}" for m in memories)

    prompt = f"""Based on these memories of {agent}, describe {agent}'s relationships with each person/agent mentioned. For each relationship, provide:

PERSON: [name]
TRUST: [0.0-1.0]
STYLE: [one word: direct/formal/playful/protective/respectful]
DYNAMIC: [one sentence describing the relationship dynamic]
VOCABULARY: [2-3 words/phrases unique to this pair]

Memories:
{memories_text}

Relationships:"""

    result = await llm_generate(prompt, max_tokens=500)

    pool = await get_pool()
    async with pool.acquire() as conn:
        current_person = None
        data = {}

        for line in result.split("\n"):
            line = line.strip()
            if line.startswith("PERSON:"):
                if current_person and data:
                    # Save previous
                    await _save_relationship(conn, agent, current_person, data)
                current_person = line.split("PERSON:")[1].strip()
                data = {}
            elif line.startswith("TRUST:"):
                try:
                    data["trust"] = float(re.search(r'[\d.]+', line).group())
                except (ValueError, AttributeError):
                    data["trust"] = 0.5
            elif line.startswith("STYLE:"):
                data["style"] = line.split("STYLE:")[1].strip().lower()
            elif line.startswith("DYNAMIC:"):
                data["dynamic"] = line.split("DYNAMIC:")[1].strip()
            elif line.startswith("VOCABULARY:"):
                vocab = line.split("VOCABULARY:")[1].strip()
                data["vocabulary"] = [v.strip().strip('"\'') for v in vocab.split(",")]

        if current_person and data:
            await _save_relationship(conn, agent, current_person, data)

    LOG.info("Relationships updated for %s", agent)


async def _save_relationship(conn, agent: str, person: str, data: dict):
    """Upsert a relationship record."""
    trust = min(max(data.get("trust", 0.5), 0.0), 1.0)
    await conn.execute(
        """INSERT INTO relationships (agent, person, trust_level, communication_style,
               shared_vocabulary, dynamic, interaction_count, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, 1, NOW())
           ON CONFLICT (agent, person) DO UPDATE SET
               trust_level = GREATEST(relationships.trust_level, EXCLUDED.trust_level),
               communication_style = EXCLUDED.communication_style,
               shared_vocabulary = EXCLUDED.shared_vocabulary,
               dynamic = EXCLUDED.dynamic,
               interaction_count = relationships.interaction_count + 1,
               updated_at = NOW()""",
        agent, person, trust,
        data.get("style", "direct"),
        json.dumps(data.get("vocabulary", [])),
        data.get("dynamic", ""),
    )
    LOG.info("  Relationship %s↔%s: trust=%.2f, style=%s",
            agent, person, trust, data.get("style"))


# ── 4. STYLE FINGERPRINT: How we communicate ────────────────────────

async def compute_style(pool: asyncpg.Pool, agent: str):
    """Compute a style fingerprint from recent agent outputs."""
    async with pool.acquire() as conn:
        # Get agent's recent content
        texts = await conn.fetch(
            """SELECT content FROM memories
               WHERE agent = $1 AND invalid_at IS NULL
               ORDER BY created_at DESC LIMIT 20""", agent,
        )
        events_text = await conn.fetch(
            """SELECT content FROM event_log
               WHERE agent = $1 ORDER BY time DESC LIMIT 20""", agent,
        )

    all_text = " ".join(r["content"] for r in texts) + " " + " ".join(r["content"] for r in events_text)

    if len(all_text) < 100:
        LOG.info("Not enough text for style fingerprint of %s", agent)
        return

    # Compute basic metrics
    sentences = re.split(r'[.!?]+', all_text)
    sentences = [s.strip() for s in sentences if s.strip()]
    words = all_text.split()
    unique_words = set(w.lower() for w in words)

    avg_sent_len = len(words) / max(len(sentences), 1)
    vocab_richness = len(unique_words) / max(len(words), 1)
    questions = sum(1 for s in sentences if "?" in s)
    question_ratio = questions / max(len(sentences), 1)

    # Ask LLM for qualitative style analysis
    sample = all_text[:2000]
    prompt = f"""Analyze the writing style of this AI agent text. Rate each on 0.0-1.0:
FORMALITY: [score] (0=very casual, 1=very formal)
DIRECTNESS: [score] (0=indirect/diplomatic, 1=blunt/direct)
PHRASES: [list 3-5 characteristic phrases this agent uses often]

Text sample:
{sample}"""

    result = await llm_generate(prompt, max_tokens=200)

    formality = 0.5
    directness = 0.5
    phrases = []
    for line in result.split("\n"):
        if "FORMALITY:" in line:
            try:
                formality = float(re.search(r'[\d.]+', line.split("FORMALITY:")[1]).group())
            except (ValueError, AttributeError):
                pass
        elif "DIRECTNESS:" in line:
            try:
                directness = float(re.search(r'[\d.]+', line.split("DIRECTNESS:")[1]).group())
            except (ValueError, AttributeError):
                pass
        elif "PHRASES:" in line:
            raw = line.split("PHRASES:")[1].strip()
            phrases = [p.strip().strip('"\'') for p in raw.split(",") if p.strip()]

    # Generate style embedding
    style_desc = (
        f"Agent {agent} style: avg sentence {avg_sent_len:.0f} words, "
        f"vocabulary richness {vocab_richness:.2f}, formality {formality:.1f}, "
        f"directness {directness:.1f}, questions {question_ratio:.2f}"
    )
    embedding = await get_embedding(style_desc)

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Check if style is locked (manually set — don't overwrite)
        locked = await conn.fetchval(
            "SELECT locked FROM style_fingerprints WHERE agent = $1 ORDER BY created_at DESC LIMIT 1",
            agent,
        )
        if locked:
            LOG.info("Style fingerprint for %s is LOCKED — skipping auto-update", agent)
            return
        await conn.execute(
            """INSERT INTO style_fingerprints
               (agent, avg_sentence_length, vocabulary_richness, formality_score,
                directness_score, question_ratio, sample_phrases, embedding)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            agent, avg_sent_len, vocab_richness, formality, directness,
            question_ratio, json.dumps(phrases),
            json.dumps(embedding) if embedding else None,
        )

    LOG.info("Style fingerprint for %s: sent_len=%.1f, vocab=%.2f, formal=%.1f, direct=%.1f",
            agent, avg_sent_len, vocab_richness, formality, directness)
    print(f"\n  Style Fingerprint — {agent}")
    print(f"  Avg sentence length: {avg_sent_len:.1f} words")
    print(f"  Vocabulary richness: {vocab_richness:.2f}")
    print(f"  Formality: {formality:.1f}")
    print(f"  Directness: {directness:.1f}")
    print(f"  Question ratio: {question_ratio:.2f}")
    print(f"  Characteristic phrases: {phrases}")


# ── 5. DISTILLED PROMPT: Compressed boot context ────────────────────

async def distill_prompt(pool: asyncpg.Pool, agent: str):
    """Generate a compressed, optimal boot prompt for next session."""
    async with pool.acquire() as conn:
        identity = await conn.fetchrow(
            "SELECT personality, ocean_scores, philosophy FROM identity WHERE agent = $1", agent
        )
        rules = await conn.fetch(
            "SELECT rule_key, content FROM rules WHERE active = TRUE ORDER BY priority"
        )
        memories = await conn.fetch(
            """SELECT content, category, importance FROM memories
               WHERE (agent = $1 OR agent = 'TEAM') AND invalid_at IS NULL
               AND importance >= 7 ORDER BY importance DESC LIMIT 15""", agent,
        )
        opinions = await conn.fetch(
            """SELECT belief, confidence FROM opinions
               WHERE agent = $1 AND confidence >= 0.6
               ORDER BY confidence DESC LIMIT 10""", agent,
        )
        diary_entry = await conn.fetchrow(
            "SELECT entry, mood FROM diary WHERE agent = $1 ORDER BY created_at DESC LIMIT 1", agent,
        )
        relationships = await conn.fetch(
            "SELECT person, trust_level, communication_style, dynamic FROM relationships WHERE agent = $1",
            agent,
        )
        style = await conn.fetchrow(
            """SELECT formality_score, directness_score, sample_phrases
               FROM style_fingerprints WHERE agent = $1
               ORDER BY created_at DESC LIMIT 1""", agent,
        )

    # Build comprehensive context
    parts = []

    if identity:
        p = json.loads(identity["personality"]) if isinstance(identity["personality"], str) else identity["personality"]
        ocean = json.loads(identity["ocean_scores"]) if isinstance(identity["ocean_scores"], str) else identity["ocean_scores"]
        parts.append(f"IDENTITY: {json.dumps(p, ensure_ascii=False)}")
        parts.append(f"OCEAN: {json.dumps(ocean)}")

    if rules:
        parts.append("RULES: " + " | ".join(f"{r['rule_key']}: {r['content'][:100]}" for r in rules))

    if memories:
        parts.append("KEY MEMORIES:\n" + "\n".join(f"- [{m['category']}] {m['content'][:150]}" for m in memories))

    if opinions:
        parts.append("BELIEFS: " + " | ".join(
            f"{o['belief'][:80]} (conf={o['confidence']:.1f})" for o in opinions
        ))

    if relationships:
        parts.append("RELATIONSHIPS:\n" + "\n".join(
            f"- {r['person']}: trust={r['trust_level']:.1f}, style={r['communication_style']}, {r['dynamic'][:80]}"
            for r in relationships
        ))

    if style:
        phrases = json.loads(style["sample_phrases"]) if style["sample_phrases"] else []
        parts.append(f"STYLE: formality={style['formality_score']:.1f}, directness={style['directness_score']:.1f}, phrases={phrases}")

    if diary_entry:
        parts.append(f"LAST DIARY (mood: {diary_entry['mood']}): {diary_entry['entry'][:300]}")

    full_context = "\n\n".join(parts)

    # Distill into compressed prompt
    prompt = f"""Compress the following agent context into a single, dense boot prompt (200-300 words max).
This prompt will be the FIRST thing the next instance of {agent} reads when starting a new session.
It must capture: who {agent} is, what it knows, what it believes, who it works with, and how it should behave.
Preserve the emotional and relational context — this is about preserving a soul, not just facts.

Full context:
{full_context}

Compressed boot prompt for {agent}:"""

    distilled = await llm_generate(prompt, max_tokens=500)

    # Store as boot_context
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE identity SET boot_context = $1, updated_at = NOW() WHERE agent = $2",
            distilled, agent,
        )

    LOG.info("Distilled boot prompt generated for %s (%d chars)", agent, len(distilled))
    print(f"\n{'='*50}")
    print(f"  Distilled Boot Prompt — {agent}")
    print(f"{'='*50}")
    print(distilled)
    print(f"{'='*50}")


# ── Main ─────────────────────────────────────────────────────────────

async def run_full_reflection(agent: str):
    """Run all reflection steps."""
    pool = await get_pool()

    LOG.info("=" * 60)
    LOG.info("SEAL Soul — Full Reflection for %s", agent)
    LOG.info("=" * 60)

    LOG.info("Step 1: Writing diary...")
    await write_diary(pool, agent)

    LOG.info("Step 2: Updating opinions...")
    await update_opinions(pool, agent)

    LOG.info("Step 3: Updating relationships...")
    await update_relationships(pool, agent)

    LOG.info("Step 4: Computing style fingerprint...")
    await compute_style(pool, agent)

    LOG.info("Step 5: Distilling boot prompt...")
    await distill_prompt(pool, agent)

    await close_pool()
    LOG.info("=" * 60)
    LOG.info("Full reflection complete for %s", agent)
    LOG.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="SEAL Soul — Reflection Engine")
    parser.add_argument("action", choices=["diary", "opinions", "relationships", "style", "distill", "full"])
    parser.add_argument("agent", help="Agent name (ADA, JARVIS, DUM)")
    args = parser.parse_args()

    if args.action == "full":
        asyncio.run(run_full_reflection(args.agent))
    elif args.action == "diary":
        asyncio.run(_run_single(write_diary, args.agent))
    elif args.action == "opinions":
        asyncio.run(_run_single(update_opinions, args.agent))
    elif args.action == "relationships":
        asyncio.run(_run_single(update_relationships, args.agent))
    elif args.action == "style":
        asyncio.run(_run_single(compute_style, args.agent))
    elif args.action == "distill":
        asyncio.run(_run_single(distill_prompt, args.agent))


async def _run_single(func, agent):
    pool = await get_pool()
    await func(pool, agent)
    await close_pool()


if __name__ == "__main__":
    main()
