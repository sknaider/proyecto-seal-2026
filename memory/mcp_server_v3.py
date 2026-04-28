"""mcp_server_v3.py — Test adapter for soul-v3 MCP server (port 8771).
Provides direct-callable async functions. Not a monolith — delegates to
the live soul-v3 server via MCP client protocol.
"""
from __future__ import annotations
import json
from typing import Optional

import asyncpg
from mcp import ClientSession
from mcp.client.sse import sse_client

_SERVER_URL = "http://127.0.0.1:8771/sse"
_DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"

# Legacy reset attributes (no-ops in v3 — no module-level singletons)
_qdrant = None
_neo4j_driver = None
_pool = None


async def get_pool() -> asyncpg.Pool:
    """Return a direct asyncpg pool to soul_v3 DB (for test setup/teardown)."""
    global _pool
    if _pool is None or _pool._closed:
        _pool = await asyncpg.create_pool(
            _DB_URL, min_size=1, max_size=3,
            server_settings={"search_path": "soul_v3"},
        )
    return _pool


async def _call(tool_name: str, **kwargs) -> any:
    """Call a soul-v3 MCP tool via SSE client. Returns parsed result."""
    clean_kwargs = {k: v for k, v in kwargs.items() if v is not None}
    async with sse_client(_SERVER_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=clean_kwargs)
            if not result.content:
                return {}
            text = result.content[0].text
            try:
                return json.loads(text)
            except Exception:
                return text


# ── TG-RAG tools ─────────────────────────────────────────────────────────────

async def temporal_graph_build(agent: Optional[str] = None) -> str:
    result = await _call("temporal_graph_build", agent=agent)
    return result if isinstance(result, str) else json.dumps(result)


async def temporal_summary_get(
    agent: str,
    period: str = "day",
    date_str: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
) -> dict:
    return await _call(
        "temporal_summary_get",
        agent=agent, period=period, date_str=date_str, year=year, month=month,
    )


async def temporal_query(
    agent: str,
    query: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    strategy: str = "local",
    top_k: int = 5,
) -> dict:
    return await _call(
        "temporal_query",
        agent=agent, query=query, start_date=start_date,
        end_date=end_date, strategy=strategy, top_k=top_k,
    )


# ── MAGMA tools ───────────────────────────────────────────────────────────────

async def magma_retrieve(
    agent: str,
    query: str,
    top_k: int = 5,
    views: Optional[list] = None,
    fuse: bool = True,
) -> str:
    result = await _call("magma_retrieve", agent=agent, query=query,
                         top_k=top_k, views=views, fuse=fuse)
    return result if isinstance(result, str) else json.dumps(result)


# ── ERL tools ────────────────────────────────────────────────────────────────

async def erl_reflect(
    agent: str,
    task_description: str,
    outcome: str,
    trajectory: str,
    context: Optional[str] = None,
    max_heuristics: int = 3,
) -> str:
    result = await _call(
        "erl_reflect",
        agent=agent, task_description=task_description, outcome=outcome,
        trajectory=trajectory, context=context, max_heuristics=max_heuristics,
    )
    return result if isinstance(result, str) else json.dumps(result)


async def erl_inject(
    agent: str,
    task_description: str,
    top_k: int = 3,
    min_confidence: float = 0.5,
    min_activation_count: int = 0,
) -> str:
    result = await _call(
        "erl_inject",
        agent=agent, task_description=task_description, top_k=top_k,
        min_confidence=min_confidence, min_activation_count=min_activation_count,
    )
    return result if isinstance(result, str) else json.dumps(result)


# ── Helpers used by tests (not MCP tools) ────────────────────────────────────

async def memory_store(agent: str, category: str, content: str, **kwargs) -> str:
    result = await _call("memory_store", agent=agent, category=category,
                         content=content, **kwargs)
    return result if isinstance(result, str) else json.dumps(result)


async def _magma_fuse(
    query: str,
    graph_results: dict,
    top_k: int = 15,
) -> dict:
    """Cross-graph fusion — pure computation, no server call needed."""
    all_memories: dict = {}
    for view_name, memories in graph_results.items():
        for mem in memories:
            mid = mem.get("id")
            if mid is None:
                continue
            if mid in all_memories:
                all_memories[mid]["score"] = max(all_memories[mid]["score"], mem.get("score", 0.5))
                all_memories[mid]["sources"].append(view_name)
            else:
                all_memories[mid] = {**mem, "sources": [view_name]}
    for mem in all_memories.values():
        n = len(mem["sources"])
        mem["cross_graph_boost"] = 1.0 + 0.15 * (n - 1)
        mem["fused_score"] = round(mem["score"] * mem["cross_graph_boost"], 4)
    ranked = sorted(all_memories.values(), key=lambda m: -m["fused_score"])
    context_lines = [
        f"[{'+'.join(m['sources'])}] {(m.get('content') or '')[:300]}"
        for m in ranked[:top_k]
    ]
    return {
        "unified_context": "\n".join(context_lines),
        "source_memories": ranked,
        "per_graph_stats": {v: len(ms) for v, ms in graph_results.items()},
    }


async def _erl_promote_sweep(agent: str) -> dict:
    """Promote high-confidence ERL heuristics to instincts via soul-v3 DB."""
    pool = await get_pool()
    promoted: list = []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, content, metadata FROM memories "
            "WHERE agent = $1 AND category = 'insight' AND invalid_at IS NULL "
            "AND metadata ? 'tags' AND metadata->'tags' ? 'heuristic' "
            "AND COALESCE((metadata->>'confidence')::float, 0) >= 0.85 "
            "AND COALESCE((metadata->>'activation_count')::int, 0) >= 3 "
            "AND (metadata->'promoted_to_instinct') IS NULL",
            agent,
        )
        for r in rows:
            meta = r["metadata"] or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            await conn.execute(
                "UPDATE memories SET metadata = metadata || $1::jsonb WHERE id = $2",
                json.dumps({"promoted_to_instinct": True}), r["id"],
            )
            promoted.append(r["id"])
    return {"promoted_count": len(promoted), "promoted_ids": promoted}
