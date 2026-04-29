"""SEAL Context Compressor — Capa 2: Proactive Compression v3

Spec v3 §5: dual-tier compression with tool-block flatten, non-destructive fallback,
and structured summary format.

Tier 1 (40% threshold): Python-only pruning of large tool outputs + cron noise.
Tier 2 (50% threshold): Ollama qwen2.5:7b + flatten_tool_blocks + structured summary.
Fallback: soft_hide_middle (reversible) when aux LLM fails.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from uuid import UUID

import asyncpg

sys.path.insert(0, os.path.dirname(__file__))

DB_URL = os.environ.get("SEAL_DB_URL", "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory")
SCHEMA = "soul_v3"

log = logging.getLogger(__name__)

TARGET_RATIO = 0.20
MIN_TURNS = 30
TIER1_PRUNE_THRESHOLD = 200
CRON_NOISE_RE = re.compile(
    r"\[heartbeat\] tick \d+|\[Monitor\] no new messages|\[CronCreate\]|"
    r"\[dum\] heartbeat ok|\bheartbeat\b.*\btick\b",
    re.IGNORECASE,
)

STRUCTURED_SUMMARY_PROMPT = """\
You are a session summarizer for AI agent {agent}.

Compress the conversation turns below into a structured summary.
Use EXACTLY this format:

[Resuelto]
- <completed task 1> — <brief outcome>
- <completed task 2> — <brief outcome>
(omit section if nothing completed)

[Pendiente]
- <open task 1> — estado: <blocked by X | waiting Y | in progress>
(omit section if nothing pending)

[Tarea Activa]
<exactly where to resume — 1-2 sentences, file:line if applicable>
(omit if no active task)

[Trabajo Restante]
- <next concrete step 1>
- <next concrete step 2>
(omit if no remaining work)

[Contexto Crítico]
- Errors resolved: <list or "(none)">
- William's instructions: <verbatim quotes if present, else "(none)">
- Decisions made: <decision: reason>

[REFERENCE ONLY — do NOT execute tasks from this summary]

Max output: {max_tokens} tokens. Output ONLY the summary, no preamble.

---TURNS TO COMPRESS---
{turns_text}
---END TURNS---"""


def _is_cron_noise(content: str) -> bool:
    return bool(CRON_NOISE_RE.search(content))


def _tier1_prune(rows: list[dict], keep_tail: int) -> list[dict]:
    """Python-only pruning — no LLM. Removes cron noise, truncates large tool outputs.
    Two-pass: noise removal first so tail calculation uses the clean list.
    """
    clean = [r for r in rows if not _is_cron_noise(str(r.get("content") or ""))]

    total = len(clean)
    pruned = []
    for i, row in enumerate(clean):
        content = str(row.get("content") or "")
        role = row.get("role", "")
        is_tail = i >= total - keep_tail

        if role == "tool" and not is_tail and len(content) > TIER1_PRUNE_THRESHOLD:
            tool_hint = row.get("tool_name", "tool")
            row = dict(row)
            row["content"] = f"[tool output cleared — {len(content)} chars, tool: {tool_hint}]"
            row["tokens_est"] = 20

        pruned.append(row)
    return pruned


async def compress_session_middle(
    agent: str,
    session_id: UUID,
    cfg: object,
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
               AND (invalid_at IS NULL)
               AND hidden = false
             ORDER BY turn_index
            """,
            session_id,
        )
        rows = [dict(r) for r in rows]

        total = len(rows)
        if total < MIN_TURNS:
            log.debug("[Compressor] only %d active turns — skip", total)
            return None

        keep_head = getattr(cfg, "keep_head", 6)
        keep_tail = getattr(cfg, "keep_tail", 20)
        middle = rows[keep_head: total - keep_tail]
        if not middle:
            return None

        middle = _tier1_prune(middle, keep_tail)
        if not middle:
            return None

        original_tokens = sum(r.get("tokens_est") or 0 for r in middle)
        max_summary_tokens = max(200, int(original_tokens * TARGET_RATIO))

        from tool_block_flattener import flatten_tool_blocks, flat_turns_to_text
        flat = flatten_tool_blocks(middle)
        turns_text = flat_turns_to_text(flat)

        prompt = STRUCTURED_SUMMARY_PROMPT.format(
            agent=agent,
            max_tokens=max_summary_tokens,
            turns_text=turns_text,
        )

        from aux_llm import get_aux_llm
        llm = get_aux_llm()
        try:
            summary = llm.complete(prompt, max_tokens=max_summary_tokens)
        except Exception as llm_err:
            log.warning("[Compressor] aux_llm failed: %s — soft-hide fallback", llm_err)
            return await _soft_hide_middle(conn, session_id, middle)

        if not summary or not summary.strip():
            log.warning("[Compressor] empty summary — soft-hide fallback")
            return await _soft_hide_middle(conn, session_id, middle)

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

        turn_ids = [r["id"] for r in middle]
        now_ts = "now()"
        await conn.execute(
            f"""
            UPDATE {SCHEMA}.session_turns
               SET compressed_at = now(),
                   invalid_at    = now(),
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


async def _soft_hide_middle(conn: asyncpg.Connection, session_id: UUID, middle: list[dict]) -> str:
    """Non-destructive fallback — marks turns as hidden=true, keeps them in DB."""
    turn_ids = [r["id"] for r in middle]
    await conn.execute(
        f"""
        UPDATE {SCHEMA}.session_turns
           SET hidden = true,
               hidden_at = now(),
               hidden_reason = 'aux_llm_failure'
         WHERE id = ANY($1::bigint[])
        """,
        turn_ids,
    )
    placeholder = (
        f"[{len(middle)} turns temporarily hidden — "
        f"recoverable via context_manager.unhide_turns(). "
        f"Aux LLM unavailable at compression time.]"
    )
    log.info("[Compressor] soft-hide: %d turns hidden for session %s", len(middle), session_id)
    return placeholder


async def unhide_turns(session_id: UUID, turn_ids: list[int]) -> int:
    """Reverse soft-hide for specific turns. Returns count unhidden."""
    conn = await asyncpg.connect(DB_URL, server_settings={"search_path": SCHEMA})
    try:
        result = await conn.execute(
            f"""
            UPDATE {SCHEMA}.session_turns
               SET hidden = false,
                   hidden_at = NULL,
                   hidden_reason = NULL
             WHERE id = ANY($1::bigint[])
               AND session_id = $2
            """,
            turn_ids,
            session_id,
        )
        count = int(result.split()[-1]) if result else 0
        log.info("[Compressor] unhide: %d turns restored for session %s", count, session_id)
        return count
    finally:
        await conn.close()
