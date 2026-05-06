"""Audit log middleware.

Writes every successful or failed proxy request to `smg_audit_log` with
HMAC-SHA256 signature for tamper-evidence. Signatures are computed over a
canonical serialization of (agent, method, path, status, ts, latency_ms).

The middleware is fire-and-forget for latency: writes happen on a
background task so they don't block the response path.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

LOG = logging.getLogger("smg.audit")

_CREDENTIALS_ENV = Path("/home/dadito/.config/seal/credentials.env")


def _load_hmac_key() -> bytes:
    env_key = os.environ.get("SEAL_MCP_HMAC_KEY")
    if env_key:
        try:
            return base64.b64decode(env_key)
        except Exception:
            pass
    if _CREDENTIALS_ENV.exists():
        for line in _CREDENTIALS_ENV.read_text().splitlines():
            line = line.strip()
            if line.startswith("SEAL_MCP_HMAC_KEY="):
                try:
                    return base64.b64decode(line.split("=", 1)[1].strip())
                except Exception:
                    break
    return b""


class AuditLogger:
    """Append-only audit writer backed by PostgreSQL."""

    SCHEMA_DDL = """
    CREATE TABLE IF NOT EXISTS smg_audit_log (
        id            BIGSERIAL PRIMARY KEY,
        ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        agent         TEXT,
        method        TEXT NOT NULL,
        path          TEXT NOT NULL,
        status        INTEGER NOT NULL,
        latency_ms    INTEGER NOT NULL,
        backend       TEXT,
        extra         JSONB,
        signature     VARCHAR(64)
    );
    CREATE INDEX IF NOT EXISTS idx_smg_audit_ts ON smg_audit_log(ts DESC);
    CREATE INDEX IF NOT EXISTS idx_smg_audit_agent ON smg_audit_log(agent, ts DESC);
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self._key = _load_hmac_key()
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2048)
        self._worker_task: asyncio.Task | None = None
        self._stopped = False

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=4)
        async with self._pool.acquire() as conn:
            await conn.execute(self.SCHEMA_DDL)
        self._worker_task = asyncio.create_task(self._worker(), name="smg-audit-worker")
        LOG.info("Audit logger started (pool=ok, hmac_key=%s)", "loaded" if self._key else "missing")

    async def stop(self) -> None:
        self._stopped = True
        if self._worker_task:
            # drain queue briefly
            try:
                await asyncio.wait_for(self._queue.join(), timeout=3.0)
            except asyncio.TimeoutError:
                pass
            self._worker_task.cancel()
        if self._pool:
            await self._pool.close()

    def _sign(self, row: dict[str, Any]) -> str:
        if not self._key:
            return ""
        canonical = "|".join(
            [
                str(row.get("agent", "")),
                row["method"],
                row["path"],
                str(row["status"]),
                row["ts"].isoformat() if hasattr(row["ts"], "isoformat") else str(row["ts"]),
                str(row["latency_ms"]),
            ]
        )
        return hmac.new(self._key, canonical.encode(), hashlib.sha256).hexdigest()

    def record(self, **fields: Any) -> None:
        """Enqueue an audit record (non-blocking). Called from request hot path."""
        ts = fields.get("ts")
        if ts is None:
            ts = datetime.now(timezone.utc)
        row = {
            "ts": ts,
            "agent": fields.get("agent"),
            "method": fields.get("method", "?"),
            "path": fields.get("path", "?"),
            "status": int(fields.get("status", 0)),
            "latency_ms": int(fields.get("latency_ms", 0)),
            "backend": fields.get("backend"),
            "extra": fields.get("extra") or {},
        }
        row["signature"] = self._sign(row)
        try:
            self._queue.put_nowait(row)
        except asyncio.QueueFull:
            LOG.warning("Audit queue full; dropping record agent=%s path=%s", row["agent"], row["path"])

    async def _worker(self) -> None:
        assert self._pool is not None
        while not self._stopped:
            row = await self._queue.get()
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO smg_audit_log
                            (ts, agent, method, path, status, latency_ms, backend, extra, signature)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
                        """,
                        row["ts"],
                        row["agent"],
                        row["method"],
                        row["path"],
                        row["status"],
                        row["latency_ms"],
                        row["backend"],
                        json.dumps(row["extra"]),
                        row["signature"],
                    )
            except Exception as e:
                LOG.error("Audit write failed: %s", e)
            finally:
                self._queue.task_done()
