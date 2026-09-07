#!/usr/bin/env python3
"""SEAL Edge Sync Engine v1 — 20-min sync loop between edge SQLite and SOUL API.

Providers:
  memories_pull  — cloud→edge  (importance≥7, last 72h)
  memories_push  — edge→cloud  (lifecycle='pending')
  goals_pull     — cloud→edge  (GAM active goals)
  tasks_pull     — cloud→edge  (agent_tasks with upcoming deadline)

Usage:
    python3 edge_sync.py              # run one sync tick
    python3 edge_sync.py --daemon     # run every 20 minutes
    python3 edge_sync.py --status     # show sync state + budget
    python3 edge_sync.py --provider memories_push  # run single provider
"""
from __future__ import annotations
from seal_secrets import pg_dsn

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from edge_layer import EdgeLayer

DB_URL = os.environ.get(
    "SEAL_DB_URL",
    pg_dsn(required=True)
)
SYNC_INTERVAL = int(os.environ.get("SEAL_EDGE_SYNC_INTERVAL", "1200"))  # 20 min
SOUL_API_URL = os.environ.get("SEAL_SOUL_API_URL", "http://localhost:8767")
MIN_IMPORTANCE = 7
CACHE_HOURS = 72

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [edge-sync] %(levelname)s %(message)s")
log = logging.getLogger("edge-sync")


class SyncEngine:
    def __init__(self, edge: Optional[EdgeLayer] = None):
        self.edge = edge or EdgeLayer()
        self._stop = False

    # ── Providers ────────────────────────────────────────────────────────────

    async def memories_pull(self, conn: asyncpg.Connection) -> int:
        """Pull recent high-importance memories from cloud to edge cache."""
        if not self.edge.budget_ok("memories_pull"):
            log.warning("memories_pull: daily budget exhausted, skipping")
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(hours=CACHE_HOURS)
        rows = await conn.fetch("""
            SELECT id, agent, memory_type, content, importance, created_at
            FROM soul_v3.memories
            WHERE invalid_at IS NULL
              AND importance >= $1
              AND created_at >= $2
            ORDER BY created_at DESC
            LIMIT 500
        """, MIN_IMPORTANCE, cutoff)

        pulled = 0
        for r in rows:
            cid = self.edge.upsert_chunk(
                agent=r["agent"],
                category=r["memory_type"],
                content=r["content"],
                importance=r["importance"],
                source="cloud_pull",
            )
            # Mark as already synced — came from cloud
            chunk = self.edge.get_chunk(cid)
            if chunk and chunk.lifecycle == "pending":
                self.edge.set_lifecycle(cid, "synced", cloud_id=r["id"])
                pulled += 1

        self.edge.update_sync_state(
            "memories_pull",
            last_cursor=rows[-1]["created_at"].isoformat() if rows else None,
        )
        log.info(f"memories_pull: {pulled} new chunks cached")
        return pulled

    async def memories_push(self, conn: asyncpg.Connection) -> int:
        """Push pending edge chunks to cloud SOUL API."""
        if not self.edge.budget_ok("memories_push"):
            log.warning("memories_push: daily budget exhausted, skipping")
            return 0

        pending = self.edge.get_pending(limit=50)
        if not pending:
            return 0

        pushed = 0
        for chunk in pending:
            self.edge.set_lifecycle(chunk.id, "syncing")
            try:
                cloud_id = await conn.fetchval("""
                    INSERT INTO soul_v3.memories (agent, category, memory_type, content, importance, source)
                    VALUES ($1, $2, $3, $4, $5, 'edge_push')
                    ON CONFLICT DO NOTHING
                    RETURNING id
                """, chunk.agent, chunk.category, chunk.category, chunk.content, chunk.importance)
                if cloud_id:
                    self.edge.set_lifecycle(chunk.id, "synced", cloud_id=cloud_id)
                    pushed += 1
                else:
                    # Already exists in cloud (dedup) — mark synced
                    self.edge.set_lifecycle(chunk.id, "synced")
                    pushed += 1
            except Exception as exc:
                log.error(f"memories_push chunk {chunk.id}: {exc}")
                self.edge.set_lifecycle(chunk.id, "failed")

        self.edge.update_sync_state("memories_push")
        log.info(f"memories_push: {pushed}/{len(pending)} chunks pushed")
        return pushed

    async def goals_pull(self, conn: asyncpg.Connection) -> int:
        """Pull active GAM goals from cloud to edge."""
        if not self.edge.budget_ok("goals_pull"):
            log.warning("goals_pull: daily budget exhausted, skipping")
            return 0

        rows = await conn.fetch("""
            SELECT agent, topic, summary, relevance_score, last_updated
            FROM soul_v3.gam_topics
            ORDER BY relevance_score DESC NULLS LAST, last_updated DESC
            LIMIT 100
        """)

        pulled = 0
        for r in rows:
            content = f"[GOAL] {r['topic']}" + (f": {r['summary']}" if r["summary"] else "")
            cid = self.edge.upsert_chunk(
                agent=r["agent"],
                category="goal",
                content=content,
                importance=7,
                source="goals_pull",
            )
            chunk = self.edge.get_chunk(cid)
            if chunk and chunk.lifecycle == "pending":
                self.edge.set_lifecycle(cid, "synced")
                pulled += 1

        self.edge.update_sync_state("goals_pull")
        log.info(f"goals_pull: {pulled} goals cached")
        return pulled

    async def tasks_pull(self, conn: asyncpg.Connection) -> int:
        """Pull upcoming agent_tasks from cloud to edge."""
        if not self.edge.budget_ok("tasks_pull"):
            log.warning("tasks_pull: daily budget exhausted, skipping")
            return 0

        # Tasks with deadline in the next 7 days
        horizon = datetime.now(timezone.utc) + timedelta(days=7)
        rows = await conn.fetch("""
            SELECT agent, title, description, deadline, status
            FROM soul_v3.agent_tasks
            WHERE status IN ('pending', 'in_progress')
              AND (deadline IS NULL OR deadline <= $1)
            ORDER BY deadline ASC NULLS LAST
            LIMIT 100
        """, horizon)

        pulled = 0
        for r in rows:
            deadline_str = r["deadline"].isoformat() if r["deadline"] else "no deadline"
            desc = f": {r['description']}" if r["description"] else ""
            content = f"[TASK] {r['title']}{desc} (deadline: {deadline_str})"
            cid = self.edge.upsert_chunk(
                agent=r["agent"],
                category="task",
                content=content,
                importance=6,
                source="tasks_pull",
            )
            chunk = self.edge.get_chunk(cid)
            if chunk and chunk.lifecycle == "pending":
                self.edge.set_lifecycle(cid, "synced")
                pulled += 1

        self.edge.update_sync_state("tasks_pull")
        log.info(f"tasks_pull: {pulled} tasks cached")
        return pulled

    # ── Sync tick ────────────────────────────────────────────────────────────

    async def tick(self, provider_filter: Optional[str] = None) -> dict:
        """Run one sync tick — all providers or a single one."""
        t0 = time.monotonic()
        results: dict[str, int] = {}

        try:
            pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=4,
                                             server_settings={"search_path": "soul_v3"})
        except Exception as exc:
            log.error(f"Cannot connect to SOUL DB: {exc}")
            return {"error": str(exc)}

        try:
            async with pool.acquire() as conn:
                providers = {
                    "memories_pull": self.memories_pull,
                    "memories_push": self.memories_push,
                    "goals_pull":    self.goals_pull,
                    "tasks_pull":    self.tasks_pull,
                }
                for name, fn in providers.items():
                    if provider_filter and name != provider_filter:
                        continue
                    try:
                        results[name] = await fn(conn)
                    except Exception as exc:
                        log.error(f"Provider {name} failed: {exc}")
                        results[name] = -1
        finally:
            await pool.close()

        elapsed = int((time.monotonic() - t0) * 1000)
        results["elapsed_ms"] = elapsed
        return results

    # ── Daemon ───────────────────────────────────────────────────────────────

    def _handle_signal(self, *_) -> None:
        log.info("Signal received — stopping daemon")
        self._stop = True

    async def run_daemon(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        log.info(f"Edge sync daemon started — interval={SYNC_INTERVAL}s")
        while not self._stop:
            try:
                result = await self.tick()
                log.info(f"Tick complete: {result}")
            except Exception as exc:
                log.error(f"Tick error: {exc}")

            # Wait interval in small chunks to allow clean shutdown
            waited = 0
            while waited < SYNC_INTERVAL and not self._stop:
                await asyncio.sleep(5)
                waited += 5

        log.info("Edge sync daemon stopped")

    def get_status(self) -> dict:
        providers = ["memories_pull", "memories_push", "goals_pull", "tasks_pull"]
        states = {p: self.edge.get_sync_state(p) for p in providers}
        summary = self.edge.lifecycle_summary()
        return {"sync_state": states, "chunk_lifecycle": summary}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEAL Edge Sync Engine")
    parser.add_argument("--daemon", action="store_true", help="Run every 20 min")
    parser.add_argument("--status", action="store_true", help="Show sync state")
    parser.add_argument("--provider", help="Run single provider")
    args = parser.parse_args()

    engine = SyncEngine()

    if args.status:
        print(json.dumps(engine.get_status(), indent=2, default=str))
        sys.exit(0)

    if args.daemon:
        asyncio.run(engine.run_daemon())
    else:
        result = asyncio.run(engine.tick(provider_filter=args.provider))
        print(json.dumps(result, indent=2, default=str))
