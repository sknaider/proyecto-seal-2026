"""SEAL Context Manager — Capa 1+2+3+4 orchestration (spec v3)

Manages the lifecycle of a SEAL agent session:
  - Persists every turn to soul_v3.session_turns (Capa 1)
  - Externalizes middle turns to memories when turn_count > externalize_after (Capa 1)
  - Compresses middle via Ollama with tool-block flatten + soft-hide fallback (Capa 2)
  - Writes session digest + chains sessions via parent_id (Capa 3)
  - Extracts facts per significant turn ADD-only (Capa 4)
  - Bitemporal: invalid_at / hidden columns, never DELETE

Called from:
  - memory/pre_compact_hook.py  (flush + digest before compact)
  - memory/post_compact_hook.py (open new chained session)
  - Claude Code UserPromptSubmit / Stop hooks (on_turn per turn)
  - Claude Code PostToolBatch hook (extract_facts_from_turn)
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
    "postgresql://seal:REDACTADO@localhost:5433/seal_memory",
)
SCHEMA = "soul_v3"
log = logging.getLogger(__name__)


@dataclass
class ContextManagerConfig:
    # Capa 1
    externalize_after: int = 40
    keep_head: int = 6
    keep_tail: int = 20

    # Capa 2 (v3)
    tier1_at_pct: float = 0.40
    compress_at_pct: float = 0.50
    compress_min_turns: int = 30
    target_summary_ratio: float = 0.20
    max_summary_pct: float = 0.05
    token_buffer_pct: float = 0.10

    # Capa 4 (v3)
    extract_enabled: bool = True
    extract_min_content: int = 200
    extract_max_facts: int = 5
    extract_timeout_s: float = 1.5

    enabled_layers: set[int] = field(default_factory=lambda: {1, 2, 3, 4})


class ContextManager:
    """Orchestrates all four context management layers for one agent session."""

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
        """Persist one turn and trigger externalization + compression checks."""
        if not self.session_id:
            await self.open_session()

        from token_estimator import estimate, context_pct as _ctx_pct

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

        if 2 in self.cfg.enabled_layers:
            pct = await self.estimate_context_pct()
            if pct >= self.cfg.compress_at_pct:
                asyncio.create_task(self.compress_middle())

    # ── Capa 1: Turn Externalization ──────────────────────────────────────────

    async def _externalize_turns(self, conn: asyncpg.Connection) -> int:
        rows = await conn.fetch(
            f"""
            SELECT id, turn_index, role, content, tokens_est
              FROM {SCHEMA}.session_turns
             WHERE session_id = $1
               AND externalized_at IS NULL
               AND (invalid_at IS NULL)
               AND hidden = false
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
                           invalid_at      = now(),
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

    async def externalize_specific(self, turn_ids: list[int]) -> int:
        """Externalize specific turn IDs — MCP tool target."""
        if not turn_ids or not self.session_id:
            return 0
        conn = await self._conn()
        try:
            rows = await conn.fetch(
                f"""
                SELECT id, turn_index, role, content, tokens_est
                  FROM {SCHEMA}.session_turns
                 WHERE id = ANY($1::bigint[])
                   AND session_id = $2
                   AND externalized_at IS NULL
                """,
                turn_ids,
                self.session_id,
            )
            count = 0
            for turn in rows:
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
                    }),
                )
                await conn.execute(
                    f"""
                    UPDATE {SCHEMA}.session_turns
                       SET externalized_at = now(),
                           invalid_at      = now(),
                           memory_id = $1
                     WHERE id = $2
                    """,
                    mem_row["id"],
                    turn["id"],
                )
                count += 1
            return count
        finally:
            await conn.close()

    async def flush_pending_turns(self) -> int:
        """Force-externalize all externalizable turns. Call from pre_compact_hook."""
        return await self.externalize_turns()

    # ── Bitemporal helpers ────────────────────────────────────────────────────

    async def invalidate_turns(self, turn_ids: list[int], reason: str = "invalidated") -> int:
        """Mark turns as invalid_at = now() — bitemporal, non-destructive."""
        if not turn_ids:
            return 0
        conn = await self._conn()
        try:
            result = await conn.execute(
                f"""
                UPDATE {SCHEMA}.session_turns
                   SET invalid_at = now(),
                       hidden_reason = COALESCE(hidden_reason, $1)
                 WHERE id = ANY($2::bigint[])
                   AND invalid_at IS NULL
                """,
                reason,
                turn_ids,
            )
            count = int(result.split()[-1]) if result else 0
            log.debug("[CM] invalidated %d turns", count)
            return count
        finally:
            await conn.close()

    async def vigentes_turns(self) -> list[dict]:
        """Return active turns: invalid_at IS NULL AND hidden = false."""
        if not self.session_id:
            return []
        conn = await self._conn()
        try:
            rows = await conn.fetch(
                f"""
                SELECT id, turn_index, role, content, tokens_est, created_at
                  FROM {SCHEMA}.session_turns
                 WHERE session_id = $1
                   AND invalid_at IS NULL
                   AND hidden = false
                 ORDER BY turn_index
                """,
                self.session_id,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    async def unhide_turns(self, turn_ids: list[int]) -> int:
        """Reverse soft-hide for specific turns."""
        from context_compressor import unhide_turns as _unhide
        return await _unhide(self.session_id, turn_ids)

    # ── Capa 2: Proactive Compression ─────────────────────────────────────────

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
                   AND invalid_at IS NULL
                   AND hidden = false
                """,
                self.session_id,
            )
            from token_estimator import context_pct
            return context_pct(row["total_tokens"])
        finally:
            await conn.close()

    # ── Capa 3: Session Chain ─────────────────────────────────────────────────

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

    # ── Capa 4: Proactive Turn Extraction ─────────────────────────────────────

    async def extract_facts_from_turn(
        self,
        turn_content: str,
        turn_index: int | None = None,
        tool_names: list[str] | None = None,
        tool_outputs: list[str] | None = None,
    ) -> int:
        """Single-pass ADD-only fact extraction from a significant turn."""
        if 4 not in self.cfg.enabled_layers or not self.cfg.extract_enabled:
            return 0
        if not self.session_id:
            return 0
        try:
            from turn_extractor import extract_and_store
            return await extract_and_store(
                agent=self.agent,
                session_id=self.session_id,
                turn_index=turn_index or 0,
                turn_content=turn_content,
                tool_names=tool_names,
                tool_outputs=tool_outputs,
            )
        except Exception as exc:
            log.warning("[CM] extract_facts failed: %s", exc)
            return 0

    # ── Convenience ───────────────────────────────────────────────────────────

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
