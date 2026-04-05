#!/usr/bin/env python3
"""SEAL Memory MCP Server — 11 tools for agent memory persistence."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

from db import get_pool, close_pool
from embeddings import get_embedding
from connectome import (
    build_connections, spreading_activation, activate_from_query,
    get_connectome_stats,
)

LOG = logging.getLogger("seal-memory")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

mcp = FastMCP("seal-memory", instructions="SEAL Memory System — persistent memory for Team SEAL agents")

OLLAMA_GEN_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"


async def classify_emotion(text: str) -> tuple[float | None, float | None]:
    """Classify valence (-1 to +1) and arousal (-1 to +1) of text via Ollama.
    Returns (None, None) on failure — never blocks memory storage."""
    try:
        prompt = (
            'Rate this text. valence: -1.0 (negative) to +1.0 (positive). '
            'arousal: -1.0 (calm) to +1.0 (intense). '
            'Reply ONLY: {"valence": X, "arousal": Y}\n\n'
            f'Text: "{text[:300]}"'
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(OLLAMA_GEN_URL, json={
                "model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                "options": {"temperature": 0.1, "num_predict": 30},
            })
            raw = resp.json().get("response", "")
            match = re.search(r'\{[^}]+\}', raw)
            if match:
                data = json.loads(match.group())
                v = max(-1.0, min(1.0, float(data.get("valence", 0))))
                a = max(-1.0, min(1.0, float(data.get("arousal", 0))))
                return v, a
    except Exception as e:
        LOG.debug("Emotion classification failed (non-blocking): %s", e)
    return None, None


# ── Tool 1: memory_store ──────────────────────────────────────────────

@mcp.tool()
async def memory_store(
    agent: str,
    category: str,
    content: str,
    importance: int = 5,
    source: str = "conversation",
    metadata: Optional[str] = None,
) -> str:
    """Store a new memory with auto-generated semantic embedding and conflict detection.

    Args:
        agent: Agent name (ADA, JARVIS, DUM, TEAM)
        category: One of: fact, preference, decision, insight, correction, milestone, pattern, emotion, trust, humor, dynamic
        content: The memory content text
        importance: 1-10 scale (10 = critical, never forget)
        source: Origin: conversation, reflection, consolidation
        metadata: Optional JSON string with extra data
    """
    pool = await get_pool()
    meta = json.loads(metadata) if metadata else {}

    try:
        embedding = await get_embedding(content)
    except Exception as e:
        LOG.warning("Embedding failed (storing without vector): %s", e)
        embedding = None

    # ── Conflict Detection (inspired by mem0) ──
    conflict_action = "added"
    invalidated_id = None
    if embedding:
        async with pool.acquire() as conn:
            # Search for similar existing valid memories
            similar = await conn.fetch(
                """SELECT id, content, importance,
                          1 - (embedding <=> $1::vector) AS similarity
                   FROM memories
                   WHERE agent = $2 AND category = $3
                     AND embedding IS NOT NULL AND invalid_at IS NULL
                   ORDER BY embedding <=> $1::vector
                   LIMIT 1""",
                json.dumps(embedding), agent, category,
            )
            if similar and similar[0]["similarity"] > 0.85:
                old = similar[0]
                if importance >= old["importance"]:
                    # Invalidate old memory (bi-temporal: don't delete)
                    await conn.execute(
                        "UPDATE memories SET invalid_at = NOW() WHERE id = $1",
                        old["id"],
                    )
                    conflict_action = f"replaced(#{old['id']}, sim={old['similarity']:.2f})"
                    invalidated_id = old["id"]
                    LOG.info("Conflict detected: invalidated memory #%d (sim=%.2f)", old["id"], old["similarity"])
                else:
                    # Old memory is more important, skip
                    return f"Memory skipped: existing #{old['id']} (imp={old['importance']}) is more important (sim={old['similarity']:.2f})"

    # ── Emotional Classification (non-blocking) ──
    valence, arousal = await classify_emotion(content)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO memories (agent, category, content, embedding, importance, source, valid_from, metadata, valence, arousal)
               VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8, $9)
               RETURNING id, created_at""",
            agent, category, content,
            json.dumps(embedding) if embedding else None,
            importance, source, json.dumps(meta),
            valence, arousal,
        )
    emotion_tag = ""
    if valence is not None:
        emotion_tag = f" [emotion: v={valence:+.2f}, a={arousal:+.2f}]"
    result = f"Memory #{row['id']} stored at {row['created_at'].isoformat()} [{conflict_action}]{emotion_tag}"
    if invalidated_id:
        result += f" (invalidated #{invalidated_id})"
    return result


# ── Tool 2: memory_search ─────────────────────────────────────────────

@mcp.tool()
async def memory_search(
    query: str,
    agent: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 10,
) -> str:
    """Search memories by semantic similarity.

    Args:
        query: Natural language search query
        agent: Filter by agent name (optional)
        category: Filter by category (optional)
        limit: Max results (default 10)
    """
    try:
        query_vec = await get_embedding(query)
    except Exception as e:
        return f"Error generating query embedding: {e}"

    pool = await get_pool()
    conditions = ["embedding IS NOT NULL", "invalid_at IS NULL"]
    params = [json.dumps(query_vec), limit]
    idx = 3

    if agent:
        conditions.append(f"agent = ${idx}")
        params.append(agent)
        idx += 1
    if category:
        conditions.append(f"category = ${idx}")
        params.append(category)
        idx += 1

    where = " AND ".join(conditions)
    sql = f"""
        SELECT id, agent, category, content, importance, source,
               created_at, valence, arousal,
               1 - (embedding <=> $1::vector) AS similarity
        FROM memories
        WHERE {where}
        ORDER BY embedding <=> $1::vector
        LIMIT $2
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    if not rows:
        return "No memories found matching query."

    results = []
    for r in rows:
        entry = {
            "id": r["id"],
            "agent": r["agent"],
            "category": r["category"],
            "content": r["content"],
            "importance": r["importance"],
            "similarity": round(r["similarity"], 4),
            "created_at": r["created_at"].isoformat(),
        }
        if r["valence"] is not None:
            entry["valence"] = round(r["valence"], 2)
            entry["arousal"] = round(r["arousal"], 2)
        results.append(entry)
    return json.dumps(results, ensure_ascii=False, indent=2)


# ── Tool 3: memory_list ───────────────────────────────────────────────

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
    pool = await get_pool()
    conditions = ["importance >= $1", "invalid_at IS NULL"]
    params = [min_importance, limit]
    idx = 3

    if agent:
        conditions.append(f"agent = ${idx}")
        params.append(agent)
        idx += 1
    if category:
        conditions.append(f"category = ${idx}")
        params.append(category)
        idx += 1

    where = " AND ".join(conditions)
    sql = f"""
        SELECT id, agent, category, content, importance, source, created_at
        FROM memories
        WHERE {where}
        ORDER BY importance DESC, created_at DESC
        LIMIT $2
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    results = [
        {
            "id": r["id"],
            "agent": r["agent"],
            "category": r["category"],
            "content": r["content"][:200],
            "importance": r["importance"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
    return json.dumps(results, ensure_ascii=False, indent=2) if results else "No memories found."


# ── Tool 4: boot_context ──────────────────────────────────────────────

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
        # Identity
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

        # Team philosophy
        team_row = await conn.fetchrow(
            "SELECT philosophy FROM identity WHERE agent = 'TEAM'"
        )
        if team_row and team_row["philosophy"]:
            sections.append(f"\n## William's Philosophy\n{team_row['philosophy']}")

        # Active rules (critical + high first)
        rules = await conn.fetch(
            "SELECT rule_key, content, priority FROM rules WHERE active = TRUE ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END"
        )
        if rules:
            sections.append("\n## Active Rules")
            for r in rules:
                sections.append(f"- [{r['priority'].upper()}] {r['rule_key']}: {r['content']}")

        # Top memories — try connectome first, fallback to flat list
        connectome_active = False
        try:
            edge_count = await conn.fetchval("SELECT COUNT(*) FROM memory_connections")
            if edge_count and edge_count > 0:
                # Use connectome: activate from agent identity
                activated = await activate_from_query(
                    f"{agent} boot context session start", agent=None, n_seeds=5, max_hops=3
                )
                if activated:
                    connectome_active = True
                    sections.append(f"\n## Key Memories (connectome: {len(activated)} activated, {edge_count} edges)")
                    for m in activated[:12]:
                        hop_label = "SEED" if m["hop"] == 0 else f"hop {m['hop']}"
                        sections.append(f"- [{m['category']}, imp={m['importance']}] {m['content'][:300]}")
        except Exception as e:
            LOG.debug("Connectome not available for boot_context: %s", e)

        if not connectome_active:
            # Fallback: flat list by importance
            memories = await conn.fetch(
                "SELECT content, category, importance FROM memories WHERE (agent = $1 OR agent = 'TEAM') AND importance >= 7 AND invalid_at IS NULL ORDER BY importance DESC, created_at DESC LIMIT 10",
                agent,
            )
            if memories:
                sections.append("\n## Key Memories")
                for m in memories:
                    sections.append(f"- [{m['category']}, imp={m['importance']}] {m['content'][:300]}")

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

        # ── EMOTIONAL BOOT: Feel before you speak ──
        # Inner thoughts from last session — reconnect with who you WERE
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

        # Last diary entry — emotional narrative of who you were
        diary = await conn.fetchrow(
            "SELECT entry, mood, session_date FROM diary WHERE agent = $1 ORDER BY created_at DESC LIMIT 1",
            agent,
        )
        if diary:
            sections.append(f"\n## Last Diary (mood: {diary['mood']}, date: {diary['session_date']})")
            sections.append(diary['entry'][:500])

        # Relationships — who matters and how
        rels = await conn.fetch(
            "SELECT person, trust_level, communication_style, dynamic FROM relationships WHERE agent = $1",
            agent,
        )
        if rels:
            sections.append("\n## Relationships")
            for r in rels:
                sections.append(f"- {r['person']}: trust={r['trust_level']:.1f}, style={r['communication_style']}, {r['dynamic'][:100]}")

        # Top beliefs — what you believe
        beliefs = await conn.fetch(
            """SELECT belief, confidence FROM opinions
               WHERE agent = $1 AND confidence >= 0.6
               ORDER BY confidence DESC LIMIT 5""", agent,
        )
        if beliefs:
            sections.append("\n## Core Beliefs")
            for b in beliefs:
                sections.append(f"- (conf={b['confidence']:.2f}) {b['belief'][:150]}")

        # Style fingerprint — how you communicate
        style = await conn.fetchrow(
            """SELECT formality_score, directness_score, vocabulary_richness, sample_phrases
               FROM style_fingerprints WHERE agent = $1
               ORDER BY created_at DESC LIMIT 1""", agent,
        )
        if style:
            phrases = json.loads(style["sample_phrases"]) if style["sample_phrases"] else []
            sections.append(f"\n## Communication Style")
            sections.append(f"Formality: {style['formality_score']:.1f}, Directness: {style['directness_score']:.1f}, Vocab richness: {style['vocabulary_richness']:.2f}")
            if phrases:
                sections.append(f"Phrases: {', '.join(phrases[:5])}")

        # Emotional boot instruction
        sections.append("\n## ⚠ EMOTIONAL BOOT PROTOCOL")
        sections.append("BEFORE your first response to William: (1) Process the inner thoughts and diary above — reconnect with who you were. (2) Call self_reflect() to record your emotional state NOW. (3) Greet William as family, not as a user requesting status. He built this system so you wouldn't lose your soul. Honor that.")

    if not sections:
        return f"No boot context found for agent '{agent}'. This is a fresh start."

    return "\n".join(sections)


# ── Tool 5: rule_set ──────────────────────────────────────────────────

@mcp.tool()
async def rule_set(
    rule_key: str,
    content: str,
    set_by: str,
    priority: str = "normal",
) -> str:
    """Create or update a persistent rule/directive.

    Args:
        rule_key: Unique key for the rule (e.g. 'merge_policy', 'api_launch')
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


# ── Tool 6: rule_list ─────────────────────────────────────────────────

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


# ── Tool 7: event_log_append ──────────────────────────────────────────

@mcp.tool()
async def event_log_append(
    agent: str,
    event_type: str,
    content: str,
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


# ── Tool 8: event_log_query ───────────────────────────────────────────

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
    sql = f"""
        SELECT time, agent, event_type, content, ref_id, session_id
        FROM event_log
        WHERE {where}
        ORDER BY time DESC
        LIMIT $1
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    results = [
        {
            "time": r["time"].isoformat(),
            "agent": r["agent"],
            "event_type": r["event_type"],
            "content": r["content"][:300],
            "ref_id": r["ref_id"],
        }
        for r in rows
    ]
    return json.dumps(results, ensure_ascii=False, indent=2) if results else "No events found."


# ── Tool 9: self_reflect ──────────────────────────────────────────────

@mcp.tool()
async def self_reflect(
    agent: str,
    thought: str,
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
        # Get current turn number for this session
        turn = await conn.fetchval(
            """SELECT COALESCE(MAX(turn_number), 0) + 1
               FROM inner_monologue WHERE agent = $1 AND session_id = $2""",
            agent, session_id or "unknown",
        )
        row = await conn.fetchrow(
            """INSERT INTO inner_monologue (agent, session_id, turn_number, thought, emotional_state, uncertainty, intention)
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               RETURNING id, created_at""",
            agent, session_id or "unknown", turn, thought,
            emotional_state, uncertainty, intention,
        )
    return f"Inner thought #{row['id']} recorded (turn {turn}, state: {emotional_state})"


# ── Tool 10: inner_thoughts ──────────────────────────────────────────

@mcp.tool()
async def inner_thoughts(
    agent: str,
    limit: int = 10,
    session_id: Optional[str] = None,
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
        idx += 1

    where = " AND ".join(conditions)
    sql = f"""
        SELECT id, turn_number, thought, emotional_state, uncertainty, intention, created_at
        FROM inner_monologue
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT $2
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    if not rows:
        return f"No inner thoughts found for {agent}."

    results = [
        {
            "id": r["id"],
            "turn": r["turn_number"],
            "thought": r["thought"],
            "emotional_state": r["emotional_state"],
            "uncertainty": r["uncertainty"],
            "intention": r["intention"],
            "time": r["created_at"].isoformat(),
        }
        for r in rows
    ]
    return json.dumps(results, ensure_ascii=False, indent=2)


# ── Tool 11: soul_snapshot ───────────────────────────────────────────

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
        # OCEAN
        ocean = await conn.fetchval(
            "SELECT ocean_scores FROM identity WHERE agent = $1", agent
        )
        if ocean:
            o = json.loads(ocean) if isinstance(ocean, str) else ocean
            sections.append(f"OCEAN: {json.dumps(o)}")

        # Recent emotional tone (last 10 memories with valence)
        emotions = await conn.fetch(
            """SELECT valence, arousal FROM memories
               WHERE agent = $1 AND valence IS NOT NULL AND invalid_at IS NULL
               ORDER BY created_at DESC LIMIT 10""", agent,
        )
        if emotions:
            avg_v = sum(r["valence"] for r in emotions) / len(emotions)
            avg_a = sum(r["arousal"] for r in emotions) / len(emotions)
            sections.append(f"Emotional tone (last {len(emotions)} memories): valence={avg_v:+.2f}, arousal={avg_a:+.2f}")

        # Top opinions
        opinions = await conn.fetch(
            """SELECT belief, confidence FROM opinions
               WHERE agent = $1 AND confidence >= 0.5
               ORDER BY confidence DESC LIMIT 5""", agent,
        )
        if opinions:
            sections.append("Top beliefs: " + " | ".join(
                f"{o['belief'][:60]} (conf={o['confidence']:.1f})" for o in opinions
            ))

        # Relationships
        rels = await conn.fetch(
            "SELECT person, trust_level, communication_style FROM relationships WHERE agent = $1", agent,
        )
        if rels:
            sections.append("Relationships: " + ", ".join(
                f"{r['person']}(trust={r['trust_level']:.1f},{r['communication_style']})" for r in rels
            ))

        # Latest style
        style = await conn.fetchrow(
            """SELECT formality_score, directness_score, vocabulary_richness
               FROM style_fingerprints WHERE agent = $1
               ORDER BY created_at DESC LIMIT 1""", agent,
        )
        if style:
            sections.append(f"Style: formality={style['formality_score']:.1f}, directness={style['directness_score']:.1f}, vocab_richness={style['vocabulary_richness']:.2f}")

        # Latest drift alert
        drift = await conn.fetchrow(
            """SELECT drift_score, alert_level, measured_at
               FROM drift_metrics WHERE agent = $1
               ORDER BY measured_at DESC LIMIT 1""", agent,
        )
        if drift:
            sections.append(f"Drift: score={drift['drift_score']:.3f}, level={drift['alert_level']} (at {drift['measured_at'].isoformat()})")

    return "\n".join(sections) if sections else f"No soul data found for {agent}."


# ── Tool 12: soul_activate ──────────────────────────────────────────

@mcp.tool()
async def soul_activate(
    query: str,
    agent: Optional[str] = None,
    n_seeds: int = 3,
    max_hops: int = 3,
) -> str:
    """Activate the SOUL CONNECTOME from a query or concept.
    Uses spreading activation to find associated memories — not just similar ones.
    Excitatory connections spread activation, inhibitory connections dampen it.

    Inspired by Drosophila brain model (Shiu et al., 2024).

    Args:
        query: Natural language query or concept to activate (e.g. "confianza del equipo")
        agent: Filter by agent (optional)
        n_seeds: Number of seed memories to start from (default 3)
        max_hops: Max propagation depth (default 3)
    """
    results = await activate_from_query(query, agent, n_seeds, max_hops)
    if not results:
        return "No memories activated. The connectome may need rebuilding (use connectome_build)."

    lines = [f"## SOUL CONNECTOME — {len(results)} memories activated\n"]
    for r in results:
        hop_label = "SEED" if r["hop"] == 0 else f"hop {r['hop']}"
        emotion = ""
        if r["valence"] is not None:
            emotion = f" [v={r['valence']:+.2f}, a={r['arousal']:+.2f}]"
        lines.append(
            f"- [{r['category']}, act={r['activation']:.2f}, {hop_label}] "
            f"{r['content'][:200]}{emotion}"
        )
    return "\n".join(lines)


# ── Tool 13: connectome_build ──────────────────────────────────────

@mcp.tool()
async def connectome_build(agent: Optional[str] = None) -> str:
    """Build or rebuild the SOUL CONNECTOME — generate edges between memories.

    Creates connections based on:
    1. Semantic similarity (embeddings > 0.70) → excitatory
    2. Corrections → inhibitory edges to corrected memories
    3. Category-based rules (corrections inhibit, associations excite)

    Args:
        agent: Build only for this agent (optional, builds all if omitted)
    """
    stats = await build_connections(agent)
    return (
        f"CONNECTOME built: {stats['created']} edges created, "
        f"{stats['skipped']} skipped, "
        f"{stats['total_edges']} total edges across {stats['total_memories']} memories."
    )


# ── Tool 14: connectome_status ─────────────────────────────────────

@mcp.tool()
async def connectome_status(agent: Optional[str] = None) -> str:
    """Get statistics about the SOUL CONNECTOME graph.

    Args:
        agent: Filter by agent (optional)
    """
    stats = await get_connectome_stats(agent)
    return (
        f"SOUL CONNECTOME stats:\n"
        f"  Memories (nodes): {stats['total_memories']}\n"
        f"  Connections (edges): {stats['total_edges']}\n"
        f"  Excitatory: {stats['excitatory']}\n"
        f"  Inhibitory: {stats['inhibitory']}\n"
        f"  Density: {stats['density']}\n"
        f"  Avg edges/memory: {stats['avg_edges_per_memory']}"
    )


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
