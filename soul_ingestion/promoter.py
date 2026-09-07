"""Durable least-privilege promoter for human-reviewed SUIE candidates."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

from .memory_writer import CanonicalMemoryWriter
from .service import DEFAULT_TENANT, _configure_connection, _database_dsn, _load_or_create_token


TOKEN_PATH = Path(
    os.environ.get("SUIE_PROMOTER_TOKEN_FILE", "var/soul_ingestion/promoter.token")
).resolve()
MEMORY_TOKEN_PATH = Path(
    os.environ.get("SUIE_MEMORY_TOKEN_FILE", "/run/user/1000/seal/ADA.token")
).resolve()
MCP_URL = os.environ.get("SUIE_MCP_URL", "http://127.0.0.1:8771/mcp")
STATUS_PATH = Path(
    os.environ.get("SUIE_PROMOTER_STATUS_FILE", "var/soul_ingestion/promoter_status.json")
).resolve()


class PromotionWorker:
    def __init__(self, pool: asyncpg.Pool, writer: CanonicalMemoryWriter, *, worker_id: str) -> None:
        self.pool = pool
        self.writer = writer
        self.worker_id = worker_id

    async def _call(self, tenant_id: UUID, query: str, *args: object) -> Any:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SET LOCAL ROLE pr_ingestion_promoter")
                await connection.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", str(tenant_id)
                )
                return await connection.fetchval(query, *args)

    async def claim(self, tenant_id: UUID) -> dict[str, Any] | None:
        result = await self._call(
            tenant_id,
            "SELECT soul_v3.ingestion_promoter_claim($1,$2,60)",
            tenant_id,
            self.worker_id,
        )
        return dict(result) if result else None

    async def resolve_memory(self, tenant_id: UUID, candidate_id: UUID) -> int | None:
        return await self._call(
            tenant_id,
            "SELECT soul_v3.ingestion_promoted_memory($1,$2)",
            tenant_id,
            candidate_id,
        )

    async def complete(self, tenant_id: UUID, task: dict[str, Any], memory_id: int) -> str:
        return await self._call(
            tenant_id,
            "SELECT soul_v3.ingestion_promoter_complete($1,$2,$3,$4)",
            tenant_id,
            int(task["outbox_id"]),
            self.worker_id,
            memory_id,
        )

    async def fail(self, tenant_id: UUID, task: dict[str, Any], error: Exception) -> bool:
        retry_seconds = min(300, 2 ** min(int(task.get("attempts", 1)), 8))
        safe_error = f"{type(error).__name__}: {str(error)[:400]}"
        return bool(
            await self._call(
                tenant_id,
                "SELECT soul_v3.ingestion_promoter_fail($1,$2,$3,$4,$5)",
                tenant_id,
                int(task["outbox_id"]),
                self.worker_id,
                safe_error,
                retry_seconds,
            )
        )

    async def process_once(self, tenant_id: UUID = DEFAULT_TENANT) -> dict[str, Any]:
        task = await self.claim(tenant_id)
        if task is None:
            return {"status": "idle"}
        try:
            candidate_id = UUID(str(task["candidate_id"]))
            memory_id = int(task["memory_id"]) if task.get("memory_id") is not None else None
            if task["action"] == "promote":
                memory_id = memory_id or await self.resolve_memory(tenant_id, candidate_id)
                if memory_id is None:
                    try:
                        memory_id = await self.writer.store(task)
                    except Exception:
                        memory_id = await self.resolve_memory(tenant_id, candidate_id)
                        if memory_id is None:
                            raise
            else:
                memory_id = memory_id or await self.resolve_memory(tenant_id, candidate_id)
                if memory_id is None:
                    raise RuntimeError("revocation has no canonical memory binding")
                await self.writer.invalidate(
                    memory_id,
                    agent=str(task["proposed_agent"]),
                    reason=f"SUIE human revocation: {task['approval_binding_sha256']}",
                )
            state = await self.complete(tenant_id, task, int(memory_id))
            return {"status": state, "outbox_id": task["outbox_id"], "memory_id": memory_id}
        except Exception as exc:
            await self.fail(tenant_id, task, exc)
            return {"status": "error", "outbox_id": task["outbox_id"], "error": type(exc).__name__}


def _write_status(payload: dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATUS_PATH.parent.chmod(0o700)
    output = {**payload, "observed_at": datetime.now(UTC).isoformat()}
    temporary = STATUS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(output, sort_keys=True), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(STATUS_PATH)


async def run(*, once: bool = False) -> int:
    token = _load_or_create_token(TOKEN_PATH)
    pool = await asyncpg.create_pool(
        _database_dsn(token, login="svc_soul_ingestion_promoter"),
        min_size=1,
        max_size=2,
        command_timeout=20,
        init=_configure_connection,
    )
    worker = PromotionWorker(
        pool,
        CanonicalMemoryWriter(MCP_URL, MEMORY_TOKEN_PATH),
        worker_id=f"{socket.gethostname()}:{os.getpid()}",
    )
    try:
        while True:
            result = await worker.process_once()
            _write_status(result)
            if once:
                return 1 if result["status"] == "error" else 0
            await asyncio.sleep(0.5 if result["status"] != "idle" else 2.0)
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    raise SystemExit(asyncio.run(run(once=parser.parse_args().once)))
