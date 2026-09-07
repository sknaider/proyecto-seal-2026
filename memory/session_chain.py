"""SEAL Session Chain — Capa 3: Session digest + chain continuity.

At session close: generate a compact digest (<1500 tokens) and write it to
soul_v3.session_chain, chained via parent_id.

At session open (boot/post_compact): load parent digest and inject into context.

Insight from MemGPT/Roo Code research: the digest is the bridge between
ephemeral RAM (Claude context) and persistent disk (SOUL). It must preserve
decisions, tasks, errors, and William's instructions — nothing else.
"""
from __future__ import annotations

import json
import logging
import os
from uuid import UUID

import asyncpg

DB_URL = os.environ.get(
    "SEAL_DB_URL",
    "postgresql://seal:REDACTADO@localhost:5433/seal_memory",
)
SCHEMA = "soul_v3"
MAX_DIGEST_TOKENS = 1500

log = logging.getLogger(__name__)


async def write_digest(agent: str, session_id: UUID) -> str | None:
    """Generate and persist a session digest. Returns digest text or None."""
    conn = await asyncpg.connect(DB_URL, server_settings={"search_path": SCHEMA})
    try:
        chain_row = await conn.fetchrow(
            f"SELECT turn_count FROM {SCHEMA}.session_chain WHERE id = $1",
            session_id,
        )
        if not chain_row:
            log.warning("[Chain] session %s not found in session_chain", session_id)
            return None

        tail_turns = await conn.fetch(
            f"""
            SELECT role, content, turn_index
              FROM {SCHEMA}.session_turns
             WHERE session_id = $1
             ORDER BY turn_index DESC
             LIMIT 30
            """,
            session_id,
        )
        tail_turns = list(reversed(tail_turns))

        ws_row = await conn.fetchrow(
            f"""
            SELECT task_name, step, total_steps, description,
                   active_hypotheses, current_constraints, pending_validations,
                   emotional_state, last_intention, state
              FROM {SCHEMA}.working_state
             WHERE agent = $1::varchar
            """,
            agent,
        )

        summary_memories = await conn.fetch(
            f"""
            SELECT content FROM {SCHEMA}.memories
             WHERE agent = $1
               AND type = 'session_summary'
               AND metadata->>'session_id' = $2
             ORDER BY created_at DESC
             LIMIT 3
            """,
            agent,
            str(session_id),
        )

        digest = _build_digest(
            agent=agent,
            session_id=session_id,
            turn_count=chain_row["turn_count"],
            tail_turns=tail_turns,
            working_state=dict(ws_row) if ws_row else {},
            session_summaries=[r["content"] for r in summary_memories],
        )

        from token_estimator import estimate
        digest_tokens = estimate(digest)

        await conn.execute(
            f"""
            UPDATE {SCHEMA}.session_chain
               SET digest = $1,
                   digest_tokens = $2,
                   ended_at = COALESCE(ended_at, now()),
                   ended_reason = COALESCE(ended_reason, 'auto_compact')
             WHERE id = $3
            """,
            digest,
            digest_tokens,
            session_id,
        )

        try:
            await conn.execute(
                f"""
                INSERT INTO {SCHEMA}.memories
                    (agent, type, content, category, importance, metadata, created_at)
                VALUES ($1, 'session_digest', $2, 'identity', 8, $3::jsonb, now())
                """,
                agent,
                digest,
                json.dumps({
                    "session_id": str(session_id),
                    "turn_count": chain_row["turn_count"],
                    "digest_tokens": digest_tokens,
                }),
            )
        except Exception:
            pass

        log.info("[Chain] wrote digest for %s session %s (%d tokens)", agent, session_id, digest_tokens)
        return digest

    except Exception as exc:
        log.error("[Chain] write_digest failed: %s", exc)
        return None
    finally:
        await conn.close()


async def load_parent_digest(agent: str) -> dict | None:
    """Load the most recent closed session digest for this agent."""
    conn = await asyncpg.connect(DB_URL, server_settings={"search_path": SCHEMA})
    try:
        row = await conn.fetchrow(
            f"""
            SELECT id, digest, digest_tokens, turn_count, ended_at, ended_reason
              FROM {SCHEMA}.session_chain
             WHERE agent = $1
               AND ended_at IS NOT NULL
               AND digest IS NOT NULL
             ORDER BY ended_at DESC
             LIMIT 1
            """,
            agent,
        )
        if not row:
            return None
        return {
            "session_id": str(row["id"]),
            "digest": row["digest"],
            "digest_tokens": row["digest_tokens"],
            "turn_count": row["turn_count"],
            "ended_at": row["ended_at"].isoformat() if row["ended_at"] else None,
            "ended_reason": row["ended_reason"],
        }
    finally:
        await conn.close()


async def open_chained_session(agent: str) -> UUID:
    """Open a new session chained to the last closed one. Returns new session_id."""
    conn = await asyncpg.connect(DB_URL, server_settings={"search_path": SCHEMA})
    try:
        last = await conn.fetchrow(
            f"""
            SELECT id FROM {SCHEMA}.session_chain
             WHERE agent = $1 AND ended_at IS NOT NULL
             ORDER BY ended_at DESC LIMIT 1
            """,
            agent,
        )
        parent_id = last["id"] if last else None

        row = await conn.fetchrow(
            f"""
            INSERT INTO {SCHEMA}.session_chain (agent, parent_id, started_at)
            VALUES ($1, $2, now())
            RETURNING id
            """,
            agent,
            parent_id,
        )
        session_id = row["id"]

        await conn.execute(
            f"""
            UPDATE {SCHEMA}.working_state
               SET active_session_id = $1
             WHERE agent = $2::varchar
            """,
            session_id,
            agent,
        )

        log.info("[Chain] opened chained session %s (parent=%s) for %s", session_id, parent_id, agent)
        return session_id
    finally:
        await conn.close()


def _build_digest(
    agent: str,
    session_id: UUID,
    turn_count: int,
    tail_turns: list,
    working_state: dict,
    session_summaries: list[str],
) -> str:
    from aux_llm import get_aux_llm

    sections = [f"# Session Digest — {agent}"]
    sections.append(f"session_id: {session_id}")
    sections.append(f"turn_count: {turn_count}")

    if working_state.get("task_name"):
        step = working_state.get("step") or 0
        total = working_state.get("total_steps") or 0
        sections.append(f"\n## Active Task\n{working_state['task_name']}")
        if total:
            sections.append(f"Progress: step {step}/{total}")
        if working_state.get("description"):
            sections.append(f"Description: {working_state['description']}")

    if working_state.get("active_hypotheses"):
        items = working_state["active_hypotheses"]
        if isinstance(items, list) and items:
            sections.append("\n## Active Hypotheses")
            sections.extend(f"- {h}" for h in items[:5])

    if working_state.get("current_constraints"):
        items = working_state["current_constraints"]
        if isinstance(items, list) and items:
            sections.append("\n## Constraints")
            sections.extend(f"- {c}" for c in items[:5])

    if working_state.get("emotional_state"):
        sections.append(f"\n## Agent State\nemotional: {working_state['emotional_state']}")
    if working_state.get("last_intention"):
        sections.append(f"last_intention: {working_state['last_intention'][:200]}")

    if session_summaries:
        sections.append("\n## Compressed Session History")
        for s in session_summaries[:2]:
            sections.append(s[:600])

    if tail_turns:
        tail_text = "\n".join(
            f"[{t['role'].upper()}] {str(t['content'])[:300]}"
            for t in tail_turns[-15:]
        )
        sections.append(f"\n## Recent Turns (last 15)\n{tail_text}")

    raw_digest = "\n".join(sections)
    budget_chars = MAX_DIGEST_TOKENS * 4

    if len(raw_digest) <= budget_chars:
        return raw_digest

    prompt = f"""Compress this session digest to under {MAX_DIGEST_TOKENS} tokens.
Preserve: active task, decisions, errors, William's instructions, last known agent state.
Remove: redundant details, pleasantries, superseded information.

---
{raw_digest[:budget_chars * 2]}
---

Output the compressed digest only, no preamble."""

    try:
        llm = get_aux_llm()
        return llm.complete(prompt, max_tokens=MAX_DIGEST_TOKENS)
    except Exception:
        return raw_digest[:budget_chars]
