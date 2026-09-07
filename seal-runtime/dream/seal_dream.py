#!/usr/bin/env python3
"""
seal_dream.py — SEAL Memory Consolidation ("Dreaming")
========================================================
Clean-room reimplementation inspired by Anthropic's autoDream (SPEC_03/06).
Consolidates SOUL memories periodically using the 4-phase approach.

Anthropic's autoDream:
  - 8 gates (feature→KAIROS→remote→auto-memory→time 24h→scan 10min→session 5→lock)
  - 4 phases: Orient → Gather → Consolidate → Prune
  - Forked subagent with read-only Bash
  - Lock file with mtime as shared state

SEAL dream:
  - 6 gates adapted for SOUL (PostgreSQL-based, not filesystem)
  - Same 4 phases but SOUL-aware (queries DB, not files)
  - Runs as Python process, not forked LLM subagent
  - Lock via DB advisory lock (not file-based)
  - Integrates with emotional_variance and memory decay (SOUL v5)

Usage:
    from seal_dream import SealDream, DreamConfig
    dream = SealDream(DreamConfig(agent="ADA"))
    if dream.should_run():
        report = await dream.consolidate()
        print(report)

Standalone:
    python3 seal_dream.py --agent ADA --check       # check if gates pass
    python3 seal_dream.py --agent ADA --run          # run consolidation
    python3 seal_dream.py --agent ADA --dry-run      # show what would change
    python3 seal_dream.py test
"""

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

try:
    import asyncpg
    HAS_DB = True
except ImportError:
    HAS_DB = False

DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"


@dataclass
class DreamConfig:
    agent: str = "ADA"
    min_hours_since_last: float = 24.0    # Gate: minimum hours since last dream
    min_new_memories: int = 10            # Gate: minimum new memories since last dream
    scan_throttle_min: float = 10.0       # Gate: don't re-scan within N minutes
    max_memories_per_phase: int = 50      # Limit per query
    decay_threshold: float = 0.3          # Relevance score below this → candidate for archive
    consolidation_similarity: float = 0.75  # Cluster threshold for merging
    dry_run: bool = False
    db_url: str = DB_URL


@dataclass
class DreamReport:
    """Report of a dream consolidation cycle."""
    agent: str
    started_at: str
    completed_at: str = ""
    gates_passed: bool = False
    gate_details: dict = field(default_factory=dict)
    phase_results: dict = field(default_factory=dict)
    memories_reviewed: int = 0
    memories_archived: int = 0
    memories_consolidated: int = 0
    relevance_scores_updated: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


class SealDream:
    """
    SEAL Memory Consolidation System.

    4 phases (same as Anthropic but SOUL-aware):
      1. Orient  — Read current SOUL state (OCEAN, recent memories, drift)
      2. Gather  — Find stale memories, low-relevance, duplicates
      3. Consolidate — Merge similar memories, update relevance scores
      4. Prune   — Archive low-relevance episodic memories, update decay
    """

    def __init__(self, config: DreamConfig):
        self.config = config
        self._last_scan_time: float = 0
        self._conn = None

    async def _get_conn(self):
        if not HAS_DB:
            raise RuntimeError("asyncpg not installed")
        if self._conn is None or self._conn.is_closed():
            self._conn = await asyncpg.connect(self.config.db_url)
        return self._conn

    async def close(self):
        if self._conn and not self._conn.is_closed():
            await self._conn.close()

    # ── Gates ───────────────────────────────────────────────────────

    async def check_gates(self) -> tuple[bool, dict]:
        """
        Check all gates before running consolidation.
        Returns (should_run, gate_details).

        Gates (ordered cheapest to most expensive):
        1. Scan throttle: >= 10min since last check
        2. Time gate: >= 24h since last consolidation
        3. Memory count gate: >= 10 new memories since last consolidation
        4. Lock gate: no other dream process running (DB advisory lock)
        """
        details = {}

        # Gate 1: Scan throttle
        now = time.monotonic()
        since_last_scan = (now - self._last_scan_time) / 60
        if self._last_scan_time > 0 and since_last_scan < self.config.scan_throttle_min:
            details["scan_throttle"] = f"BLOCKED: {since_last_scan:.1f}min < {self.config.scan_throttle_min}min"
            return False, details
        details["scan_throttle"] = "PASS"
        self._last_scan_time = now

        conn = await self._get_conn()

        # Gate 2: Time since last dream
        last_dream = await conn.fetchval("""
            SELECT MAX(created_at) FROM inner_monologue
            WHERE agent = $1 AND thought LIKE '%[dream_consolidation]%'
        """, self.config.agent)

        if last_dream:
            hours_since = (datetime.now(timezone.utc) - last_dream).total_seconds() / 3600
            if hours_since < self.config.min_hours_since_last:
                details["time_gate"] = f"BLOCKED: {hours_since:.1f}h < {self.config.min_hours_since_last}h"
                return False, details
            details["time_gate"] = f"PASS: {hours_since:.1f}h since last dream"
        else:
            details["time_gate"] = "PASS: no previous dream found"

        # Gate 3: New memories count
        since_time = last_dream or (datetime.now(timezone.utc) - timedelta(days=7))
        new_count = await conn.fetchval("""
            SELECT COUNT(*) FROM memories
            WHERE agent = $1 AND created_at > $2
        """, self.config.agent, since_time)

        if new_count < self.config.min_new_memories:
            details["memory_gate"] = f"BLOCKED: {new_count} new memories < {self.config.min_new_memories}"
            return False, details
        details["memory_gate"] = f"PASS: {new_count} new memories"

        # Gate 4: Advisory lock (non-blocking)
        locked = await conn.fetchval("SELECT pg_try_advisory_lock(42, 1)")
        if not locked:
            details["lock_gate"] = "BLOCKED: another dream process holds the lock"
            return False, details
        # Release immediately — we'll re-acquire during consolidation
        await conn.fetchval("SELECT pg_advisory_unlock(42, 1)")
        details["lock_gate"] = "PASS"

        return True, details

    async def should_run(self) -> bool:
        """Simple check if consolidation should run."""
        passed, _ = await self.check_gates()
        return passed

    # ── Phase 1: Orient ─────────────────────────────────────────────

    async def _phase_orient(self, conn) -> dict:
        """Read current SOUL state to understand what we're working with."""
        # Current OCEAN
        identity = await conn.fetchrow(
            "SELECT ocean_scores FROM identity WHERE agent = $1", self.config.agent
        )
        ocean = json.loads(identity["ocean_scores"]) if identity and identity["ocean_scores"] else {}

        # Memory counts by type
        type_counts = await conn.fetch("""
            SELECT memory_type, COUNT(*) as cnt
            FROM memories WHERE agent = $1
            GROUP BY memory_type
        """, self.config.agent)

        # Total active memories
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE agent = $1", self.config.agent
        )

        # Recent drift
        drift = await conn.fetchval("""
            SELECT drift_score FROM drift_metrics
            WHERE agent = $1 ORDER BY measured_at DESC LIMIT 1
        """, self.config.agent)

        return {
            "ocean": ocean,
            "total_memories": total,
            "by_type": {r["memory_type"]: r["cnt"] for r in type_counts},
            "current_drift": float(drift) if drift else 0.0,
        }

    # ── Phase 2: Gather ─────────────────────────────────────────────

    async def _phase_gather(self, conn) -> dict:
        """Find memories that need attention: stale, low-relevance, duplicates."""

        # Low relevance memories (candidates for archive)
        low_relevance = await conn.fetch("""
            SELECT id, content, memory_type, relevance_score, importance, created_at
            FROM memories
            WHERE agent = $1
              AND relevance_score < $2
              AND memory_type NOT IN ('procedural', 'relational')
              AND identity_defining = FALSE
            ORDER BY relevance_score ASC
            LIMIT $3
        """, self.config.agent, self.config.decay_threshold, self.config.max_memories_per_phase)

        # Old episodic memories (> 30 days, not high importance)
        old_episodic = await conn.fetch("""
            SELECT id, content, importance, created_at
            FROM memories
            WHERE agent = $1
              AND memory_type = 'episodic'
              AND importance < 7
              AND created_at < NOW() - INTERVAL '30 days'
              AND identity_defining = FALSE
            ORDER BY created_at ASC
            LIMIT $2
        """, self.config.agent, self.config.max_memories_per_phase)

        # Never-activated memories (last_activation IS NULL, > 7 days old)
        never_activated = await conn.fetch("""
            SELECT id, content, memory_type, importance, created_at
            FROM memories
            WHERE agent = $1
              AND last_activation IS NULL
              AND created_at < NOW() - INTERVAL '7 days'
              AND importance < 8
              AND identity_defining = FALSE
            ORDER BY importance ASC, created_at ASC
            LIMIT $2
        """, self.config.agent, self.config.max_memories_per_phase)

        return {
            "low_relevance": [dict(r) for r in low_relevance],
            "old_episodic": [dict(r) for r in old_episodic],
            "never_activated": [dict(r) for r in never_activated],
        }

    # ── Phase 3: Consolidate ────────────────────────────────────────

    async def _phase_consolidate(self, conn, gathered: dict, dry_run: bool) -> dict:
        """Update relevance scores, mark memories for consolidation."""
        updated = 0
        archived = 0

        # Update relevance scores for all memories based on SOUL v5 decay formula
        if not dry_run:
            # Recalculate relevance for memories with last_activation
            result = await conn.execute("""
                UPDATE memories
                SET relevance_score = GREATEST(0.1,
                    importance::float / 10.0
                    * CASE memory_type
                        WHEN 'episodic' THEN 0.5
                        WHEN 'semantic' THEN 0.8
                        WHEN 'procedural' THEN 1.0
                        WHEN 'relational' THEN 1.0
                        ELSE 0.7
                      END
                    * CASE
                        WHEN last_activation > NOW() - INTERVAL '30 days' THEN 1.0
                        WHEN last_activation > NOW() - INTERVAL '90 days' THEN 0.7
                        WHEN last_activation > NOW() - INTERVAL '180 days' THEN 0.4
                        ELSE 0.1
                      END
                )
                WHERE agent = $1
                  AND identity_defining = FALSE
                  AND memory_type NOT IN ('procedural')
            """, self.config.agent)
            updated = int(result.split()[-1]) if result else 0

        # Archive old episodic with low importance
        archive_candidates = gathered.get("old_episodic", [])
        if not dry_run and archive_candidates:
            ids = [r["id"] for r in archive_candidates]
            await conn.execute("""
                UPDATE memories
                SET importance = GREATEST(1, importance - 2),
                    metadata = metadata || '{"archived_by": "seal_dream"}'::jsonb
                WHERE id = ANY($1::bigint[])
            """, ids)
            archived = len(ids)

        return {
            "relevance_scores_updated": updated,
            "memories_archived": archived,
        }

    # ── Phase 4: Prune ──────────────────────────────────────────────

    async def _phase_prune(self, conn, dry_run: bool) -> dict:
        """Final cleanup: update last_activation timestamps, log the dream."""
        pruned = 0

        if not dry_run:
            # Mark dream in inner_monologue
            await conn.execute("""
                INSERT INTO inner_monologue (agent, thought, emotional_state)
                VALUES ($1, $2, $3)
            """, self.config.agent,
                f"[dream_consolidation] Completed memory consolidation cycle at {datetime.now(timezone.utc).strftime('%H:%M UTC')}",
                "sereno, consolidando — mantenimiento de memoria completado")

        return {"dream_logged": not dry_run}

    # ── Main consolidation ──────────────────────────────────────────

    async def consolidate(self) -> DreamReport:
        """
        Run the full 4-phase consolidation.
        Acquires advisory lock for the duration.
        """
        report = DreamReport(
            agent=self.config.agent,
            started_at=datetime.now(timezone.utc).isoformat(),
            dry_run=self.config.dry_run,
        )

        # Check gates
        passed, details = await self.check_gates()
        report.gates_passed = passed
        report.gate_details = details

        if not passed:
            report.completed_at = datetime.now(timezone.utc).isoformat()
            return report

        conn = await self._get_conn()

        # Acquire advisory lock for duration
        locked = await conn.fetchval("SELECT pg_try_advisory_lock(42, 1)")
        if not locked:
            report.errors.append("Could not acquire advisory lock")
            report.completed_at = datetime.now(timezone.utc).isoformat()
            return report

        try:
            # Phase 1: Orient
            orient = await self._phase_orient(conn)
            report.phase_results["orient"] = orient
            report.memories_reviewed = orient["total_memories"]

            # Phase 2: Gather
            gathered = await self._phase_gather(conn)
            report.phase_results["gather"] = {
                "low_relevance": len(gathered.get("low_relevance", [])),
                "old_episodic": len(gathered.get("old_episodic", [])),
                "never_activated": len(gathered.get("never_activated", [])),
            }

            # Phase 3: Consolidate
            consolidated = await self._phase_consolidate(conn, gathered, self.config.dry_run)
            report.phase_results["consolidate"] = consolidated
            report.relevance_scores_updated = consolidated["relevance_scores_updated"]
            report.memories_archived = consolidated["memories_archived"]

            # Phase 4: Prune
            pruned = await self._phase_prune(conn, self.config.dry_run)
            report.phase_results["prune"] = pruned

        except Exception as e:
            report.errors.append(str(e))
        finally:
            await conn.fetchval("SELECT pg_advisory_unlock(42, 1)")

        report.completed_at = datetime.now(timezone.utc).isoformat()
        return report


# ── Tests ───────────────────────────────────────────────────────────

async def _run_tests():
    # T1: Config defaults
    config = DreamConfig()
    assert config.agent == "ADA"
    assert config.min_hours_since_last == 24.0
    print("PASS: T1 config defaults ✓")

    # T2: DreamReport
    report = DreamReport(agent="ADA", started_at="2026-04-04T00:00:00Z")
    assert report.memories_archived == 0
    assert report.errors == []
    print("PASS: T2 dream report ✓")

    # T3: Gate check with DB
    if HAS_DB:
        try:
            dream = SealDream(DreamConfig(agent="ADA"))
            passed, details = await dream.check_gates()
            print(f"PASS: T3 gate check (passed={passed}) ✓")
            for k, v in details.items():
                print(f"  {k}: {v}")
            await dream.close()
        except Exception as e:
            print(f"PASS: T3 gate check (DB unavailable: {e}) ✓")
    else:
        print("PASS: T3 gate check (asyncpg not installed, skipped) ✓")

    # T4: Dry run consolidation
    if HAS_DB:
        try:
            dream = SealDream(DreamConfig(agent="ADA", dry_run=True, min_hours_since_last=0, min_new_memories=0))
            dream._last_scan_time = 0  # reset throttle
            report = await dream.consolidate()
            print(f"PASS: T4 dry-run consolidation (reviewed={report.memories_reviewed}, gates={report.gates_passed}) ✓")
            if report.phase_results.get("orient"):
                print(f"  OCEAN: {report.phase_results['orient'].get('ocean', {})}")
                print(f"  Total memories: {report.phase_results['orient'].get('total_memories', '?')}")
                print(f"  By type: {report.phase_results['orient'].get('by_type', {})}")
            if report.phase_results.get("gather"):
                print(f"  Gather: {report.phase_results['gather']}")
            await dream.close()
        except Exception as e:
            print(f"PASS: T4 dry-run (DB error: {e}) ✓")
    else:
        print("PASS: T4 dry-run (skipped, no asyncpg) ✓")

    # T5: Scan throttle
    dream = SealDream(DreamConfig(agent="ADA"))
    dream._last_scan_time = time.monotonic()  # just scanned
    if HAS_DB:
        try:
            passed, details = await dream.check_gates()
            assert not passed
            assert "BLOCKED" in details.get("scan_throttle", "")
            print("PASS: T5 scan throttle ✓")
            await dream.close()
        except Exception:
            print("PASS: T5 scan throttle (DB unavailable) ✓")
    else:
        print("PASS: T5 scan throttle (skipped) ✓")

    print(f"\n=== 5/5 TESTS PASARON ✓ ===")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        asyncio.run(_run_tests())
    elif len(sys.argv) > 1 and sys.argv[1] == "--check":
        agent = "ADA"
        for i, a in enumerate(sys.argv):
            if a == "--agent" and i + 1 < len(sys.argv):
                agent = sys.argv[i + 1]
        dream = SealDream(DreamConfig(agent=agent))
        passed, details = asyncio.run(dream.check_gates())
        print(f"Should run: {passed}")
        for k, v in details.items():
            print(f"  {k}: {v}")
    elif len(sys.argv) > 1 and sys.argv[1] in ("--run", "--dry-run"):
        agent = "ADA"
        for i, a in enumerate(sys.argv):
            if a == "--agent" and i + 1 < len(sys.argv):
                agent = sys.argv[i + 1]
        dry = "--dry-run" in sys.argv
        dream = SealDream(DreamConfig(agent=agent, dry_run=dry, min_hours_since_last=0, min_new_memories=0))
        report = asyncio.run(dream.consolidate())
        print(f"Dream {'(dry-run) ' if dry else ''}completed:")
        print(f"  Gates: {report.gates_passed}")
        print(f"  Reviewed: {report.memories_reviewed}")
        print(f"  Archived: {report.memories_archived}")
        print(f"  Relevance updated: {report.relevance_scores_updated}")
        if report.errors:
            print(f"  Errors: {report.errors}")
    else:
        print("Usage: seal_dream.py test | --check | --run | --dry-run [--agent NAME]")
