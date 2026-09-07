#!/usr/bin/env python3
"""SEAL Session Distill Pipeline — H2.7

Procesa sesiones sin distilaciones y las comprime en distilled_exchanges.
Standalone — no importa mcp_server (evita overhead del servidor MCP).

Basado en Structured Distillation (arxiv 2603.13017):
  11x compression, 96.8% vocabulary preserved.

Uso:
    python3 session_distill_pipeline.py           # procesa todas las sesiones pendientes
    python3 session_distill_pipeline.py ADA       # solo agente ADA
    python3 session_distill_pipeline.py --dry-run # show plan sin ejecutar
    python3 session_distill_pipeline.py --metrics # solo reporte de métricas

Scheduled: cron daily a las 03:00 (post-sesión)

Owner: ADA (H2.7) | Meta M1: distill_coverage ≥ 0.85
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import asyncpg
import httpx

LOG = logging.getLogger("distill-pipeline")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

LIMA_TZ = ZoneInfo("America/Lima")

DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"
EMBED_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "nomic-embed-text"

# ─── Distillation prompt ───────────────────────────────────────────────────
DISTILL_PROMPT = """\
You are a session compressor for an AI agent team (SEAL).
Compress this exchange into EXACTLY this JSON format. Use SURVIVING VOCABULARY — reuse exact technical terms from the exchange, do NOT paraphrase.
{overlap_section}
Exchange:
{exchange_text}

Output JSON (and nothing else):
{{
  "exchange_core": "<what was accomplished, 1-2 sentences, commit-message style>",
  "specific_context": "<one distinguishing technical detail + emotional state if present + key decision if any>",
  "room_assignments": [
    {{"type": "<file|concept|workflow>", "key": "<identifier>", "label": "<human-readable>"}}
  ],
  "files_touched": ["<file paths or MCP tools used>"]
}}

Rules:
- exchange_core: max 30 words, past tense, factual
- specific_context: max 40 words, include the most unique technical detail
- room_assignments: 1-3 entries, types are: file (specific file), concept (technical concept), workflow (process/pipeline)
"""


async def get_embedding(text: str) -> list | None:
    """Get embedding via Ollama nomic-embed-text."""
    import urllib.request
    try:
        payload = json.dumps({"model": EMBED_MODEL, "input": text}).encode()
        req = urllib.request.Request(
            EMBED_URL, data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data["embeddings"][0]
    except Exception:
        return None


async def ollama_distill(exchange_text: str, overlap_text: str = "") -> dict | None:
    """Call Ollama to distill exchange into structured JSON."""
    overlap_section = (
        f"\nPrevious context (maintain narrative continuity):\n{overlap_text}\n"
        if overlap_text else ""
    )
    prompt = DISTILL_PROMPT.format(
        exchange_text=exchange_text[:3000],
        overlap_section=overlap_section,
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(OLLAMA_URL, json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 300},
            })
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()

            # Extract JSON — handle markdown wrapping
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                raw = raw[start:end]

            return json.loads(raw)
    except Exception as e:
        LOG.warning("Ollama distillation failed: %s", e)
        return None


async def find_sessions_needing_distill(conn: asyncpg.Connection, agent: str | None) -> list[dict]:
    """Find sessions with candidatas > 0 but distilled = 0."""
    where = f"AND s.agent = '{agent}'" if agent else ""
    rows = await conn.fetch(f"""
        SELECT
            s.id, s.agent, s.started_at, s.ended_at,
            COUNT(de.id) AS distilled,
            COUNT(DISTINCT m.id) FILTER (
                WHERE m.importance >= 6
                AND m.created_at BETWEEN s.started_at AND COALESCE(s.ended_at, NOW())
            ) AS candidatas
        FROM sessions s
        LEFT JOIN distilled_exchanges de ON de.session_id = s.id
        LEFT JOIN memories m ON m.agent = s.agent
        WHERE 1=1 {where}
        GROUP BY s.id, s.agent, s.started_at, s.ended_at
        HAVING COUNT(DISTINCT m.id) FILTER (
            WHERE m.importance >= 6
            AND m.created_at BETWEEN s.started_at AND COALESCE(s.ended_at, NOW())
        ) > 0
        AND COUNT(de.id) = 0
        ORDER BY s.started_at DESC
        LIMIT 50
    """)
    return [dict(r) for r in rows]


async def get_session_memories(
    conn: asyncpg.Connection,
    agent: str,
    started_at: datetime,
    ended_at: datetime | None,
) -> list[dict]:
    """Get memories in session time window."""
    end = ended_at or (started_at + timedelta(hours=12))
    rows = await conn.fetch("""
        SELECT id, content, category, importance, created_at
        FROM memories
        WHERE agent = $1
          AND created_at BETWEEN $2 AND $3
          AND (invalid_at IS NULL OR invalid_at > NOW())
        ORDER BY created_at ASC
    """, agent, started_at, end)
    return [dict(r) for r in rows]


def group_into_windows(memories: list[dict], window_minutes: int = 30) -> list[list[dict]]:
    """Group memories into time windows."""
    if not memories:
        return []

    windows = []
    current = [memories[0]]
    window_start = memories[0]["created_at"]

    for mem in memories[1:]:
        if (mem["created_at"] - window_start) > timedelta(minutes=window_minutes):
            windows.append(current)
            current = [mem]
            window_start = mem["created_at"]
        else:
            current.append(mem)

    if current:
        windows.append(current)

    return windows


async def distill_session(
    conn: asyncpg.Connection,
    session: dict,
    dry_run: bool = False,
) -> dict:
    """Distill a single session. Returns result dict."""
    agent = session["agent"]
    session_id = session["id"]
    started_at = session["started_at"]
    ended_at = session.get("ended_at")

    memories = await get_session_memories(conn, agent, started_at, ended_at)
    if not memories:
        return {"session_id": session_id, "distilled": 0, "skipped": "no_memories"}

    windows = group_into_windows(memories, window_minutes=30)
    if not windows:
        return {"session_id": session_id, "distilled": 0, "skipped": "no_windows"}

    distilled_count = 0
    overlap_text = ""

    # Get existing overlap if any
    try:
        prev_overlap = await conn.fetchval("""
            SELECT overlap_context FROM distilled_exchanges
            WHERE agent = $1 AND session_id = $2 AND overlap_context IS NOT NULL
            ORDER BY created_at DESC LIMIT 1
        """, agent, session_id)
        if prev_overlap:
            overlap_text = prev_overlap
    except Exception:
        pass

    for i, window in enumerate(windows):
        combined = "\n".join(
            f"[{m['category']}] (imp={m['importance']}) {m['content'][:300]}"
            for m in window
        )
        if len(combined) < 100:
            continue

        source_tokens = len(combined.split())

        if dry_run:
            LOG.info("[DRY RUN] Would distill window %d/%d: %d tokens", i+1, len(windows), source_tokens)
            distilled_count += 1
            continue

        distilled = await ollama_distill(combined, overlap_text)
        if not distilled:
            LOG.warning("Distillation failed for session %s window %d", session_id, i)
            continue

        exchange_core = distilled.get("exchange_core", "")
        specific_context = distilled.get("specific_context", "")
        room_assignments = distilled.get("room_assignments", [])
        files_touched = distilled.get("files_touched", [])

        distilled_text = f"{exchange_core}\n{specific_context}"
        distilled_tokens = len(distilled_text.split())

        # Generate overlap for continuity
        exchange_words = combined.split()
        new_overlap = " ".join(exchange_words[-200:]) if len(exchange_words) > 200 else combined
        new_overlap = f"[prev: {exchange_core}] {new_overlap}"[:1000]

        # Exchange time = middle of window
        ply_start = i * 10
        ply_end = (i + 1) * 10 - 1
        exchange_time = window[len(window) // 2]["created_at"]

        try:
            row = await conn.fetchrow("""
                INSERT INTO distilled_exchanges
                (session_id, agent, exchange_core, specific_context,
                 room_assignments, files_touched, ply_start, ply_end,
                 source_tokens, distilled_tokens, exchange_time, overlap_context)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11, $12)
                RETURNING id
            """,
                session_id, agent, exchange_core, specific_context,
                json.dumps(room_assignments, ensure_ascii=False),
                files_touched, ply_start, ply_end,
                source_tokens, distilled_tokens, exchange_time, new_overlap,
            )
            distill_id = row["id"]

            # Embed in Qdrant
            emb = await get_embedding(distilled_text)
            if emb:
                try:
                    from qdrant_client import AsyncQdrantClient
                    from qdrant_client.models import PointStruct
                    qdrant = AsyncQdrantClient(url="http://localhost:6333")
                    qdrant_id = distill_id + 100000
                    await qdrant.upsert(
                        collection_name="soul_memories",
                        points=[PointStruct(
                            id=qdrant_id,
                            vector=emb,
                            payload={
                                "agent": agent, "content": distilled_text,
                                "category": "distilled_exchange",
                                "importance": 6, "session_id": session_id,
                                "source": "session_distill_pipeline",
                                "rooms": [r.get("key", "") for r in room_assignments],
                            },
                        )],
                    )
                    await conn.execute(
                        "UPDATE distilled_exchanges SET qdrant_point_id = $1 WHERE id = $2",
                        qdrant_id, distill_id,
                    )
                    await qdrant.close()
                except Exception as e:
                    LOG.debug("Qdrant embed skipped: %s", e)

            compression = round(source_tokens / max(distilled_tokens, 1), 1)
            LOG.info(
                "Distilled #%d: %s window %d/%d — %dx compression. Core: %s",
                distill_id, session_id, i+1, len(windows), compression, exchange_core[:60],
            )

            overlap_text = new_overlap
            distilled_count += 1

        except Exception as e:
            LOG.warning("DB write failed for session %s window %d: %s", session_id, i, e)

    return {
        "session_id": session_id,
        "agent": agent,
        "windows": len(windows),
        "distilled": distilled_count,
    }


async def run_metrics(conn: asyncpg.Connection, agent: str | None) -> None:
    """Print M1/M2 metrics."""
    where = f"AND s.agent = '{agent}'" if agent else ""

    m1_rows = await conn.fetch(f"""
        SELECT s.agent,
               COUNT(DISTINCT s.id) AS total_sessions,
               SUM(CASE WHEN de.cnt > 0 THEN 1 ELSE 0 END) AS distilled_sessions,
               ROUND(AVG(COALESCE(de.cnt::numeric, 0) / NULLIF(cand.n::numeric, 0)), 3) AS avg_coverage
        FROM sessions s
        LEFT JOIN (
            SELECT session_id, agent, COUNT(*) AS cnt
            FROM distilled_exchanges GROUP BY session_id, agent
        ) de ON de.session_id = s.id AND de.agent = s.agent
        LEFT JOIN (
            SELECT s2.id,
                COUNT(DISTINCT m.id) FILTER (
                    WHERE m.importance >= 6
                    AND m.created_at BETWEEN s2.started_at AND COALESCE(s2.ended_at, NOW())
                ) AS n
            FROM sessions s2
            LEFT JOIN memories m ON m.agent = s2.agent
            GROUP BY s2.id
        ) cand ON cand.id = s.id
        WHERE 1=1 {where}
        GROUP BY s.agent
    """)

    m2_rows = await conn.fetch(f"""
        SELECT s.agent,
               PERCENTILE_CONT(0.5) WITHIN GROUP (
                   ORDER BY EXTRACT(EPOCH FROM (s.started_at - m_last.last_mem_time)) / 60
               ) AS recency_p50_min
        FROM sessions s
        JOIN LATERAL (
            SELECT MAX(created_at) AS last_mem_time
            FROM memories WHERE agent = s.agent AND created_at < s.started_at
        ) m_last ON true
        WHERE 1=1 {where.replace('AND s.', 'AND ').replace('AND agent', 'AND s.agent')}
        GROUP BY s.agent
    """)

    print(f"\n{'='*55}")
    print(f"  SEAL Distill Metrics — {datetime.now(LIMA_TZ).strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")

    print("\n[M1] distill_coverage (meta ≥0.85):")
    for r in m1_rows:
        cov = float(r['avg_coverage'] or 0)
        flag = "✅" if cov >= 0.85 else "🔴"
        print(f"  {r['agent']:8} total={r['total_sessions']:3} distilled_sessions={r['distilled_sessions']:3} avg_cov={cov:.3f} {flag}")

    print("\n[M2] boot_memory_recency p50 (meta <30 min):")
    for r in m2_rows:
        mins = float(r['recency_p50_min'] or 0)
        flag = "✅" if mins < 30 else "🔴"
        print(f"  {r['agent']:8} {mins:.1f} min {flag}")

    print(f"{'='*55}\n")


async def main_async(agent: str | None, dry_run: bool, metrics_only: bool) -> None:
    conn = await asyncio.wait_for(asyncpg.connect(DB_URL), timeout=5.0)
    try:
        if metrics_only:
            await run_metrics(conn, agent)
            return

        sessions = await find_sessions_needing_distill(conn, agent)
        LOG.info("Found %d sessions needing distillation", len(sessions))

        if not sessions:
            LOG.info("Nothing to distill — all sessions covered.")
            await run_metrics(conn, agent)
            return

        total_distilled = 0
        for sess in sessions:
            result = await distill_session(conn, sess, dry_run=dry_run)
            total_distilled += result.get("distilled", 0)
            LOG.info("Session %s: %d/%d windows distilled",
                     result["session_id"], result.get("distilled", 0), result.get("windows", 0))

        print(f"\n✅ Pipeline complete — {total_distilled} exchanges distilled from {len(sessions)} sessions")

        if not dry_run:
            await run_metrics(conn, agent)

    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="SEAL session_distill_pipeline (H2.7)")
    parser.add_argument("agent", nargs="?", help="Agent: ADA, JARVIS, ALICE (default: all)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--metrics", action="store_true", help="Only show metrics")
    args = parser.parse_args()

    agent = args.agent.upper() if args.agent else None
    asyncio.run(main_async(agent, args.dry_run, args.metrics))


if __name__ == "__main__":
    main()
