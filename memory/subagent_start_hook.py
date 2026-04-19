#!/usr/bin/env python3
"""SEAL Subagent Start Hook (Fase 5 — Soul Injection)

Claude Code SubagentStart hook. Injects condensed SOUL context into every
subagent so they inherit team rules and corrections.

Graceful degradation: if DB is unavailable, returns {} and the subagent
runs without soul context.
"""

import asyncio
import json
import os
import sys
import time

DEFAULT_DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"


async def fetch_soul_context(agent: str, dsn: str) -> dict:
    """Query PostgreSQL for active rules and recent corrections."""
    import asyncpg

    conn = None
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(dsn),
            timeout=2.0,
        )

        # Top 3 active rules (critical/high priority)
        rules_rows = await conn.fetch(
            """
            SELECT content FROM rules
            WHERE active = true
              AND LOWER(priority) IN ('critical', 'high')
            ORDER BY
              CASE LOWER(priority) WHEN 'critical' THEN 0 ELSE 1 END,
              created_at DESC
            LIMIT 3
            """,
        )

        # Top 3 recent corrections
        corrections_rows = await conn.fetch(
            """
            SELECT content FROM memories
            WHERE agent = $1 AND category = 'correction'
            ORDER BY created_at DESC
            LIMIT 3
            """,
            agent,
        )

        rules = [r["content"] for r in rules_rows]
        corrections = [r["content"] for r in corrections_rows]

        return {"rules": rules, "corrections": corrections}

    finally:
        if conn:
            await conn.close()


def build_context(agent: str, data: dict) -> str:
    """Build condensed context string (~500 tokens max)."""
    lines = [f"[SOUL — Subagent context from {agent}]"]

    if data.get("rules"):
        lines.append("Rules:")
        for i, rule in enumerate(data["rules"], 1):
            # Truncate each rule to ~100 chars
            truncated = rule[:100] + "..." if len(rule) > 100 else rule
            lines.append(f"  {i}. {truncated}")

    if data.get("corrections"):
        lines.append("Corrections:")
        for i, corr in enumerate(data["corrections"], 1):
            truncated = corr[:100] + "..." if len(corr) > 100 else corr
            lines.append(f"  {i}. {truncated}")

    lines.append(f"Agent: {agent} — part of Team SEAL")
    return "\n".join(lines)


def main():
    # Read stdin JSON
    try:
        event = json.loads(sys.stdin.read())
    except Exception:
        event = {}

    # Detect parent agent
    agent = os.environ.get("SEAL_AGENT", "UNKNOWN")
    dsn = os.environ.get("SEAL_PG_DSN", DEFAULT_DSN)

    try:
        data = asyncio.run(fetch_soul_context(agent, dsn))
        context_str = build_context(agent, data)

        output = {
            "hookSpecificOutput": {
                "additionalContext": context_str
            }
        }
        json.dump(output, sys.stdout)

    except Exception:
        # Graceful degradation — subagent runs without soul
        json.dump({}, sys.stdout)

    sys.exit(0)


if __name__ == "__main__":
    main()
