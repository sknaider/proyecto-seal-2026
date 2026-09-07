"""SEAL Memory — mem0-compatible MemoryClient.

Compatibility facade for mem0's MemoryClient over the tenant-safe REST API.

    # Before (mem0)
    from mem0 import MemoryClient
    client = MemoryClient(api_key="m0-xxx")

    # After (SEAL — self-hosted, data sovereign)
    from seal_memory import MemoryClient
    client = MemoryClient(api_key="soul_live_...", base_url="https://memory.example.com")

    # Read operations keep the familiar shape:
    results = client.search("hiking", user_id="alice")
    all_mem = client.get_all(user_id="alice")

Write/extraction methods without a native REST equivalent fail locally. Direct
PostgreSQL access is intentionally disabled.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any


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


class MemoryClient:
    """
    mem0-compatible memory client backed by SEAL Soul DB.

    Contract v0.2 is REST-only. Direct database access is deliberately disabled
    so API-key identity and tenant RLS cannot be bypassed.

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
        if db_url is not None or host != "localhost" or db_port != 5433:
            raise NotImplementedError(
                "direct_db_disabled: use the tenant-safe REST API with base_url and api_key"
            )
        from .client import SealMemory

        self._api_key = api_key or os.environ.get("SEAL_API_KEY", "")
        self._rest = SealMemory(
            api_key=self._api_key,
            base_url=base_url or os.environ.get("SEAL_BASE_URL", "http://localhost:8767"),
        )
        self._db_url = None

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
        del entity, messages, metadata
        raise NotImplementedError("add is not exposed by the tenant-safe REST contract v0.2")

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
        Ranking is provided by the tenant-safe SOUL REST service.
        """
        entity = _resolve_entity(user_id=user_id, agent_id=agent_id, run_id=run_id)
        raw = self._rest.search(entity, query=query, limit=limit)
        items = raw if isinstance(raw, list) else raw.get("results", raw.get("memories", []))
        return {"results": [_norm(m) for m in items]}

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
        raw = self._rest.get_all(entity, limit=page_size, offset=offset)
        items = raw if isinstance(raw, list) else raw.get("results", [])
        return {"results": [_norm(m) for m in items]}

    def delete(self, memory_id: str) -> dict:
        """Soft-delete a memory (sets invalid_at)."""
        del memory_id
        raise NotImplementedError("delete is not exposed by the tenant-safe REST contract v0.2")

    def update(self, memory_id: str, data: str) -> dict:
        """Update memory content using ADD-only pattern (invalidates old, creates new).

        Returns: {"id": new_id, "memory": data, "event": "UPDATE", "message": ...}
        """
        del memory_id, data
        raise NotImplementedError("update is not exposed by the tenant-safe REST contract v0.2")

    def history(self, memory_id: str) -> dict:
        """Get version history chain for a memory.

        Returns mem0-compatible: {"results": [...]}
        """
        raise NotImplementedError("history is not exposed by the tenant-safe REST contract v0.2")

    def delete_all(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
    ) -> dict:
        """Soft-delete all memories for an entity."""
        del user_id, agent_id, run_id
        raise NotImplementedError("delete_all is not exposed by the tenant-safe REST contract v0.2")

    def __repr__(self) -> str:
        return "MemoryClient(mode='REST')"


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
