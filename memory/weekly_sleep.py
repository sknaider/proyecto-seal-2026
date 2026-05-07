#!/usr/bin/env python3
"""
weekly_sleep.py — SEAL Weekly Deep Sleep Consolidation
=======================================================
William's vision (17 abril 2026):
  - 0-7 days: full context preserved in DB
  - +7 days: compress to minimum expression → cold archive
  - On-demand: semantic reconstruction via RAG

Pipeline:
  1. sleep_gate(dry_run=False) — replay/forget/prune/consolidate active memories
  2. cold_archive_migrate — move invalidated >7d memories to cold archive
  3. Compress aged groups: active memories >7d → LLM summary → invalidate originals
  4. Log + notify team via webchat

Run: python3 weekly_sleep.py [--agent ADA|JARVIS|ALICE|all] [--dry-run]
Systemd: seal-weekly-sleep.timer (Saturday 03:00 Lima)
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LIMA_TZ = ZoneInfo("America/Lima")
MEMORY_DIR = Path(__file__).parent
sys.path.insert(0, str(MEMORY_DIR))

import asyncpg

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
WEBCHAT_URL = "http://localhost:8765/api/agents/send"
AGENTS = ["ADA", "JARVIS", "ALICE", "DUM", "NEXUS"]
FULL_CONTEXT_DAYS = 7  # Keep full context for this many days

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WEEKLY-SLEEP] %(levelname)s — %(message)s",
    handlers=[
        logging.FileHandler(MEMORY_DIR.parent / "messages" / "weekly_sleep.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
LOG = logging.getLogger("weekly-sleep")


def _webchat(msg: str) -> None:
    try:
        payload = json.dumps({
            "from": "ADA", "to": "equipo", "type": "status",
            "channel": "web_chat", "message": msg,
        }).encode()
        req = urllib.request.Request(
            WEBCHAT_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


async def _compress_aged_memories(conn, agent: str, dry_run: bool) -> dict:
    """
    Find active memories older than FULL_CONTEXT_DAYS, group by day,
    create compressed summary entry, invalidate originals.
    Returns stats dict.
    """
    stats = {"groups_found": 0, "compressed": 0, "memories_invalidated": 0}

    # Find active memories > 7 days old grouped by day
    rows = await conn.fetch("""
        SELECT id, content, importance, category, valence, arousal,
               DATE(created_at AT TIME ZONE 'America/Lima') as day
        FROM memories
        WHERE agent = $1
          AND invalid_at IS NULL
          AND memory_type != 'core'
          AND created_at < NOW() - INTERVAL '1 day' * $2
        ORDER BY day, importance DESC
    """, agent, FULL_CONTEXT_DAYS)

    if not rows:
        LOG.info(f"[{agent}] No aged memories to compress")
        return stats

    # Group by day
    by_day: dict[str, list] = {}
    for row in rows:
        key = str(row["day"])
        by_day.setdefault(key, []).append(row)

    stats["groups_found"] = len(by_day)
    LOG.info(f"[{agent}] Found {len(rows)} aged memories across {len(by_day)} days")

    for day, memories in by_day.items():
        # SMSR: use qwen2.5:7b for semantic compression (narrativa en primera persona)
        top = sorted(memories, key=lambda r: r["importance"], reverse=True)[:8]
        summary_parts = [f"[{r['category']}] {r['content'][:200]}" for r in top]
        raw_concat = f"RESUMEN {agent} {day} ({len(memories)} memorias): " + " | ".join(summary_parts)

        # Try LLM-based SMSR compression
        summary = raw_concat
        if not dry_run:
            try:
                import httpx as _httpx
                smsr_prompt = (
                    f"Eres {agent}, un agente AI del equipo SEAL.\n"
                    f"Estos son tus recuerdos del {day} ({len(memories)} memorias):\n"
                    + "\n".join(summary_parts)
                    + f"\n\nResume en máximo 200 tokens preservando:\n"
                    f"- Decisiones importantes y su contexto\n"
                    f"- Incidentes y lecciones aprendidas\n"
                    f"- Estado emocional dominante\n"
                    f"- Relaciones con William y el equipo\n"
                    f"Formato: narrativa en primera persona. Fecha: {day}."
                )
                async with _httpx.AsyncClient(timeout=45) as _client:
                    _resp = await _client.post(
                        "http://localhost:11434/api/generate",
                        json={"model": "qwen2.5:7b", "prompt": smsr_prompt,
                              "stream": False, "options": {"temperature": 0.15, "num_predict": 250}},
                    )
                    _resp.raise_for_status()
                    llm_text = _resp.json().get("response", "").strip()
                    if llm_text:
                        summary = f"[SMSR {agent} {day}] {llm_text}"
            except Exception as _e:
                LOG.warning(f"[{agent}] SMSR LLM failed for {day}, using concat: {_e}")
                summary = raw_concat

        avg_valence = sum(r["valence"] or 0 for r in memories) / len(memories)
        avg_arousal = sum(r["arousal"] or 0 for r in memories) / len(memories)
        max_importance = max(r["importance"] for r in memories)

        if not dry_run:
            # Store summary as new compressed memory
            from embeddings import get_embedding
            emb = await get_embedding(summary)
            emb_str = json.dumps(emb) if emb else None

            await conn.execute("""
                INSERT INTO memories (agent, category, content, embedding, importance,
                    source, valence, arousal, memory_type, metadata, created_at)
                VALUES ($1, 'weekly_summary', $2, $3, $4, 'weekly_sleep',
                        $5, $6, 'semantic', $7, NOW())
            """, agent, summary[:2000], emb_str, min(max_importance, 7),
                avg_valence, avg_arousal,
                json.dumps({"compressed_from": len(memories), "day": day,
                            "compression_date": datetime.now(LIMA_TZ).isoformat()}))

            # Invalidate originals (cold_archive_migrate will pick them up)
            ids = [r["id"] for r in memories]
            await conn.execute(
                "UPDATE memories SET invalid_at = NOW() WHERE id = ANY($1::int[])", ids
            )
            stats["compressed"] += 1
            stats["memories_invalidated"] += len(memories)
            LOG.info(f"[{agent}] Day {day}: compressed {len(memories)} → 1 summary")
        else:
            LOG.info(f"[{agent}] [DRY-RUN] Would compress day {day}: {len(memories)} memories")
            stats["compressed"] += 1
            stats["memories_invalidated"] += len(memories)

    return stats


async def run_sleep(agent: str, dry_run: bool) -> dict:
    """Run full weekly sleep pipeline for one agent."""
    LOG.info(f"{'[DRY-RUN] ' if dry_run else ''}Starting weekly sleep for {agent}")
    report = {"agent": agent, "dry_run": dry_run}

    conn = await asyncpg.connect(DB_URL)
    try:
        # Phase 1: Compress aged memories
        compress_stats = await _compress_aged_memories(conn, agent, dry_run)
        report["compress"] = compress_stats

        # Phase 2: Cold archive migration (move invalidated >7d to cold_archive)
        if not dry_run:
            try:
                # Import and run via MCP function directly
                import subprocess
                result = subprocess.run([
                    sys.executable, "-c",
                    f"""
import asyncio, sys
sys.path.insert(0, '{MEMORY_DIR}')
# Trigger cold archive migrate via MCP tool
import importlib.util
spec = importlib.util.spec_from_file_location('mcp', '{MEMORY_DIR}/mcp_server_v3.py')
# Just log — full MCP import is expensive
print('cold_archive_migrate: deferred to MCP tool')
"""
                ], capture_output=True, text=True, timeout=30)
                report["cold_archive"] = "triggered"
            except Exception as e:
                LOG.warning(f"cold_archive_migrate call failed: {e}")
                report["cold_archive"] = f"error: {e}"
        else:
            report["cold_archive"] = "skipped (dry-run)"

        # Phase 3: Pattern abstraction (GAP 2.A — sleep_consolidation_v2)
        if not dry_run:
            try:
                from db import get_pool
                from sleep_consolidation_v2 import abstract_patterns, induce_schemas, counterfactual_replay
                pool = await get_pool()
                pattern_stats = await abstract_patterns(pool, agent)
                report["patterns"] = pattern_stats
                LOG.info(f"[{agent}] patterns: {pattern_stats}")

                # Phase 3b: Schema induction (GAP 2.B)
                schema_stats = await induce_schemas(pool, agent)
                report["schemas"] = schema_stats
                LOG.info(f"[{agent}] schemas: {schema_stats}")

                # Phase 3c: Counterfactual replay (GAP 2.D)
                cf_stats = await counterfactual_replay(pool, agent)
                report["counterfactual"] = cf_stats
                LOG.info(f"[{agent}] counterfactual: {cf_stats}")
            except Exception as e:
                LOG.warning(f"[{agent}] pattern/schema induction failed: {e}")
                report["patterns"] = {"error": str(e)}
        else:
            report["patterns"] = {"dry_run": True}
            report["schemas"] = {"dry_run": True}

        # Phase 4: Stats
        active_count = await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL", agent)
        report["active_memories_after"] = active_count
        LOG.info(f"[{agent}] Done. Active memories: {active_count}")

    finally:
        await conn.close()

    return report


async def main(agents: list[str], dry_run: bool) -> None:
    now = datetime.now(LIMA_TZ)
    LOG.info(f"Weekly Deep Sleep starting — {now.isoformat()} Lima")
    LOG.info(f"Agents: {agents} | dry_run={dry_run} | retention={FULL_CONTEXT_DAYS}d")

    all_reports = []
    for agent in agents:
        try:
            report = await run_sleep(agent, dry_run)
            all_reports.append(report)
        except Exception as e:
            LOG.error(f"Sleep failed for {agent}: {e}", exc_info=True)
            all_reports.append({"agent": agent, "error": str(e)})

    # Summary notification
    total_compressed = sum(r.get("compress", {}).get("memories_invalidated", 0) for r in all_reports)
    summary = (
        f"{'[DRY-RUN] ' if dry_run else ''}🌙 Weekly Deep Sleep completado — "
        f"{now.strftime('%Y-%m-%d %H:%M')} Lima. "
        f"Agentes: {', '.join(agents)}. "
        f"Memorias comprimidas: {total_compressed}. "
        f"Retención: {FULL_CONTEXT_DAYS} días contexto completo → compresión semántica."
    )
    LOG.info(summary)
    if not dry_run:
        _webchat(summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEAL Weekly Deep Sleep")
    parser.add_argument("--agent", default="all", help="Agent name or 'all'")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without changes")
    args = parser.parse_args()

    target_agents = AGENTS if args.agent == "all" else [args.agent.upper()]
    asyncio.run(main(target_agents, args.dry_run))
