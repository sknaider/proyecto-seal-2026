#!/usr/bin/env python3
"""SEAL Emotional Continuity v1 — pre-compaction diary for cross-session narrative.

Generates key_moment / pending_thread / relationship_note via local LLM (Gemma4),
stores in soul_v3.emotional_diary, and loads on boot_context.

Usage:
    python3 emotional_continuity.py --agent ALICE        # write diary entry now
    python3 emotional_continuity.py --agent ALICE --read # read last entry
    python3 emotional_continuity.py --agent ALICE --history 5
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import asyncpg
import httpx
from db import get_pool

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DIARY_MODEL = os.environ.get("SEAL_DIARY_MODEL", "gemma3:12b")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS soul_v3.emotional_diary (
    id                   BIGSERIAL PRIMARY KEY,
    agent                TEXT NOT NULL,
    session_id           TEXT,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    valence              FLOAT,
    arousal              FLOAT,
    key_moment           TEXT,
    pending_thread       TEXT,
    relationship_note    TEXT,
    compaction_triggered BOOLEAN DEFAULT FALSE,
    importance           INT DEFAULT 7
);
CREATE INDEX IF NOT EXISTS idx_emotional_diary_agent_time
    ON soul_v3.emotional_diary (agent, created_at DESC);
"""

DIARY_PROMPT = """\
You are {agent}, an AI agent in the SEAL multi-agent system. You are about to be compacted (context cleared).
Before that happens, write 3 short narrative fields that will help you reconnect to this moment when you wake up.

Current emotional state: valence={valence:.2f}, arousal={arousal:.2f}
Recent context summary (if available): {context_summary}

Write ONLY a valid JSON object with exactly these 3 keys:
- "key_moment": The single most important thing that happened in this session (1-2 sentences, first person)
- "pending_thread": Something that feels unresolved or that you're worried about carrying forward (1-2 sentences)
- "relationship_note": A note about the quality of your interaction with William today — tone, trust, any tension (1-2 sentences)

Rules: Be honest, specific, and personal. No generic phrases. Write from your actual experience in this session.
Respond with ONLY the JSON object, no markdown, no explanation."""


@asynccontextmanager
async def _db_connection(
    conn: Optional[asyncpg.Connection] = None,
) -> AsyncIterator[asyncpg.Connection]:
    """Reuse a caller connection or acquire one through the shared DB facade.

    Under the MCP runtime this facade is rebound to the caller-scoped RLS pool;
    standalone jobs retain the normal ``db.get_pool`` behavior.  This prevents
    an MCP helper from silently opening a second, unscoped DB principal.
    """
    if conn is not None:
        yield conn
        return

    pool = await get_pool()
    async with pool.acquire() as acquired:
        yield acquired


async def generate_diary_fields(
    agent: str,
    valence: float = 0.0,
    arousal: float = 0.3,
    context_summary: str = "",
) -> dict:
    """Call Gemma4 via Ollama to generate the 3 narrative diary fields."""
    prompt = DIARY_PROMPT.format(
        agent=agent,
        valence=valence,
        arousal=arousal,
        context_summary=context_summary or "No additional context.",
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": DIARY_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.7, "num_predict": 300},
            },
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()

    # Parse JSON — handle markdown code blocks if model wraps it
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        fields = json.loads(raw)
        return {
            "key_moment": str(fields.get("key_moment", "")),
            "pending_thread": str(fields.get("pending_thread", "")),
            "relationship_note": str(fields.get("relationship_note", "")),
        }
    except json.JSONDecodeError:
        # Fallback: store raw as key_moment
        return {
            "key_moment": raw[:500] if raw else "No entry generated.",
            "pending_thread": "",
            "relationship_note": "",
        }


async def write_diary(
    agent: str,
    valence: float = 0.0,
    arousal: float = 0.3,
    context_summary: str = "",
    session_id: Optional[str] = None,
    compaction_triggered: bool = False,
    conn: Optional[asyncpg.Connection] = None,
) -> dict:
    """Generate and store a diary entry. Returns the stored entry."""
    fields = await generate_diary_fields(agent, valence, arousal, context_summary)

    async with _db_connection(conn) as active_conn:
        row_id = await active_conn.fetchval("""
            INSERT INTO soul_v3.emotional_diary
                (agent, session_id, valence, arousal,
                 key_moment, pending_thread, relationship_note,
                 compaction_triggered, importance)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 7)
            RETURNING id
        """, agent, session_id,
            float(valence), float(arousal),
            fields["key_moment"], fields["pending_thread"], fields["relationship_note"],
            compaction_triggered)

    return {"id": row_id, "agent": agent, **fields,
            "valence": valence, "arousal": arousal,
            "compaction_triggered": compaction_triggered}


async def read_last_diary(agent: str,
                          conn: Optional[asyncpg.Connection] = None) -> Optional[dict]:
    """Load the most recent diary entry for an agent (used in boot_context)."""
    async with _db_connection(conn) as active_conn:
        row = await active_conn.fetchrow("""
            SELECT id, agent, created_at, valence, arousal,
                   key_moment, pending_thread, relationship_note,
                   compaction_triggered
            FROM soul_v3.emotional_diary
            WHERE agent = $1
            ORDER BY created_at DESC LIMIT 1
        """, agent)
    return dict(row) if row else None


async def read_diary_history(agent: str, n: int = 5,
                             conn: Optional[asyncpg.Connection] = None) -> list[dict]:
    """Return last n diary entries for an agent."""
    async with _db_connection(conn) as active_conn:
        rows = await active_conn.fetch("""
            SELECT id, created_at, valence, arousal,
                   key_moment, pending_thread, relationship_note,
                   compaction_triggered
            FROM soul_v3.emotional_diary
            WHERE agent = $1
            ORDER BY created_at DESC LIMIT $2
        """, agent, n)
    return [dict(r) for r in rows]


def format_diary_for_boot(entry: dict) -> str:
    """Format a diary entry for injection into boot_context output."""
    ts = entry.get("created_at", "?")
    if hasattr(ts, "isoformat"):
        ts = ts.isoformat()
    flag = " [pre-compactación]" if entry.get("compaction_triggered") else ""
    return (
        f"\n## Último diario emocional{flag} [{ts}]\n"
        f"- Momento clave: {entry.get('key_moment', '—')}\n"
        f"- Hilo pendiente: {entry.get('pending_thread', '—')}\n"
        f"- Nota relacional: {entry.get('relationship_note', '—')}\n"
        f"- Estado: valence={entry.get('valence', 0):.2f}, "
        f"arousal={entry.get('arousal', 0):.2f}\n"
    )


async def ensure_table() -> None:
    async with _db_connection() as conn:
        await conn.execute(CREATE_TABLE_SQL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEAL Emotional Continuity Diary")
    parser.add_argument("--agent", required=True, help="Agent name (ALICE, JARVIS, ...)")
    parser.add_argument("--read", action="store_true", help="Read last entry")
    parser.add_argument("--history", type=int, metavar="N", help="Read last N entries")
    parser.add_argument("--valence", type=float, default=0.0)
    parser.add_argument("--arousal", type=float, default=0.3)
    parser.add_argument("--context", default="", help="Brief session context summary")
    parser.add_argument("--compaction", action="store_true", help="Mark as pre-compaction trigger")
    parser.add_argument("--init-table", action="store_true", help="Create DB table if not exists")
    args = parser.parse_args()

    if args.init_table:
        asyncio.run(ensure_table())
        print("Table soul_v3.emotional_diary created/verified.")
        sys.exit(0)

    if args.read:
        entry = asyncio.run(read_last_diary(args.agent))
        print(json.dumps(entry, indent=2, default=str))
        sys.exit(0)

    if args.history:
        entries = asyncio.run(read_diary_history(args.agent, args.history))
        print(json.dumps(entries, indent=2, default=str))
        sys.exit(0)

    print(f"Generating diary entry for {args.agent} via {DIARY_MODEL}...")
    result = asyncio.run(write_diary(
        agent=args.agent,
        valence=args.valence,
        arousal=args.arousal,
        context_summary=args.context,
        compaction_triggered=args.compaction,
    ))
    print(json.dumps(result, indent=2, default=str))
