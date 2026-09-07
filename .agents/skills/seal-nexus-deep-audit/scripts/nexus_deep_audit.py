#!/usr/bin/env python3
"""Evidence snapshot for NEXUS deep audits.
Read-only. Outputs markdown with SQL-backed checks.
"""
from __future__ import annotations
import argparse, asyncio, os, textwrap
from datetime import datetime, timezone
import asyncpg

DB_URL = os.getenv("SEAL_DB_URL", "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory")
AGENTS = ["ADA", "ALICE", "JARVIS", "NEXUS", "DUM"]

CHECKS = [
    ("Recall Router usage last 6h", """
        SELECT agent, COUNT(*) AS calls, COALESCE(ROUND(AVG(elapsed_ms))::int,0) AS avg_ms,
               MAX(created_at) AS latest
        FROM soul_v3.recall_audit
        WHERE created_at > NOW() - INTERVAL '6 hours'
        GROUP BY agent ORDER BY agent
    """),
    ("Inner monologue last 7d", """
        SELECT agent, COUNT(*) AS thoughts, MAX(created_at) AS latest
        FROM soul_v3.inner_monologue
        WHERE created_at > NOW() - INTERVAL '7 days'
        GROUP BY agent ORDER BY agent
    """),
    ("Session memory coverage", """
        SELECT agent, COUNT(*) AS rows, MAX(updated_at) AS newest
        FROM soul_v3.session_memory
        GROUP BY agent ORDER BY agent
    """),
    ("Distilled exchanges gaps", """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE summary IS NULL OR btrim(summary) = '') AS missing_summary,
               COUNT(*) FILTER (WHERE session_id IS NULL) AS missing_session_id
        FROM soul_v3.distilled_exchanges
    """),
    ("BM25 NULL by source_tier", """
        SELECT COALESCE(source_tier,'(null)') AS source_tier,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE embedding_bm25 IS NULL) AS bm25_null
        FROM soul_v3.memories
        GROUP BY source_tier ORDER BY bm25_null DESC, total DESC
    """),
]

def fmt(v):
    if v is None: return "NULL"
    if isinstance(v, datetime): return v.astimezone(timezone.utc).isoformat()
    return str(v)

def table(rows):
    if not rows: return "(no rows)"
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(fmt(r[c])) for r in rows)) for c in cols}
    out = [" | ".join(c.ljust(widths[c]) for c in cols), " | ".join("-"*widths[c] for c in cols)]
    for r in rows:
        out.append(" | ".join(fmt(r[c]).ljust(widths[c]) for c in cols))
    return "\n".join(out)

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="NEXUS")
    args = ap.parse_args()
    conn = await asyncpg.connect(DB_URL)
    try:
        print(f"# NEXUS Deep Audit Snapshot — {datetime.now(timezone.utc).isoformat()}")
        print(f"Agent focus: {args.agent.upper()}\n")
        for title, sql in CHECKS:
            sql_clean = textwrap.dedent(sql).strip()
            rows = await conn.fetch(sql_clean)
            print(f"## {title}")
            print("SQL:")
            print("```sql")
            print(sql_clean)
            print("```")
            print("Output:")
            print("```text")
            print(table([dict(r) for r in rows]))
            print("```\n")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
