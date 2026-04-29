"""SEAL Context Manager — Capa 1: Turn Externalization + orchestration.

Manages the lifecycle of a SEAL agent session:
  - Persists every turn to soul_v3.session_turns
  - Externalizes old middle turns to memories when turn_count > EXTERNALIZE_AFTER
  - Delegates compression (Capa 2) and session chaining (Capa 3) to sibling modules

Called from:
  - memory/pre_compact_hook.py  (flush + digest before compact)
  - memory/post_compact_hook.py (open new chained session)
  - Claude Code UserPromptSubmit / Stop hooks (on_turn per turn)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg

DB_URL = os.environ.get(
    "SEAL_DB_URL",
    "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory",
)
SCHEMA = "soul_v3"

log = logging.getLogger(__name__)

EXTERNALIZE_AFTER = 40
KEEP_HEAD = 6
KEEP_TAIL = 20
COMPRESS_AT_PCT = 0.70


@dataclass
class ContextManagerConfig:
    externalize_after: int = EXTERNALIZE_AFTER
    keep_head: int = KEEP_HEAD
    keep_tail: int = KEEP_TAIL
    compress_at_pct: float = COMPRESS_AT_PCT
    enabled_layers: set[int] = field(default_factory=lambda: {1, 2, 3})


class ContextManager:
    """Orchestrates Turn Externalization (Capa 1) for one agent session."""

    def __init__(self, agent: str, config: ContextManagerConfig | None = None):
        self.agent = agent
        self.cfg = config or ContextManagerConfig()
        self.session_id: UUID | None = None

    # ── DB helpers ────────────────────────────────────────────────────────────

    async def _conn(self) -> asyncpg.Connection:
        return await asyncpg.connect(DB_URL, server_settings={"search_path": SCHEMA})

    # ── Session lifecycle ─────────────────────────────────────────────────────

    async def open_session(self, parent_id: UUID | None = None) -> UUID:
        conn = await self._conn()
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {SCHEMA}.session_chain
                    (agent, parent_id, started_at)
                VALUES ($1, $2, now())
                RETURNING id
                """,
                self.agent,
                parent_id,
            )
            self.session_id = row["id"]
            await conn.execute(
                f"""
                UPDATE {SCHEMA}.working_state
                   SET active_session_id = $1
                 WHERE agent = $2::varchar
                """,
                self.session_id,
                self.agent,
            )
            log.info("[CM] opened session %s (parent=%s)", self.session_id, parent_id)
            return self.session_id
        finally:
            await conn.close()

    async def close_session(self, reason: str = "clean_exit") -> str | None:
        if not self.session_id:
            return None
        conn = await self._conn()
        try:
            await conn.execute(
                f"""
                UPDATE {SCHEMA}.session_chain
                   SET ended_at = now(), ended_reason = $1
                 WHERE id = $2
                """,
                reason,
                self.session_id,
            )
            log.info("[CM] closed session %s reason=%s", self.session_id, reason)
            return str(self.session_id)
        finally:
            await conn.close()

    # ── Per-turn recording ────────────────────────────────────────────────────

    async def on_turn(self, role: str, content: str) -> None:
        """Persist one turn and trigger externalization if threshold reached."""
        if not self.session_id:
            await self.open_session()

        from token_estimator import estimate

        conn = await self._conn()
        try:
            row = await conn.fetchrow(
                f"""
                SELECT COALESCE(MAX(turn_index), -1) + 1 AS next_idx,
                       COUNT(*) AS turn_count
                  FROM {SCHEMA}.session_turns
                 WHERE session_id = $1
                """,
                self.session_id,
            )
            next_idx = row["next_idx"]
            turn_count = row["turn_count"] + 1

            await conn.execute(
                f"""
                INSERT INTO {SCHEMA}.session_turns
                    (session_id, turn_index, role, content, tokens_est, created_at)
                VALUES ($1, $2, $3, $4, $5, now())
                ON CONFLICT (session_id, turn_index) DO NOTHING
                """,
                self.session_id,
                next_idx,
                role,
                content,
                estimate(content),
            )

            await conn.execute(
                f"""
                UPDATE {SCHEMA}.session_chain
                   SET turn_count = $1
                 WHERE id = $2
                """,
                turn_count,
                self.session_id,
            )

            if 1 in self.cfg.enabled_layers and turn_count > self.cfg.externalize_after:
                await self._externalize_turns(conn)

        finally:
            await conn.close()

    # ── Capa 1: Turn Externalization ──────────────────────────────────────────

    async def _externalize_turns(self, conn: asyncpg.Connection) -> int:
        rows = await conn.fetch(
            f"""
            SELECT id, turn_index, role, content, tokens_est
              FROM {SCHEMA}.session_turns
             WHERE session_id = $1
               AND externalized_at IS NULL
             ORDER BY turn_index
            """,
            self.session_id,
        )
        total = len(rows)
        if total <= self.cfg.keep_head + self.cfg.keep_tail:
            return 0

        middle = rows[self.cfg.keep_head: total - self.cfg.keep_tail]
        if not middle:
            return 0

        externalized = 0
        for turn in middle:
            try:
                mem_row = await conn.fetchrow(
                    f"""
                    INSERT INTO {SCHEMA}.memories
                        (agent, type, content, category, importance, metadata, created_at)
                    VALUES ($1, 'session_turn', $2, 'context', 3, $3::jsonb, now())
                    RETURNING id
                    """,
                    self.agent,
                    turn["content"],
                    json.dumps({
                        "session_id": str(self.session_id),
                        "turn_index": turn["turn_index"],
                        "role": turn["role"],
                        "tokens_est": turn["tokens_est"],
                    }),
                )
                await conn.execute(
                    f"""
                    UPDATE {SCHEMA}.session_turns
                       SET externalized_at = now(),
                           memory_id = $1
                     WHERE id = $2
                    """,
                    mem_row["id"],
                    turn["id"],
                )
                externalized += 1
            except Exception as exc:
                log.warning("[CM] externalize turn %d failed: %s", turn["turn_index"], exc)

        if externalized:
            log.info("[CM] externalized %d turns from session %s", externalized, self.session_id)
        return externalized

    async def externalize_turns(self) -> int:
        conn = await self._conn()
        try:
            return await self._externalize_turns(conn)
        finally:
            await conn.close()

    async def flush_pending_turns(self) -> int:
        """Force-externalize all externalizable turns. Call from pre_compact_hook."""
        return await self.externalize_turns()

    # ── Capa 2: Proactive Compression delegation ──────────────────────────────

    async def compress_middle(self) -> str | None:
        if 2 not in self.cfg.enabled_layers or not self.session_id:
            return None
        try:
            from context_compressor import compress_session_middle
            return await compress_session_middle(self.agent, self.session_id, self.cfg)
        except Exception as exc:
            log.warning("[CM] compress_middle failed: %s", exc)
            return None

    async def estimate_context_pct(self) -> float:
        if not self.session_id:
            return 0.0
        conn = await self._conn()
        try:
            row = await conn.fetchrow(
                f"""
                SELECT COALESCE(SUM(tokens_est), 0) AS total_tokens
                  FROM {SCHEMA}.session_turns
                 WHERE session_id = $1
                   AND externalized_at IS NULL
                """,
                self.session_id,
            )
            from token_estimator import context_pct
            return context_pct(row["total_tokens"])
        finally:
            await conn.close()

    # ── Capa 3: Session Chain delegation ─────────────────────────────────────

    async def write_session_digest(self) -> str | None:
        if 3 not in self.cfg.enabled_layers or not self.session_id:
            return None
        try:
            from session_chain import write_digest
            return await write_digest(self.agent, self.session_id)
        except Exception as exc:
            log.warning("[CM] write_session_digest failed: %s", exc)
            return None

    async def get_session_context(self) -> dict[str, Any]:
        """Assemble context dict for boot/post_compact injection."""
        conn = await self._conn()
        try:
            last_closed = await conn.fetchrow(
                f"""
                SELECT id, digest, digest_tokens, ended_at, turn_count
                  FROM {SCHEMA}.session_chain
                 WHERE agent = $1
                   AND ended_at IS NOT NULL
                 ORDER BY ended_at DESC
                 LIMIT 1
                """,
                self.agent,
            )

            recent_turns = await conn.fetch(
                f"""
                SELECT role, content, turn_index
                  FROM {SCHEMA}.session_turns
                 WHERE session_id = $1
                   AND externalized_at IS NOT NULL
                 ORDER BY turn_index DESC
                 LIMIT 10
                """,
                self.session_id,
            ) if self.session_id else []

            return {
                "last_digest": last_closed["digest"] if last_closed else None,
                "last_session_turn_count": last_closed["turn_count"] if last_closed else 0,
                "recent_externalized": [
                    {"role": t["role"], "content": t["content"][:300]}
                    for t in reversed(recent_turns)
                ],
                "active_session_id": str(self.session_id) if self.session_id else None,
            }
        finally:
            await conn.close()

    # ── Convenience: last open session ───────────────────────────────────────

    @classmethod
    async def resume_or_open(cls, agent: str) -> "ContextManager":
        """Resume the last open session for agent, or open a new one."""
        cm = cls(agent)
        conn = await cm._conn()
        try:
            row = await conn.fetchrow(
                f"""
                SELECT id FROM {SCHEMA}.session_chain
                 WHERE agent = $1 AND ended_at IS NULL
                 ORDER BY started_at DESC LIMIT 1
                """,
                agent,
            )
            if row:
                cm.session_id = row["id"]
            else:
                last_closed = await conn.fetchrow(
                    f"""
                    SELECT id FROM {SCHEMA}.session_chain
                     WHERE agent = $1 AND ended_at IS NOT NULL
                     ORDER BY ended_at DESC LIMIT 1
                    """,
                    agent,
                )
                parent_id = last_closed["id"] if last_closed else None
                await cm.open_session(parent_id=parent_id)
        finally:
            await conn.close()
        return cm
