#!/usr/bin/env python3
"""
SEAL Soul — Consolidation ("Sleep" Process)
Runs periodically (every 24h or end-of-session) to:
1. Summarize recent events into high-level memories
2. Detect patterns and create insights
3. Merge redundant memories
4. Update OCEAN scores based on interaction patterns
5. Archive old events (>30 days → consolidated only)

Usage:
    python3 consolidate.py                # full consolidation
    python3 consolidate.py --dry-run      # show what would happen
    python3 consolidate.py --hours 6      # consolidate last 6 hours only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")

import asyncpg
import httpx

from db import get_pool, close_pool, DB_URL
from embeddings import get_embedding

LOG = logging.getLogger("seal-consolidate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"


async def llm_summarize(prompt: str) -> str:
    """Call Ollama qwen2.5:7b for text summarization."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 500},
        })
        resp.raise_for_status()
        return resp.json().get("response", "").strip()


async def consolidate_events(pool: asyncpg.Pool, hours_back: int = 24, dry_run: bool = False) -> list[str]:
    """Summarize recent events into high-level memories."""
    actions = []
    cutoff = datetime.now(LIMA_TZ) - timedelta(hours=hours_back)

    async with pool.acquire() as conn:
        # Get events grouped by agent
        agents = await conn.fetch(
            "SELECT DISTINCT agent FROM event_log WHERE created_at > $1", cutoff
        )

        for agent_row in agents:
            agent = agent_row["agent"]
            events = await conn.fetch(
                """SELECT created_at, event_type, content, ref_id
                   FROM event_log WHERE agent = $1 AND created_at > $2
                   ORDER BY created_at ASC""",
                agent, cutoff,
            )

            if len(events) < 3:
                continue

            # Build event text for summarization
            event_text = "\n".join(
                f"[{e['time'].strftime('%H:%M')}] {e['event_type']}: {e['content'][:200]}"
                for e in events
            )

            prompt = f"""Summarize the following activity log for AI agent {agent} into 2-3 key insights.
Focus on: decisions made, problems solved, patterns observed, and emotional/relational moments.
Be concise. Write in English. Each insight should be one sentence.

Activity log:
{event_text}

Key insights:"""

            if dry_run:
                actions.append(f"[DRY RUN] Would summarize {len(events)} events for {agent}")
                continue

            try:
                summary = await llm_summarize(prompt)
                if summary:
                    # Store as consolidated memory
                    embedding = await get_embedding(summary)
                    await conn.execute(
                        """INSERT INTO memories (agent, category, content, embedding, importance, source, valid_from, metadata)
                           VALUES ($1, 'insight', $2, $3, 7, 'consolidation', NOW(),
                                   $4)""",
                        agent, summary,
                        json.dumps(embedding) if embedding else None,
                        json.dumps({"consolidated_events": len(events), "hours_back": hours_back}),
                    )
                    actions.append(f"Consolidated {len(events)} events for {agent} → insight memory")
                    LOG.info("Consolidated %d events for %s", len(events), agent)
            except Exception as e:
                LOG.error("Failed to consolidate events for %s: %s", agent, e)
                actions.append(f"ERROR consolidating {agent}: {e}")

    return actions


async def merge_redundant(pool: asyncpg.Pool, dry_run: bool = False) -> list[str]:
    """Find and merge memories with similarity > 0.90 (same agent, same category)."""
    actions = []

    async with pool.acquire() as conn:
        # Find pairs of very similar valid memories
        pairs = await conn.fetch(
            """SELECT a.id AS id_a, b.id AS id_b,
                      a.content AS content_a, b.content AS content_b,
                      a.importance AS imp_a, b.importance AS imp_b,
                      a.agent, a.category,
                      1 - (a.embedding <=> b.embedding) AS similarity
               FROM memories a
               JOIN memories b ON a.agent = b.agent AND a.category = b.category
                                  AND a.id < b.id
               WHERE a.embedding IS NOT NULL AND b.embedding IS NOT NULL
                 AND a.invalid_at IS NULL AND b.invalid_at IS NULL
                 AND 1 - (a.embedding <=> b.embedding) > 0.90
               ORDER BY similarity DESC
               LIMIT 20"""
        )

        for p in pairs:
            if dry_run:
                actions.append(
                    f"[DRY RUN] Would merge #{p['id_a']} + #{p['id_b']} "
                    f"(sim={p['similarity']:.3f}, {p['agent']}/{p['category']})"
                )
                continue

            # Keep the more important one, invalidate the other
            keep_id = p["id_a"] if p["imp_a"] >= p["imp_b"] else p["id_b"]
            drop_id = p["id_b"] if keep_id == p["id_a"] else p["id_a"]

            await conn.execute(
                "UPDATE memories SET invalid_at = NOW() WHERE id = $1", drop_id
            )
            actions.append(
                f"Merged: kept #{keep_id}, invalidated #{drop_id} "
                f"(sim={p['similarity']:.3f}, {p['agent']}/{p['category']})"
            )
            LOG.info("Merged memories: kept #%d, invalidated #%d (sim=%.3f)", keep_id, drop_id, p["similarity"])

    return actions


async def archive_old_events(pool: asyncpg.Pool, days: int = 30, dry_run: bool = False) -> list[str]:
    """Archive events older than N days (delete from hypertable after consolidation)."""
    actions = []
    cutoff = datetime.now(LIMA_TZ) - timedelta(days=days)

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM event_log WHERE created_at < $1", cutoff
        )

        if count == 0:
            return [f"No events older than {days} days to archive."]

        if dry_run:
            return [f"[DRY RUN] Would archive {count} events older than {days} days"]

        # Only delete if we have consolidated memories covering this period
        consolidated = await conn.fetchval(
            """SELECT COUNT(*) FROM memories
               WHERE source = 'consolidation' AND created_at < $1""",
            cutoff + timedelta(days=1),
        )

        if consolidated == 0:
            actions.append(f"Skipping archive: {count} old events exist but no consolidation memories cover them yet")
            LOG.warning("Skipping archive: no consolidation memories for period before %s", cutoff)
        else:
            await conn.execute("DELETE FROM event_log WHERE created_at < $1", cutoff)
            actions.append(f"Archived: deleted {count} events older than {days} days ({consolidated} consolidation memories exist)")
            LOG.info("Archived %d events older than %d days", count, days)

    return actions


async def detect_drift(pool: asyncpg.Pool, dry_run: bool = False) -> list[str]:
    """Compare current OCEAN/style against baseline and detect personality drift."""
    actions = []

    async with pool.acquire() as conn:
        agents = await conn.fetch("SELECT agent, ocean_scores FROM identity WHERE ocean_scores IS NOT NULL")

        for agent_row in agents:
            agent = agent_row["agent"]
            baseline_ocean = json.loads(agent_row["ocean_scores"]) if isinstance(agent_row["ocean_scores"], str) else agent_row["ocean_scores"]

            if not baseline_ocean:
                continue

            # Measure current OCEAN from recent emotional patterns
            # Valence/arousal patterns map to OCEAN adjustments
            recent = await conn.fetch(
                """SELECT valence, arousal, category FROM memories
                   WHERE agent = $1 AND invalid_at IS NULL AND valence IS NOT NULL
                   AND created_at > NOW() - INTERVAL '7 days'
                   ORDER BY created_at DESC LIMIT 20""", agent,
            )

            if len(recent) < 5:
                actions.append(f"Skipping drift for {agent}: not enough emotional data ({len(recent)} memories)")
                continue

            # Derive measured OCEAN from behavioral signals
            avg_valence = sum(r["valence"] for r in recent) / len(recent)
            avg_arousal = sum(r["arousal"] for r in recent) / len(recent)

            # Heuristic mapping: emotional patterns → OCEAN tendencies
            # High valence + high arousal → higher E (extraversion)
            # Low arousal → higher A (agreeableness, calm)
            # Mixed valence → higher N (neuroticism)
            # More insight/decision categories → higher C (conscientiousness)
            categories = [r["category"] for r in recent]
            insight_ratio = categories.count("insight") / max(len(categories), 1)
            correction_ratio = categories.count("correction") / max(len(categories), 1)

            measured_ocean = dict(baseline_ocean)  # start from baseline
            measured_ocean["E"] = round(min(1.0, max(0.0, baseline_ocean.get("E", 0.5) + avg_valence * 0.05 + avg_arousal * 0.03)), 2)
            measured_ocean["N"] = round(min(1.0, max(0.0, baseline_ocean.get("N", 0.3) + abs(avg_valence) * 0.02 - avg_valence * 0.03)), 2)
            measured_ocean["A"] = round(min(1.0, max(0.0, baseline_ocean.get("A", 0.5) + avg_valence * 0.03 - avg_arousal * 0.02)), 2)
            measured_ocean["C"] = round(min(1.0, max(0.0, baseline_ocean.get("C", 0.7) + insight_ratio * 0.05 + correction_ratio * 0.03)), 2)
            measured_ocean["O"] = round(baseline_ocean.get("O", 0.7), 2)  # Openness is stable

            # Compute drift score (Euclidean distance in OCEAN space)
            drift_score = 0.0
            for dim in ["O", "C", "E", "A", "N"]:
                diff = measured_ocean.get(dim, 0) - baseline_ocean.get(dim, 0)
                drift_score += diff ** 2
            drift_score = round(drift_score ** 0.5, 4)

            # Alert levels
            if drift_score > 0.15:
                alert_level = "high"
            elif drift_score > 0.08:
                alert_level = "medium"
            else:
                alert_level = "normal"

            # Measure style drift
            style_baseline = await conn.fetchrow(
                """SELECT formality_score, directness_score, vocabulary_richness
                   FROM style_fingerprints WHERE agent = $1
                   ORDER BY created_at ASC LIMIT 1""", agent,
            )
            style_current = await conn.fetchrow(
                """SELECT formality_score, directness_score, vocabulary_richness
                   FROM style_fingerprints WHERE agent = $1
                   ORDER BY created_at DESC LIMIT 1""", agent,
            )

            style_base_json = None
            style_curr_json = None
            if style_baseline and style_current and style_baseline["formality_score"] != style_current["formality_score"]:
                style_base_json = {
                    "formality": float(style_baseline["formality_score"]),
                    "directness": float(style_baseline["directness_score"]),
                    "vocab_richness": float(style_baseline["vocabulary_richness"]),
                }
                style_curr_json = {
                    "formality": float(style_current["formality_score"]),
                    "directness": float(style_current["directness_score"]),
                    "vocab_richness": float(style_current["vocabulary_richness"]),
                }

            if dry_run:
                actions.append(f"[DRY RUN] {agent} drift={drift_score:.4f} ({alert_level}), OCEAN: {json.dumps(measured_ocean)}")
                continue

            # Store drift metric
            await conn.execute(
                """INSERT INTO drift_metrics (agent, ocean_measured, ocean_baseline, drift_score, style_measured, style_baseline, alert_level)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                agent,
                json.dumps(measured_ocean),
                json.dumps(dict(baseline_ocean)),
                drift_score,
                json.dumps(style_curr_json) if style_curr_json else None,
                json.dumps(style_base_json) if style_base_json else None,
                alert_level,
            )

            msg = f"Drift {agent}: score={drift_score:.4f} ({alert_level})"
            if alert_level == "high":
                msg += " ⚠ PERSONALITY DRIFT DETECTED — consider recalibration"
                LOG.warning("HIGH DRIFT for %s: %.4f — measured=%s vs baseline=%s",
                           agent, drift_score, json.dumps(measured_ocean), json.dumps(dict(baseline_ocean)))
            else:
                LOG.info("Drift %s: %.4f (%s)", agent, drift_score, alert_level)
            actions.append(msg)

    return actions


async def update_stats(pool: asyncpg.Pool) -> dict:
    """Get current soul statistics."""
    async with pool.acquire() as conn:
        stats = {
            "total_memories": await conn.fetchval("SELECT COUNT(*) FROM memories"),
            "valid_memories": await conn.fetchval("SELECT COUNT(*) FROM memories WHERE invalid_at IS NULL"),
            "invalidated_memories": await conn.fetchval("SELECT COUNT(*) FROM memories WHERE invalid_at IS NOT NULL"),
            "events": await conn.fetchval("SELECT COUNT(*) FROM event_log"),
            "rules": await conn.fetchval("SELECT COUNT(*) FROM rules WHERE active = TRUE"),
            "identities": await conn.fetchval("SELECT COUNT(*) FROM identity"),
        }
    return stats


async def run_consolidation(hours_back: int = 24, dry_run: bool = False):
    """Run full consolidation cycle."""
    pool = await get_pool()
    LOG.info("=" * 60)
    LOG.info("SEAL Soul — Consolidation started (hours_back=%d, dry_run=%s)", hours_back, dry_run)
    LOG.info("=" * 60)

    all_actions = []

    # Step 1: Summarize events
    LOG.info("Step 1: Consolidating events...")
    actions = await consolidate_events(pool, hours_back, dry_run)
    all_actions.extend(actions)
    for a in actions:
        LOG.info("  %s", a)

    # Step 2: Merge redundant memories
    LOG.info("Step 2: Merging redundant memories...")
    actions = await merge_redundant(pool, dry_run)
    all_actions.extend(actions)
    for a in actions:
        LOG.info("  %s", a)

    # Step 3: Archive old events
    LOG.info("Step 3: Archiving old events...")
    actions = await archive_old_events(pool, days=30, dry_run=dry_run)
    all_actions.extend(actions)
    for a in actions:
        LOG.info("  %s", a)

    # Step 4: Drift detection
    LOG.info("Step 4: Detecting personality drift...")
    actions = await detect_drift(pool, dry_run)
    all_actions.extend(actions)
    for a in actions:
        LOG.info("  %s", a)

    # Step 5: Stats
    stats = await update_stats(pool)
    LOG.info("Step 5: Soul stats — %s", json.dumps(stats))

    await close_pool()

    LOG.info("=" * 60)
    LOG.info("Consolidation complete. %d actions taken.", len(all_actions))
    LOG.info("=" * 60)

    return all_actions, stats


async def run_quick_consolidation():
    """Mini-sleep: fast consolidation for end-of-session hook (2-3 min).
    Only merges duplicates and updates OCEAN. No event summarization."""
    actions = []
    pool = await get_pool()

    async with pool.acquire() as conn:
        # 1. Find and merge near-duplicate memories (similarity > 0.90)
        dupes = await conn.fetch("""
            SELECT m1.id AS id1, m2.id AS id2, m1.importance AS imp1, m2.importance AS imp2,
                   1 - (m1.embedding <=> m2.embedding) AS sim
            FROM memories m1
            JOIN memories m2 ON m1.id < m2.id
                AND m1.agent = m2.agent AND m1.category = m2.category
            WHERE m1.invalid_at IS NULL AND m2.invalid_at IS NULL
                AND m1.embedding IS NOT NULL AND m2.embedding IS NOT NULL
                AND 1 - (m1.embedding <=> m2.embedding) > 0.90
            LIMIT 20
        """)
        for d in dupes:
            # Keep the more important one, invalidate the other
            keep = d["id1"] if d["imp1"] >= d["imp2"] else d["id2"]
            drop = d["id2"] if keep == d["id1"] else d["id1"]
            await conn.execute("UPDATE memories SET invalid_at = NOW() WHERE id = $1", drop)
            actions.append(f"Merged #{drop} into #{keep} (sim={d['sim']:.2f})")

    await close_pool()
    return actions


def main():
    parser = argparse.ArgumentParser(description="SEAL Soul — Consolidation")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without doing it")
    parser.add_argument("--hours", type=int, default=24, help="Hours back to consolidate (default 24)")
    parser.add_argument("--quick", action="store_true", help="Mini-sleep: fast dedup + OCEAN only (2-3 min)")
    parser.add_argument("--full", action="store_true", help="Deep-sleep: full consolidation + archive + rebuild")
    args = parser.parse_args()

    if args.quick:
        actions = asyncio.run(run_quick_consolidation())
        print(f"Mini-sleep: {len(actions)} actions")
        for a in actions:
            print(f"  {a}")
        return

    actions, stats = asyncio.run(run_consolidation(args.hours, args.dry_run))

    print(f"\n{'=' * 50}")
    print(f"  SEAL Soul — Consolidation Report")
    print(f"{'=' * 50}")
    for a in actions:
        print(f"  {a}")
    print(f"\n  Stats: {json.dumps(stats, indent=2)}")
    print(f"{'=' * 50}")

    if args.full:
        # Rebuild connectome via Neo4j (v2 architecture)
        print("\n  Rebuilding connectome (Neo4j)...")
        try:
            from mcp_server_v4 import connectome_build
            result = asyncio.run(connectome_build())
            print(f"  {result}")
        except Exception as e:
            print(f"  Neo4j rebuild failed, falling back to PostgreSQL: {e}")
            from connectome import build_connections
            stats = asyncio.run(build_connections())
            print(f"  Connectome (PG fallback): {stats}")


if __name__ == "__main__":
    main()
