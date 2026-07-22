"""SEAL Memory — mem0-compatible MemoryClient.

True drop-in replacement for mem0's MemoryClient.
Change 1 line, keep everything else identical.

    # Before (mem0)
    from mem0 import MemoryClient
    client = MemoryClient(api_key="m0-xxx")

    # After (SEAL — self-hosted, data sovereign)
    from seal_memory import MemoryClient
    client = MemoryClient()          # no key needed locally
    # or:
    client = MemoryClient(host="seal.yourcompany.com")

    # Identical from here on:
    client.add([{"role":"user","content":"I love hiking"}], user_id="alice")
    results = client.search("hiking", user_id="alice")
    all_mem = client.get_all(user_id="alice")
    client.update(memory_id, "corrected content")
    client.delete(memory_id)
    client.delete_all(user_id="alice")
    client.history(memory_id)
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any


def _load_credentials_file() -> None:
    path = os.path.expanduser("~/.config/seal/credentials.env")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('\"').strip("'"))


def _default_db() -> str:
    _load_credentials_file()
    for key in ("SEAL_DB_DSN", "SEAL_DB_URL", "SEAL_PG_DSN"):
        if os.environ.get(key):
            return os.environ[key]
    password = os.environ.get("PG_PASSWORD") or os.environ.get("SEAL_DB_PASS")
    if not password:
        raise RuntimeError("Missing PG_PASSWORD/SEAL_DB_PASS for SEAL DB")
    host = os.environ.get("PG_HOST", "localhost")
    port = os.environ.get("PG_PORT", "5433")
    user = os.environ.get("PG_USER", "seal")
    database = os.environ.get("PG_DATABASE", "seal_memory")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


_DEFAULT_DB = _default_db()


def _resolve_entity(user_id=None, agent_id=None, run_id=None) -> str:
    """Map mem0 entity kwargs to SEAL agent string (priority: agent_id > user_id > run_id)."""
    if agent_id:
        return str(agent_id)
    if user_id:
        return str(user_id)
    if run_id:
        return f"run_{run_id}"
    raise ValueError("Provide at least one of: user_id, agent_id, run_id")


def _iso(dt) -> str:
    if dt is None:
        return datetime.now(timezone.utc).isoformat()
    if hasattr(dt, "isoformat"):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(dt)


def _run_sync(coro):
    """Run an async coroutine from sync context, compatible with nested event loops."""
    try:
        loop = asyncio.get_running_loop()
        # Already inside an event loop — use ThreadPoolExecutor
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)


class MemoryClient:
    """
    mem0-compatible memory client backed by SEAL Soul DB.

    Modes:
      - Direct DB (default): connects to PostgreSQL Soul DB via asyncpg.
        No extra server needed. Requires: pip install asyncpg
      - REST (optional): wraps SealMemory REST API.
        Use: MemoryClient(base_url="http://localhost:8767", api_key="...")

    mem0 parity:
      add / search / get_all / delete / update / history / delete_all
    """

    def __init__(
        self,
        api_key: str | None = None,
        host: str = "localhost",
        db_port: int = 5433,
        db_url: str | None = None,
        base_url: str | None = None,
    ):
        self._api_key = api_key  # accepted for API compat, ignored in direct mode
        if base_url:
            from .client import SealMemory
            self._rest = SealMemory(api_key=api_key or "", base_url=base_url)
            self._db_url = None
        else:
            self._rest = None
            self._db_url = db_url or _DEFAULT_DB

    # ── mem0 API surface ──────────────────────────────────────────────────────

    def add(
        self,
        messages: list[dict],
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Extract facts from messages and store as memories.

        Returns mem0-compatible: {"results": [...], "relations": []}
        """
        entity = _resolve_entity(user_id=user_id, agent_id=agent_id, run_id=run_id)
        if self._rest:
            raw = self._rest.add(entity, messages=messages)
            stored = raw.get("memories", [])
            results = [
                {
                    "id": str(m.get("id", "")),
                    "memory": m.get("content", ""),
                    "event": "ADD",
                    "created_at": _iso(m.get("created_at")),
                    "updated_at": _iso(m.get("updated_at")),
                    **({"metadata": metadata} if metadata else {}),
                }
                for m in stored
            ]
            return {"results": results, "relations": []}
        return _run_sync(self._db_add(entity, messages, metadata or {}))

    def search(
        self,
        query: str,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        limit: int = 10,
    ) -> dict:
        """Search memories using BM25 + importance ranking.

        Returns mem0-compatible: {"results": [{"id","memory","score","metadata",...}]}
        Semantic re-ranking available when Soul DB has embeddings via Qdrant.
        """
        entity = _resolve_entity(user_id=user_id, agent_id=agent_id, run_id=run_id)
        if self._rest:
            raw = self._rest.search(entity, query=query, limit=limit)
            items = raw if isinstance(raw, list) else raw.get("results", raw.get("memories", []))
            return {"results": [_norm(m) for m in items]}
        return _run_sync(self._db_search(entity, query, limit))

    def get_all(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """List all memories for an entity (paginated).

        Returns mem0-compatible: {"results": [...]}
        """
        entity = _resolve_entity(user_id=user_id, agent_id=agent_id, run_id=run_id)
        offset = (page - 1) * page_size
        if self._rest:
            raw = self._rest.get_all(entity, limit=page_size)
            items = raw if isinstance(raw, list) else raw.get("results", [])
            return {"results": [_norm(m) for m in items]}
        return _run_sync(self._db_get_all(entity, page_size, offset))

    def delete(self, memory_id: str) -> dict:
        """Soft-delete a memory (sets invalid_at)."""
        if self._rest:
            raise NotImplementedError(
                "delete() in REST mode requires agent_id context. "
                "Use SealMemory(api_key, base_url).delete(agent_id, memory_id) instead."
            )
        return _run_sync(self._db_delete(str(memory_id)))

    def update(self, memory_id: str, data: str) -> dict:
        """Update memory content using ADD-only pattern (invalidates old, creates new).

        Returns: {"id": new_id, "memory": data, "event": "UPDATE", "message": ...}
        """
        if self._rest:
            raise NotImplementedError(
                "update() in REST mode requires agent_id context. "
                "Use SealMemory(api_key, base_url).update(agent_id, memory_id, content=data) instead."
            )
        return _run_sync(self._db_update(str(memory_id), data))

    def history(self, memory_id: str) -> dict:
        """Get version history chain for a memory.

        Returns mem0-compatible: {"results": [...]}
        """
        return _run_sync(self._db_history(str(memory_id)))

    def delete_all(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
    ) -> dict:
        """Soft-delete all memories for an entity."""
        entity = _resolve_entity(user_id=user_id, agent_id=agent_id, run_id=run_id)
        return _run_sync(self._db_delete_all(entity))

    # ── DB backend (asyncpg direct) ───────────────────────────────────────────

    async def _conn(self):
        try:
            import asyncpg
        except ImportError:
            raise ImportError(
                "MemoryClient direct mode requires asyncpg.\n"
                "  pip install asyncpg\n"
                "Or use REST mode: MemoryClient(base_url='http://localhost:8767', api_key='...')"
            )
        return await asyncpg.connect(self._db_url, timeout=5.0)

    async def _db_add(self, entity: str, messages: list[dict], metadata: dict) -> dict:
        contents = [
            m["content"].strip()
            for m in messages
            if m.get("role") == "user" and len((m.get("content") or "").strip()) > 10
        ]
        if not contents:
            return {"results": [], "relations": []}

        conn = await self._conn()
        results = []
        try:
            now = datetime.now(timezone.utc)
            meta_json = json.dumps(metadata) if metadata else "{}"
            for content in contents:
                existing = await conn.fetchval(
                    "SELECT id FROM memories WHERE agent=$1 AND content=$2 AND invalid_at IS NULL LIMIT 1",
                    entity, content,
                )
                if existing:
                    results.append({
                        "id": str(existing), "memory": content, "event": "NONE",
                        "created_at": now.isoformat(), "updated_at": now.isoformat(),
                        "metadata": metadata,
                    })
                    continue

                row_id = await conn.fetchval("""
                    INSERT INTO memories
                      (agent, category, content, importance, confidence_score,
                       source, valid_from, created_at, provenance, metadata)
                    VALUES ($1,'fact',$2,5,0.75,'mem0_compat_sdk',$3,$3,$4,$5::jsonb)
                    RETURNING id
                """, entity, content, now,
                    f"mem0-compat SDK — {now.strftime('%Y-%m-%d %H:%M')}",
                    meta_json,
                )
                results.append({
                    "id": str(row_id), "memory": content, "event": "ADD",
                    "created_at": now.isoformat(), "updated_at": now.isoformat(),
                    "metadata": metadata,
                })
        finally:
            await conn.close()
        return {"results": results, "relations": []}

    async def _db_search(self, entity: str, query: str, limit: int) -> dict:
        conn = await self._conn()
        try:
            # search_vector is populated with English stemming by mcp_server_v3
            rows = await conn.fetch("""
                SELECT id, content, importance, created_at, metadata,
                       ts_rank(
                           COALESCE(search_vector, to_tsvector('english', content)),
                           plainto_tsquery('english', $2)
                       ) AS rank
                FROM memories
                WHERE agent = $1 AND invalid_at IS NULL
                  AND COALESCE(search_vector, to_tsvector('english', content))
                      @@ plainto_tsquery('english', $2)
                ORDER BY rank DESC, importance DESC
                LIMIT $3
            """, entity, query, limit)

            if not rows:
                # Fallback: ILIKE on full query string (parameterized — no injection)
                rows = await conn.fetch("""
                    SELECT id, content, importance, created_at, metadata,
                           0.3::float AS rank
                    FROM memories
                    WHERE agent = $1 AND invalid_at IS NULL
                      AND content ILIKE $2
                    ORDER BY importance DESC, created_at DESC
                    LIMIT $3
                """, entity, f"%{query}%", limit)

            results = [
                {
                    "id": str(r["id"]),
                    "memory": r["content"],
                    "score": float(r["rank"]),
                    "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
                    "created_at": _iso(r["created_at"]),
                    "updated_at": _iso(r["created_at"]),
                }
                for r in rows
            ]
        finally:
            await conn.close()
        return {"results": results}

    async def _db_get_all(self, entity: str, limit: int, offset: int) -> dict:
        conn = await self._conn()
        try:
            rows = await conn.fetch("""
                SELECT id, content, importance, created_at, metadata
                FROM memories
                WHERE agent = $1 AND invalid_at IS NULL
                ORDER BY importance DESC, created_at DESC
                LIMIT $2 OFFSET $3
            """, entity, limit, offset)
            results = [
                {
                    "id": str(r["id"]),
                    "memory": r["content"],
                    "score": 1.0,
                    "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
                    "created_at": _iso(r["created_at"]),
                    "updated_at": _iso(r["created_at"]),
                }
                for r in rows
            ]
        finally:
            await conn.close()
        return {"results": results}

    async def _db_delete(self, memory_id: str) -> dict:
        conn = await self._conn()
        try:
            await conn.execute(
                "UPDATE memories SET invalid_at=$1 WHERE id=$2::bigint AND invalid_at IS NULL",
                datetime.now(timezone.utc), int(memory_id),
            )
        finally:
            await conn.close()
        return {"message": "Memory deleted successfully!"}

    async def _db_update(self, memory_id: str, data: str) -> dict:
        conn = await self._conn()
        try:
            now = datetime.now(timezone.utc)
            old = await conn.fetchrow(
                "SELECT agent, category, importance, metadata FROM memories "
                "WHERE id=$1::bigint AND invalid_at IS NULL",
                int(memory_id),
            )
            if not old:
                return {"message": "Memory not found", "id": memory_id}

            await conn.execute(
                "UPDATE memories SET invalid_at=$1 WHERE id=$2::bigint",
                now, int(memory_id),
            )
            new_id = await conn.fetchval("""
                INSERT INTO memories
                  (agent, category, content, importance, confidence_score,
                   source, valid_from, created_at, provenance,
                   consolidation_parent_id, metadata)
                VALUES ($1,$2,$3,$4,0.75,'mem0_compat_sdk',$5,$5,$6,$7,$8::jsonb)
                RETURNING id
            """, old["agent"], old["category"], data, old["importance"], now,
                f"mem0-compat update — {now.strftime('%Y-%m-%d %H:%M')}",
                int(memory_id), old["metadata"],
            )
        finally:
            await conn.close()
        return {"id": str(new_id), "memory": data, "event": "UPDATE",
                "message": "Memory updated successfully!"}

    async def _db_history(self, memory_id: str) -> dict:
        conn = await self._conn()
        try:
            rows = await conn.fetch("""
                WITH RECURSIVE chain AS (
                    SELECT id, content, created_at, invalid_at, consolidation_parent_id
                    FROM memories WHERE id = $1::bigint
                    UNION ALL
                    SELECT m.id, m.content, m.created_at, m.invalid_at, m.consolidation_parent_id
                    FROM memories m JOIN chain c ON m.id = c.consolidation_parent_id
                )
                SELECT id, content, created_at, invalid_at FROM chain ORDER BY created_at
            """, int(memory_id))
            history = [
                {
                    "id": str(r["id"]),
                    "memory": r["content"],
                    "event": "DELETE" if r["invalid_at"] else "ADD",
                    "created_at": _iso(r["created_at"]),
                    "updated_at": _iso(r["invalid_at"] or r["created_at"]),
                    "prev_memory": None,
                }
                for r in rows
            ]
            # Annotate prev_memory for richer diff
            for i, h in enumerate(history):
                if i > 0:
                    h["prev_memory"] = history[i - 1]["memory"]
        finally:
            await conn.close()
        return {"results": history}

    async def _db_delete_all(self, entity: str) -> dict:
        conn = await self._conn()
        try:
            now = datetime.now(timezone.utc)
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL",
                entity,
            )
            await conn.execute(
                "UPDATE memories SET invalid_at=$1 WHERE agent=$2 AND invalid_at IS NULL",
                now, entity,
            )
        finally:
            await conn.close()
        return {"message": f"Deleted {count} memories for '{entity}'"}

    def __repr__(self) -> str:
        mode = "REST" if self._rest else "Direct-DB"
        return f"MemoryClient(mode={mode!r})"


def _norm(m: dict) -> dict:
    """Normalize a SEAL memory dict to mem0 response format."""
    return {
        "id": str(m.get("id", "")),
        "memory": m.get("content", m.get("memory", "")),
        "score": float(m.get("semantic_score", m.get("score", m.get("decayed_score", 0.0)))),
        "metadata": m.get("metadata", m.get("meta", {})) or {},
        "created_at": _iso(m.get("created_at")),
        "updated_at": _iso(m.get("updated_at", m.get("created_at"))),
    }
