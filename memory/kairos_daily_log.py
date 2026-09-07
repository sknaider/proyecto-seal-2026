#!/usr/bin/env python3
"""kairos_daily_log.py — SEAL-native KAIROS daily log writer.

Queries Soul DB for today's events and recent memories, then appends
an entry to logs/YYYY/MM/YYYY-MM-DD.md (KAIROS-compatible layout).
Called from end_session.sh after each session.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))


LOGS_DIR = Path(__file__).parent / "logs"
DB_DSN = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"


async def get_today_events(agent: str) -> list[dict]:
    """Get today's event_log entries for this agent."""
    try:
        import asyncpg
        pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=2)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT event_type, content, metadata, created_at
                FROM soul_v3.event_log
                WHERE agent = $1
                  AND created_at >= NOW() - INTERVAL '12 hours'
                ORDER BY created_at DESC
                LIMIT 50
                """,
                agent,
            )
        await pool.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return [{"event_type": "error", "content": str(e)}]


async def get_recent_memories(agent: str) -> list[dict]:
    """Get memories stored in the last 12 hours."""
    try:
        import asyncpg
        pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=2)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT content, memory_type, importance, created_at
                FROM soul_v3.memories
                WHERE agent = $1
                  AND created_at >= NOW() - INTERVAL '12 hours'
                ORDER BY importance DESC, created_at DESC
                LIMIT 20
                """,
                agent,
            )
        await pool.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return []


def build_log_entry(agent: str, events: list[dict], memories: list[dict]) -> str:
    """Build a KAIROS-format daily log entry."""
    lima_tz = timezone(timedelta(hours=-5))
    now = datetime.now(lima_tz)
    ts = now.strftime("%H:%M Lima")

    lines = [f"## {ts} — {agent} session\n"]

    # Events summary
    if events:
        lines.append("### Actividad")
        event_counts: dict[str, int] = {}
        summaries = []
        for e in events:
            etype = e.get("event_type", "unknown")
            event_counts[etype] = event_counts.get(etype, 0) + 1
            s = e.get("content", "")
            if s and len(summaries) < 8:
                summaries.append(f"- {s[:120]}")
        for etype, count in sorted(event_counts.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"- {etype}: ×{count}")
        if summaries:
            lines.append("")
            lines.append("### Resumen de acciones")
            lines.extend(summaries)

    # Key memories
    if memories:
        lines.append("")
        lines.append("### Memorias guardadas hoy")
        for m in memories[:8]:
            content = (m.get("content") or "")[:150]
            mtype = m.get("memory_type", "?")
            imp = m.get("importance", 0) or 0
            lines.append(f"- [{mtype} imp={imp:.2f}] {content}")

    lines.append("")
    return "\n".join(lines)


async def write_daily_log(agent: str) -> str:
    lima_tz = timezone(timedelta(hours=-5))
    now = datetime.now(lima_tz)

    year = now.strftime("%Y")
    month = now.strftime("%m")
    date_str = now.strftime("%Y-%m-%d")

    log_dir = LOGS_DIR / year / month / agent.upper()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{date_str}.md"

    # Create header if new file
    if not log_file.exists():
        log_file.write_text(f"# {agent} — Daily Log {date_str}\n\n")

    events = await get_today_events(agent)
    memories = await get_recent_memories(agent)

    if not events and not memories:
        entry = f"## {now.strftime('%H:%M Lima')} — {agent} session (no activity recorded)\n\n"
    else:
        entry = build_log_entry(agent, events, memories)

    with open(log_file, "a") as f:
        f.write(entry)

    return str(log_file)


if __name__ == "__main__":
    agent = sys.argv[1] if len(sys.argv) > 1 else "JARVIS"
    log_path = asyncio.run(write_daily_log(agent))
    print(f"[kairos_daily_log] Written: {log_path}")
