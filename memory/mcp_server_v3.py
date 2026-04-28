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
