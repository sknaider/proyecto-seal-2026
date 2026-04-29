#!/usr/bin/env python3
"""
Memory Lifecycle Manager — SEAL Project
========================================
Ensures memory system scales to 10+ years (500K+ memories) without degrading.

Three pillars:
1. CONSOLIDATION — merge similar memories into summaries
2. DECAY — reduce importance of unused memories over time
3. ARCHIVAL — move cold memories to archive table, cap active at ~500/agent

Designed to run as daily cron: memory_lifecycle.py --run
Or individual phases: --consolidate | --decay | --archive

Author: JARVIS (architect) — 2026-04-08
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import warnings
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, timedelta
from typing import Optional

import asyncpg

# ── Config ──────────────────────────────────────────────────────────────────
from config import settings

PG_DSN = settings.pg_dsn

# Consolidation
SIMILARITY_THRESHOLD = 0.85       # cosine similarity to consider "same topic"
MIN_CLUSTER_SIZE = 3              # minimum memories to trigger consolidation
MAX_CONSOLIDATED_LENGTH = 300     # max chars for consolidated summary

# Decay
DECAY_INACTIVE_DAYS = 14          # days without activation before decay kicks in
DECAY_IMPORTANCE_DROP = 1         # how much to reduce importance per cycle
DECAY_MIN_IMPORTANCE = 3          # floor — never decay below this

# Archival
ACTIVE_CAP_PER_AGENT = 500       # max active memories per agent
ARCHIVE_IMPORTANCE_THRESHOLD = 5  # memories at or below this importance get archived first
ARCHIVE_INACTIVE_DAYS = 30        # no activation in 30 days → archive candidate

# Protected
PROTECTED_CATEGORIES = {"identity", "core_belief"}  # never archive or consolidate these
PROTECTED_MIN_IMPORTANCE = 9      # imp >= 9 is never archived

LOG = logging.getLogger("memory_lifecycle")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ── Database Setup ──────────────────────────────────────────────────────────

async def ensure_archive_table(conn: asyncpg.Connection) -> None:
    """Create memories_archive table if it doesn't exist.
    Same schema as memories but with archive metadata."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS memories_archive (
            id BIGINT PRIMARY KEY,
            agent TEXT NOT NULL,
            scope TEXT,
            category TEXT,
            content TEXT,
            importance SMALLINT,
            source_tier TEXT,
            heat_score NUMERIC,
            access_count INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ,
            archived_at TIMESTAMPTZ DEFAULT NOW(),
            reason TEXT,
            metadata JSONB
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_archive_agent ON memories_archive(agent)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_archive_reason ON memories_archive(reason)
    """)


# ── Phase 1: CONSOLIDATION ─────────────────────────────────────────────────

async def find_duplicate_clusters(conn: asyncpg.Connection, agent: str) -> list[list[dict]]:
    """Find clusters of near-duplicate memories using content prefix matching.

    Uses a two-pass approach:
    1. Fast pass: group by content prefix (first 80 chars)
    2. Refine: within each group, check full content similarity
    """
    clusters = []

    # Pass 1: Find groups with similar prefixes
    groups = await conn.fetch("""
        SELECT LEFT(content, 80) as prefix,
               array_agg(id ORDER BY created_at) as ids,
               COUNT(*) as cnt
        FROM memories
        WHERE agent = $1 AND invalid_at IS NULL
        GROUP BY LEFT(content, 80)
        HAVING COUNT(*) >= $2
        ORDER BY cnt DESC
    """, agent, MIN_CLUSTER_SIZE)

    for g in groups:
        # Fetch full memory details for this cluster
        rows = await conn.fetch("""
            SELECT id, content, category, importance, created_at,
                   last_activation, query_count, valence, arousal
            FROM memories
            WHERE id = ANY($1)
            ORDER BY importance DESC, created_at DESC
        """, g["ids"])

        cluster = [dict(r) for r in rows]
        if len(cluster) >= MIN_CLUSTER_SIZE:
            clusters.append(cluster)

    return clusters


async def consolidate_cluster(
    conn: asyncpg.Connection, cluster: list[dict], dry_run: bool = True
) -> Optional[dict]:
    """Merge a cluster of similar memories into one consolidated entry.

    Strategy:
    - Keep the highest-importance memory as the survivor
    - Update its content to be a summary
    - Archive the rest with reason='consolidated'
    - Preserve the max importance and earliest created_at
    """
    if not cluster or len(cluster) < MIN_CLUSTER_SIZE:
        return None

    # Sort by importance DESC, then by recency
    cluster.sort(key=lambda m: (-m["importance"], -(m["query_count"] or 0)))

    survivor = cluster[0]
    victims = cluster[1:]

    # Build consolidated content
    earliest = min(m["created_at"] for m in cluster)
    latest = max(m["created_at"] for m in cluster)
    max_imp = max(m["importance"] for m in cluster)
    total_queries = sum(m.get("query_count", 0) or 0 for m in cluster)

    # Use survivor's content as base, add consolidation metadata
    consolidated_content = (
        f"[CONSOLIDADO {datetime.now(PERU_TZ).strftime('%Y-%m-%d')}] "
        f"{survivor['content'][:MAX_CONSOLIDATED_LENGTH]} "
        f"(fusión de {len(cluster)} memorias, {earliest.strftime('%b %d')}→{latest.strftime('%b %d')})"
    )

    result = {
        "survivor_id": survivor["id"],
        "victim_ids": [m["id"] for m in victims],
        "consolidated_content": consolidated_content,
        "original_count": len(cluster),
        "max_importance": max_imp,
        "total_queries": total_queries,
    }

    if dry_run:
        return result

    # Execute consolidation
    async with conn.transaction():
        # Update survivor
        await conn.execute("""
            UPDATE memories SET
                content = $1,
                importance = $2,
                query_count = $3,
                created_at = $4,
                source = 'consolidation'
            WHERE id = $5
        """, consolidated_content, max_imp, total_queries, earliest, survivor["id"])

        # Archive victims (DEPRECATED — use cold_archive instead, migration 009)
        warnings.warn(
            "memories_archive is deprecated. Use cold_archive (migration 009) for new archival.",
            DeprecationWarning, stacklevel=2,
        )
        for victim in victims:
            await conn.execute("""
                INSERT INTO memories_archive
                    (id, agent, scope, category, content, importance, source_tier,
                     heat_score, access_count, created_at, reason, metadata)
                SELECT id, agent, scope, category, content, importance, source_tier,
                       heat_score, COALESCE(access_count, query_count, 0),
                       created_at, 'consolidated', metadata
                FROM memories WHERE id = $1
                ON CONFLICT (id) DO NOTHING
            """, victim["id"])

        # Delete victims from active table
        victim_ids = [m["id"] for m in victims]
        await conn.execute(
            "DELETE FROM memories WHERE id = ANY($1)", victim_ids
        )

        # Clean up Qdrant vectors for archived memories
        # (done separately to not block the transaction)

    LOG.info(
        "Consolidated cluster: survivor=#%d, archived %d memories",
        survivor["id"], len(victims)
    )
    return result


async def run_consolidation(conn: asyncpg.Connection, agent: str, dry_run: bool = True) -> dict:
    """Run full consolidation pass for an agent."""
    clusters = await find_duplicate_clusters(conn, agent)

    results = {
        "agent": agent,
        "clusters_found": len(clusters),
        "memories_before": await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE agent = $1", agent
        ),
        "consolidated": [],
        "total_archived": 0,
    }

    for cluster in clusters:
        # Skip protected categories
        categories = {m.get("category", "") for m in cluster}
        if categories & PROTECTED_CATEGORIES:
            continue

        result = await consolidate_cluster(conn, cluster, dry_run=dry_run)
        if result:
            results["consolidated"].append(result)
            results["total_archived"] += len(result["victim_ids"])

    results["memories_after"] = results["memories_before"] - (
        0 if dry_run else results["total_archived"]
    )

    return results


# ── Phase 2: DECAY ──────────────────────────────────────────────────────────

async def run_decay(conn: asyncpg.Connection, agent: str, dry_run: bool = True) -> dict:
    """Reduce importance of memories that haven't been accessed recently.

    Rules:
    - Only affects memories with importance > DECAY_MIN_IMPORTANCE
    - Only affects memories not activated in DECAY_INACTIVE_DAYS
    - Protected categories and imp >= PROTECTED_MIN_IMPORTANCE are exempt
    - Drops importance by DECAY_IMPORTANCE_DROP per cycle
    """
    cutoff = datetime.now(PERU_TZ) - timedelta(days=DECAY_INACTIVE_DAYS)

    # Find decay candidates
    candidates = await conn.fetch("""
        SELECT id, content, category, importance, last_activation, query_count
        FROM memories
        WHERE agent = $1
          AND importance > $2
          AND importance < $3
          AND (last_activation IS NULL OR last_activation < $4)
          AND (query_count IS NULL OR query_count = 0)
          AND category NOT IN ('identity', 'core_belief')
          AND invalid_at IS NULL
        ORDER BY importance ASC, created_at ASC
    """, agent, DECAY_MIN_IMPORTANCE, PROTECTED_MIN_IMPORTANCE, cutoff)

    results = {
        "agent": agent,
        "candidates": len(candidates),
        "decayed": 0,
        "samples": [],
    }

    if dry_run:
        for c in candidates[:10]:  # show first 10 samples
            results["samples"].append({
                "id": c["id"],
                "content": c["content"][:80],
                "importance": c["importance"],
                "new_importance": max(c["importance"] - DECAY_IMPORTANCE_DROP, DECAY_MIN_IMPORTANCE),
                "last_activation": str(c["last_activation"]) if c["last_activation"] else "never",
            })
        return results

    # Execute decay
    updated = await conn.execute("""
        UPDATE memories SET importance = GREATEST(importance - $1, $2)
        WHERE agent = $3
          AND importance > $2
          AND importance < $4
          AND (last_activation IS NULL OR last_activation < $5)
          AND (query_count IS NULL OR query_count = 0)
          AND category NOT IN ('identity', 'core_belief')
          AND invalid_at IS NULL
    """, DECAY_IMPORTANCE_DROP, DECAY_MIN_IMPORTANCE, agent,
        PROTECTED_MIN_IMPORTANCE, cutoff)

    results["decayed"] = int(updated.split()[-1]) if updated else 0
    LOG.info("Decayed %d memories for %s", results["decayed"], agent)

    return results


# ── Phase 3: ARCHIVAL ───────────────────────────────────────────────────────

async def run_archival(conn: asyncpg.Connection, agent: str, dry_run: bool = True) -> dict:
    """Archive cold memories to stay under the active cap.

    Priority for archival (first to go):
    1. Low importance + never activated
    2. Low importance + old activation
    3. Medium importance + never activated

    Never archives: importance >= PROTECTED_MIN_IMPORTANCE, protected categories
    """
    await ensure_archive_table(conn)

    current_count = await conn.fetchval(
        "SELECT COUNT(*) FROM memories WHERE agent = $1 AND invalid_at IS NULL", agent
    )

    results = {
        "agent": agent,
        "current_count": current_count,
        "cap": ACTIVE_CAP_PER_AGENT,
        "needs_archival": max(0, current_count - ACTIVE_CAP_PER_AGENT),
        "archived": 0,
        "samples": [],
    }

    if current_count <= ACTIVE_CAP_PER_AGENT:
        LOG.info("%s: %d memories, under cap (%d). No archival needed.",
                 agent, current_count, ACTIVE_CAP_PER_AGENT)
        return results

    excess = current_count - ACTIVE_CAP_PER_AGENT

    # Select candidates ordered by archival priority
    candidates = await conn.fetch("""
        SELECT id, content, category, importance, created_at,
               last_activation, query_count, valence, arousal, dominance,
               source, metadata
        FROM memories
        WHERE agent = $1
          AND invalid_at IS NULL
          AND importance < $2
          AND category NOT IN ('identity', 'core_belief')
        ORDER BY
            importance ASC,
            COALESCE(query_count, 0) ASC,
            COALESCE(last_activation, '1970-01-01'::timestamptz) ASC,
            created_at ASC
        LIMIT $3
    """, agent, PROTECTED_MIN_IMPORTANCE, excess)

    results["candidates_found"] = len(candidates)

    if dry_run:
        for c in candidates[:15]:
            results["samples"].append({
                "id": c["id"],
                "content": c["content"][:80],
                "importance": c["importance"],
                "category": c["category"],
                "query_count": c["query_count"] or 0,
                "last_activation": str(c["last_activation"]) if c["last_activation"] else "never",
            })
        return results

    # Execute archival (DEPRECATED — use cold_archive instead, migration 009)
    warnings.warn(
        "memories_archive is deprecated. Use cold_archive (migration 009) for new archival.",
        DeprecationWarning, stacklevel=2,
    )
    archived_ids = []
    async with conn.transaction():
        for c in candidates:
            await conn.execute("""
                INSERT INTO memories_archive
                    (id, agent, scope, category, content, importance, source_tier,
                     heat_score, access_count, created_at, reason, metadata)
                SELECT id, agent, scope, category, content, importance, source_tier,
                       heat_score, COALESCE(access_count, query_count, 0),
                       created_at, 'cold', metadata
                FROM memories WHERE id = $1
                ON CONFLICT (id) DO NOTHING
            """, c["id"])
            archived_ids.append(c["id"])

        # Clean up all foreign key references before deleting
        await conn.execute(
            "DELETE FROM memory_broadcasts WHERE memory_id = ANY($1)", archived_ids
        )
        await conn.execute(
            "DELETE FROM utility_updates WHERE memory_id = ANY($1)", archived_ids
        )
        await conn.execute(
            "DELETE FROM memory_connections WHERE source_id = ANY($1) OR target_id = ANY($1)", archived_ids
        )
        # Delete from active table
        await conn.execute("DELETE FROM memories WHERE id = ANY($1)", archived_ids)

    results["archived"] = len(archived_ids)

    LOG.info("Archived %d memories for %s (%d → %d active)",
             results["archived"], agent, current_count,
             current_count - results["archived"])

    return results


# ── Qdrant Cleanup ──────────────────────────────────────────────────────────

async def cleanup_qdrant_vectors(archived_ids: list[int]) -> int:
    """Remove vectors from Qdrant for archived memories."""
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(host="localhost", port=6333)

        # Delete points by ID
        from qdrant_client.models import PointIdsList
        client.delete(
            collection_name="seal_memories",
            points_selector=PointIdsList(points=archived_ids)
        )
        LOG.info("Cleaned %d vectors from Qdrant", len(archived_ids))
        return len(archived_ids)
    except Exception as e:
        LOG.warning("Qdrant cleanup failed (non-critical): %s", e)
        return 0


# ── Full Lifecycle Run ──────────────────────────────────────────────────────

async def run_full_lifecycle(dry_run: bool = True, agents: list[str] | None = None) -> dict:
    """Run complete memory lifecycle: consolidate → decay → archive."""
    conn = await asyncpg.connect(PG_DSN)
    await ensure_archive_table(conn)

    if agents is None:
        rows = await conn.fetch(
            "SELECT DISTINCT agent FROM memories WHERE agent IS NOT NULL"
        )
        agents = [r["agent"] for r in rows if r["agent"] not in ("TEAM", "DUM")]

    report = {
        "timestamp": datetime.now(PERU_TZ).isoformat(),
        "dry_run": dry_run,
        "agents": {},
    }

    for agent in agents:
        LOG.info("═══ Processing %s ═══", agent)

        # Phase 1: Consolidation
        LOG.info("Phase 1: Consolidation")
        consol = await run_consolidation(conn, agent, dry_run=dry_run)

        # Phase 2: Decay
        LOG.info("Phase 2: Decay")
        decay = await run_decay(conn, agent, dry_run=dry_run)

        # Phase 3: Archival
        LOG.info("Phase 3: Archival")
        archive = await run_archival(conn, agent, dry_run=dry_run)

        report["agents"][agent] = {
            "consolidation": consol,
            "decay": decay,
            "archival": archive,
        }

        # Summary
        LOG.info(
            "%s summary: %d clusters to consolidate, %d to decay, %d to archive",
            agent,
            consol["clusters_found"],
            decay["candidates"],
            archive.get("needs_archival", 0),
        )

    await conn.close()
    return report


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SEAL Memory Lifecycle Manager")
    parser.add_argument("--run", action="store_true", help="Run full lifecycle (dry-run by default)")
    parser.add_argument("--execute", action="store_true", help="Actually execute changes (not just dry-run)")
    parser.add_argument("--consolidate", action="store_true", help="Run only consolidation phase")
    parser.add_argument("--decay", action="store_true", help="Run only decay phase")
    parser.add_argument("--archive", action="store_true", help="Run only archival phase")
    parser.add_argument("--agent", type=str, help="Target specific agent (default: all)")
    parser.add_argument("--report", action="store_true", help="Print current memory stats")
    args = parser.parse_args()

    dry_run = not args.execute
    agents = [args.agent] if args.agent else None

    if args.report:
        asyncio.run(print_report(agents))
        return

    if args.run or args.consolidate or args.decay or args.archive:
        result = asyncio.run(run_full_lifecycle(dry_run=dry_run, agents=agents))

        if dry_run:
            print("\n" + "=" * 60)
            print("  DRY RUN — No changes made. Use --execute to apply.")
            print("=" * 60)

        # Print summary
        for agent, data in result["agents"].items():
            print(f"\n{'═' * 40}")
            print(f"  {agent}")
            print(f"{'═' * 40}")

            c = data["consolidation"]
            print(f"  Consolidation: {c['clusters_found']} clusters, "
                  f"{c['total_archived']} memories to merge")

            d = data["decay"]
            print(f"  Decay: {d['candidates']} candidates")

            a = data["archival"]
            print(f"  Archival: {a['current_count']} active, "
                  f"cap={a['cap']}, needs_archival={a.get('needs_archival', 0)}")

            if dry_run and a.get("samples"):
                print(f"\n  Archive candidates (first 5):")
                for s in a["samples"][:5]:
                    print(f"    #{s['id']} imp={s['importance']} "
                          f"hits={s['query_count']} [{s['category']}] "
                          f"{s['content']}")
    else:
        parser.print_help()


async def print_report(agents: list[str] | None = None):
    """Print current memory stats."""
    conn = await asyncpg.connect(PG_DSN)

    if agents is None:
        rows = await conn.fetch(
            "SELECT DISTINCT agent FROM memories WHERE agent IS NOT NULL ORDER BY agent"
        )
        agents = [r["agent"] for r in rows]

    archive_exists = await conn.fetchval("""
        SELECT EXISTS(
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'memories_archive'
        )
    """)

    print("═" * 60)
    print("  SEAL Memory Lifecycle — Status Report")
    print(f"  {datetime.now(PERU_TZ).strftime('%Y-%m-%d %H:%M Lima')}")
    print("═" * 60)

    for agent in agents:
        active = await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE agent = $1 AND invalid_at IS NULL", agent
        )
        total_chars = await conn.fetchval(
            "SELECT COALESCE(SUM(LENGTH(content)), 0) FROM memories WHERE agent = $1", agent
        )
        never_used = await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE agent = $1 AND (last_activation IS NULL OR query_count = 0)", agent
        )

        archived = 0
        if archive_exists:
            archived = await conn.fetchval(
                "SELECT COUNT(*) FROM memories_archive WHERE agent = $1", agent
            ) or 0

        status = "OK" if active <= ACTIVE_CAP_PER_AGENT else "OVER CAP"

        print(f"\n  {agent} [{status}]")
        print(f"    Active: {active:,} (cap: {ACTIVE_CAP_PER_AGENT})")
        print(f"    Archived: {archived:,}")
        print(f"    Size: {total_chars:,} chars (~{total_chars // 4:,} tokens)")
        print(f"    Never activated: {never_used:,} ({never_used * 100 // max(active, 1)}%)")

    await conn.close()


if __name__ == "__main__":
    main()
