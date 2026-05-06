"""SEAL Turn Extractor — Capa 4: Proactive Turn Extraction (spec v3 §7)

Single-pass ADD-only fact extraction per significant turn via Ollama qwen2.5:7b.
Stores facts as memories type='fact' with category + confidence.
Fires from PostToolBatch hook after significant tool batches.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

DB_URL = os.environ.get("SEAL_DB_URL", "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory")
SCHEMA = "soul_v3"
log = logging.getLogger(__name__)

EXTRACT_TOOLS = {"Edit", "Write", "Bash", "memory_store", "mcp__seal-memory__memory_store"}
SKIP_PATTERNS = re.compile(r"heartbeat|monitor|ack|\[cron\]|\[dum\]|tick \d+", re.IGNORECASE)
MIN_CONTENT_CHARS = 200
MAX_FACTS = 5
EXTRACT_TIMEOUT_S = 8.0

VALID_CATEGORIES = {
    "decision", "error_resolved", "file_modified",
    "user_request", "technical_fact", "open_question",
}

EXTRACT_PROMPT = """\
Extract up to {max_facts} atomic facts from the following agent turn and tool actions.

Agent: {agent}
Turn content:
---
{turn_content}
---
Tool actions executed:
{tool_actions}

Rules:
- Each fact is a standalone, verifiable statement.
- Valid categories: decision | error_resolved | file_modified | user_request | technical_fact | open_question
- DO NOT repeat trivial items (acks, heartbeats, monitor events).
- DO NOT interpret — extract only what is literally present or very strongly implied.
- Respond with JSON only (no preamble, no markdown):
  [
    {{"statement": "...", "category": "...", "confidence": 0.0-1.0}},
    ...
  ]"""


def is_significant(content: str, tool_names: list[str] | None = None) -> bool:
    if len(content) < MIN_CONTENT_CHARS:
        return False
    if SKIP_PATTERNS.search(content):
        return False
    if tool_names and any(t in EXTRACT_TOOLS for t in tool_names):
        return True
    return len(content) >= MIN_CONTENT_CHARS * 2


def _summarize_tools(tool_names: list[str], tool_outputs: list[str] | None = None) -> str:
    if not tool_names:
        return "(none)"
    lines = []
    for i, name in enumerate(tool_names):
        out = ""
        if tool_outputs and i < len(tool_outputs) and tool_outputs[i]:
            out = f" → {str(tool_outputs[i])[:120]}"
        lines.append(f"  - {name}{out}")
    return "\n".join(lines)


def _parse_facts(response: str) -> list[dict]:
    text = response.strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return []
        facts = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            stmt = str(item.get("statement", "")).strip()
            cat = str(item.get("category", "technical_fact")).strip()
            try:
                conf = float(item.get("confidence", 0.7))
            except (ValueError, TypeError):
                conf = 0.7
            if not stmt:
                continue
            if cat not in VALID_CATEGORIES:
                cat = "technical_fact"
            conf = max(0.0, min(1.0, conf))
            facts.append({"statement": stmt, "category": cat, "confidence": conf})
        return facts
    except Exception:
        return []


async def extract_and_store(
    agent: str,
    session_id: str | UUID,
    turn_index: int,
    turn_content: str,
    tool_names: list[str] | None = None,
    tool_outputs: list[str] | None = None,
) -> int:
    """
    Single-pass ADD-only fact extraction for one turn.
    Returns number of facts stored.
    """
    if not is_significant(turn_content, tool_names):
        return 0

    from aux_llm import get_aux_llm
    llm = get_aux_llm()

    prompt = EXTRACT_PROMPT.format(
        max_facts=MAX_FACTS,
        agent=agent,
        turn_content=turn_content[:2000],
        tool_actions=_summarize_tools(tool_names or [], tool_outputs),
    )

    try:
        response = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, lambda: llm.complete(prompt, max_tokens=400)
            ),
            timeout=EXTRACT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.warning("[TurnExtractor] timeout — skipping turn %d", turn_index)
        return 0
    except Exception as e:
        log.warning("[TurnExtractor] llm error: %s — skipping", e)
        return 0

    if not response or not response.strip():
        return 0

    facts = _parse_facts(response)
    facts = facts[:MAX_FACTS]

    if not facts:
        return 0

    conn = await asyncpg.connect(DB_URL, server_settings={"search_path": SCHEMA})
    now = datetime.now(timezone.utc)
    stored = 0
    try:
        for fact in facts:
            await conn.execute(
                f"""
                INSERT INTO {SCHEMA}.memories
                    (agent, memory_type, content, category, importance, metadata, created_at)
                VALUES ($1, 'semantic', $2, $3, $4, $5::jsonb, $6)
                """,
                agent,
                fact["statement"],
                fact["category"],
                int(fact["confidence"] * 10),
                json.dumps({
                    "session_id": str(session_id),
                    "turn_index": turn_index,
                    "confidence": fact["confidence"],
                    "extracted_at": now.isoformat(),
                    "source": "turn_extractor_v3",
                }),
                now,
            )
            stored += 1
    except Exception as e:
        log.error("[TurnExtractor] db error: %s", e)
    finally:
        await conn.close()

    if stored:
        log.info("[TurnExtractor] %s turn %d → %d facts stored", agent, turn_index, stored)
    return stored


async def extract_batch(
    agent: str,
    session_id: str | UUID,
    turns: list[dict],
) -> int:
    """Process multiple turns in sequence. Returns total facts stored."""
    total = 0
    for turn in turns:
        n = await extract_and_store(
            agent=agent,
            session_id=session_id,
            turn_index=turn.get("turn_index", 0),
            turn_content=str(turn.get("content", "")),
            tool_names=turn.get("tool_names"),
            tool_outputs=turn.get("tool_outputs"),
        )
        total += n
    return total
