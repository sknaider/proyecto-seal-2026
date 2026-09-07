#!/usr/bin/env python3
"""SEAL Edge Layer v1 — local SQLite cache for the desktop agent.

Manages chunks (pending→syncing→synced/failed/dropped) in ~/.seal/edge.db.
Content-addressed IDs: SHA256(agent:content)[:32] — idempotent on re-ingest.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SEAL_DIR = Path.home() / ".seal"
DB_PATH = SEAL_DIR / "edge.db"

LIFECYCLE_STATES = ("pending", "syncing", "synced", "failed", "dropped")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    agent       TEXT NOT NULL,
    category    TEXT NOT NULL,
    content     TEXT NOT NULL,
    importance  INTEGER DEFAULT 5,
    created_at  TEXT NOT NULL,
    lifecycle   TEXT NOT NULL DEFAULT 'pending'
                CHECK (lifecycle IN ('pending', 'syncing', 'synced', 'failed', 'dropped')),
    synced_at   TEXT,
    cloud_id    INTEGER,
    source      TEXT DEFAULT 'edge'
);

CREATE TABLE IF NOT EXISTS sync_state (
    provider     TEXT PRIMARY KEY,
    last_cursor  TEXT,
    last_sync    TEXT,
    daily_calls  INTEGER DEFAULT 0,
    daily_budget INTEGER DEFAULT 500
);

CREATE TABLE IF NOT EXISTS wiki_entries (
    slug        TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    tags        TEXT,
    sources     TEXT
);

CREATE INDEX IF NOT EXISTS idx_chunks_lifecycle ON chunks (lifecycle);
CREATE INDEX IF NOT EXISTS idx_chunks_agent ON chunks (agent);
CREATE INDEX IF NOT EXISTS idx_chunks_created ON chunks (created_at);
"""


def chunk_id(agent: str, content: str) -> str:
    return hashlib.sha256(f"{agent}:{content}".encode()).hexdigest()[:32]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Chunk:
    id: str
    agent: str
    category: str
    content: str
    importance: int
    created_at: str
    lifecycle: str
    synced_at: Optional[str]
    cloud_id: Optional[int]
    source: str


class EdgeLayer:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Chunk CRUD ──────────────────────────────────────────────────────────

    def upsert_chunk(self, agent: str, category: str, content: str,
                     importance: int = 5, source: str = "edge") -> str:
        cid = chunk_id(agent, content)
        now = _now()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO chunks (id, agent, category, content, importance, created_at, lifecycle, source)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                ON CONFLICT (id) DO NOTHING
            """, (cid, agent, category, content, importance, now, source))
        return cid

    def get_chunk(self, cid: str) -> Optional[Chunk]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM chunks WHERE id = ?", (cid,)).fetchone()
        return _row_to_chunk(row) if row else None

    def set_lifecycle(self, cid: str, lifecycle: str,
                      cloud_id: Optional[int] = None) -> None:
        if lifecycle not in LIFECYCLE_STATES:
            raise ValueError(f"Invalid lifecycle: {lifecycle}")
        now = _now()
        synced_at = now if lifecycle == "synced" else None
        with self._conn() as conn:
            if cloud_id is not None:
                conn.execute("""
                    UPDATE chunks SET lifecycle = ?, synced_at = ?, cloud_id = ?
                    WHERE id = ?
                """, (lifecycle, synced_at, cloud_id, cid))
            else:
                conn.execute("""
                    UPDATE chunks SET lifecycle = ?,
                        synced_at = CASE WHEN ? = 'synced' THEN ? ELSE synced_at END
                    WHERE id = ?
                """, (lifecycle, lifecycle, now, cid))

    def get_pending(self, agent: Optional[str] = None,
                    limit: int = 100) -> list[Chunk]:
        with self._conn() as conn:
            if agent:
                rows = conn.execute(
                    "SELECT * FROM chunks WHERE lifecycle = 'pending' AND agent = ? LIMIT ?",
                    (agent, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM chunks WHERE lifecycle = 'pending' LIMIT ?",
                    (limit,)).fetchall()
        return [_row_to_chunk(r) for r in rows]

    def lifecycle_summary(self) -> dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT lifecycle, COUNT(*) as cnt FROM chunks GROUP BY lifecycle"
            ).fetchall()
        return {r["lifecycle"]: r["cnt"] for r in rows}

    # ── Sync state ──────────────────────────────────────────────────────────

    def get_sync_state(self, provider: str) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sync_state WHERE provider = ?", (provider,)
            ).fetchone()
        if row:
            return dict(row)
        return {"provider": provider, "last_cursor": None, "last_sync": None,
                "daily_calls": 0, "daily_budget": 500}

    def update_sync_state(self, provider: str, last_cursor: Optional[str] = None,
                          increment_calls: bool = True) -> None:
        state = self.get_sync_state(provider)
        daily_calls = state["daily_calls"] + (1 if increment_calls else 0)
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO sync_state (provider, last_cursor, last_sync, daily_calls, daily_budget)
                VALUES (?, ?, ?, ?, 500)
                ON CONFLICT (provider) DO UPDATE SET
                    last_cursor = COALESCE(excluded.last_cursor, last_cursor),
                    last_sync = excluded.last_sync,
                    daily_calls = excluded.daily_calls
            """, (provider, last_cursor or state["last_cursor"], _now(), daily_calls))

    def reset_daily_budgets(self) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE sync_state SET daily_calls = 0")

    def budget_ok(self, provider: str) -> bool:
        state = self.get_sync_state(provider)
        return state["daily_calls"] < state["daily_budget"]

    # ── Wiki entries ─────────────────────────────────────────────────────────

    def upsert_wiki_entry(self, slug: str, title: str, content: str,
                          tags: Optional[list] = None,
                          sources: Optional[list] = None) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO wiki_entries (slug, title, content, updated_at, tags, sources)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (slug) DO UPDATE SET
                    title = excluded.title,
                    content = excluded.content,
                    updated_at = excluded.updated_at,
                    tags = excluded.tags,
                    sources = excluded.sources
            """, (slug, title, content, _now(),
                  json.dumps(tags or []), json.dumps(sources or [])))

    def get_wiki_entry(self, slug: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM wiki_entries WHERE slug = ?", (slug,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tags"] = json.loads(d["tags"] or "[]")
        d["sources"] = json.loads(d["sources"] or "[]")
        return d


def _row_to_chunk(row: sqlite3.Row) -> Chunk:
    return Chunk(
        id=row["id"], agent=row["agent"], category=row["category"],
        content=row["content"], importance=row["importance"],
        created_at=row["created_at"], lifecycle=row["lifecycle"],
        synced_at=row["synced_at"], cloud_id=row["cloud_id"],
        source=row["source"],
    )


if __name__ == "__main__":
    edge = EdgeLayer()
    cid = edge.upsert_chunk("ALICE", "semantic", "Edge layer test chunk", importance=6)
    print(f"chunk_id: {cid}")
    print(f"lifecycle: {edge.lifecycle_summary()}")
    print(f"budget ok: {edge.budget_ok('memories_pull')}")
