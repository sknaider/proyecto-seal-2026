"""SEAL Context Compressor — Capa 2: Proactive Compression.

Compresses the middle portion of a session's active turns using an auxiliary LLM
when context usage exceeds COMPRESS_AT_PCT (default 70%).

Called by ContextManager.compress_middle() when estimate_context_pct() >= threshold.
"""
from __future__ import annotations

import json
import logging
import os
from uuid import UUID

import asyncpg

DB_URL = os.environ.get(
    "SEAL_DB_URL",
    "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory",
)
SCHEMA = "soul_v3"

log = logging.getLogger(__name__)

TARGET_RATIO = 0.15    # compress to ~15% of original token count
MIN_TURNS = 30         # don't compress if fewer active turns


async def compress_session_middle(
    agent: str,
    session_id: UUID,
    cfg: object,  # ContextManagerConfig — avoid circular import
) -> str | None:
    conn = await asyncpg.connect(DB_URL, server_settings={"search_path": SCHEMA})
    try:
        rows = await conn.fetch(
            f"""
            SELECT id, turn_index, role, content, tokens_est
              FROM {SCHEMA}.session_turns
             WHERE session_id = $1
               AND externalized_at IS NULL
               AND compressed_at IS NULL
             ORDER BY turn_index
            """,
            session_id,
        )

        total = len(rows)
        if total < MIN_TURNS:
            log.debug("[Compressor] only %d active turns — skip", total)
            return None

        keep_head = getattr(cfg, "keep_head", 6)
        keep_tail = getattr(cfg, "keep_tail", 20)
        middle = rows[keep_head: total - keep_tail]
        if not middle:
            return None

        original_tokens = sum(t["tokens_est"] for t in middle)
        max_summary_tokens = max(200, int(original_tokens * TARGET_RATIO))

        prompt = _build_compress_prompt(agent, middle, max_summary_tokens)

        from aux_llm import get_aux_llm
        llm = get_aux_llm()
        summary = llm.complete(prompt, max_tokens=max_summary_tokens)

        if not summary or not summary.strip():
            log.warning("[Compressor] LLM returned empty summary")
            return None

        from token_estimator import estimate
        summary_tokens = estimate(summary)

        mem_row = await conn.fetchrow(
            f"""
            INSERT INTO {SCHEMA}.memories
                (agent, type, content, category, importance, metadata, created_at)
            VALUES ($1, 'session_summary', $2, 'context', 5, $3::jsonb, now())
            RETURNING id
            """,
            agent,
            summary,
            json.dumps({
                "session_id": str(session_id),
                "covers_turn_range": [middle[0]["turn_index"], middle[-1]["turn_index"]],
                "original_tokens": original_tokens,
                "compressed_tokens": summary_tokens,
                "compression_ratio": round(summary_tokens / original_tokens, 3) if original_tokens else 0,
            }),
        )

        turn_ids = [t["id"] for t in middle]
        await conn.execute(
            f"""
            UPDATE {SCHEMA}.session_turns
               SET compressed_at = now(),
                   content_compressed = $1,
                   memory_id = $2
             WHERE id = ANY($3::bigint[])
            """,
            summary,
            mem_row["id"],
            turn_ids,
        )

        log.info(
            "[Compressor] session %s: %d turns → %d tokens (was %d, ratio %.2f)",
            session_id, len(middle), summary_tokens, original_tokens,
            summary_tokens / original_tokens if original_tokens else 0,
        )
        return summary

    except Exception as exc:
        log.error("[Compressor] failed: %s", exc)
        return None
    finally:
        await conn.close()


def _build_compress_prompt(agent: str, turns: list, max_tokens: int) -> str:
    turns_text = "\n".join(
        f"[{t['role'].upper()} turn {t['turn_index']}] {t['content'][:800]}"
        for t in turns
    )
    return f"""You are a session summarizer for AI agent {agent}.

Compress the following conversation turns into a dense summary of at most {max_tokens} tokens.

MANDATORY — preserve ALL of the following if present:
- Decisions made by William or the team
- Errors encountered and fixes applied
- Tasks in progress and their current state
- Files modified, commands run, tests passed/failed
- Technical constraints or architecture decisions
- Any explicit instructions from William

DO NOT preserve:
- Pleasantries, acknowledgements, repeated context
- Tool call outputs that were superseded by later corrections
- Intermediate reasoning that led nowhere

Output ONLY the summary, no preamble.

---TURNS TO COMPRESS---
{turns_text}
---END TURNS---"""
