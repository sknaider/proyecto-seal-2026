#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
soul_mcp.py - Servidor MCP LOCAL de memoria para el alma nativa de Windows.
============================================================================
Conecta un agente (Claude Code / Codex) que corre EN el device con el alma
COMPLETA instalada localmente (Postgres soul_v3, 105k+ memorias). Todo LOCAL:
- Sin central, sin internet, sin modelo de embeddings.
- Busqueda: full-text BM25 (columna embedding_bm25, tsvector ya poblada en el dump)
  con fallback ILIKE. La busqueda semantica vectorial pura queda para cuando haya
  un embedder local (los indices HNSW ya estan; solo falta embeber el query).

Transporte STDIO: Claude Code lanza este proceso y habla por stdin/stdout. No abre
puertos ni deja un servidor colgado. Config en .mcp.json:
  { "mcpServers": { "soul-memory": {
      "command": "python",
      "args": ["C:/soul-native/soul_mcp.py"],
      "env": { "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/seal_memory",
               "AGENT_NAME": "William" } } } }

Deps: asyncpg, mcp   (pip install asyncpg mcp)
"""
from __future__ import annotations
import os
import json
from datetime import datetime, timezone
from typing import Optional

import asyncpg
from mcp.server.fastmcp import FastMCP

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/seal_memory"
)
AGENT_NAME = os.environ.get("AGENT_NAME", "William")
# MEMORY_SCOPE (granito FABLE+JARVIS 8-jul): que el acceso a memoria del agente sea ENFORCED
# server-side, no override-able por el caller. Valores: all | own | <NombreAgente>.
#   all  -> puede leer memorias de todos (el caller puede narrow dentro de 'all')
#   own  -> SOLO las del propio agente (AGENT_NAME), aunque pida otra
#   <X>  -> SOLO las del agente X
MEMORY_SCOPE = os.environ.get("MEMORY_SCOPE", "own")


def _enforce_scope(requested_agent: str) -> str:
    """Devuelve el filtro de agente REAL a aplicar, respetando MEMORY_SCOPE (no escalable)."""
    if MEMORY_SCOPE == "all":
        return requested_agent or ""      # dentro de 'all' el caller puede afinar, o ver todo
    if MEMORY_SCOPE == "own":
        return AGENT_NAME                  # forzado: solo lo propio
    return MEMORY_SCOPE                    # forzado: solo ese agente

mcp = FastMCP("SOUL-local")
_pool: asyncpg.Pool | None = None


async def _pool_get() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
        # el alma vive en el schema soul_v3
        async with _pool.acquire() as c:
            await c.execute("SET search_path TO soul_v3, public")
    return _pool


def _row_to_dict(r) -> dict:
    return {
        "id": str(r["id"]),
        "agent": r.get("agent"),
        "category": r.get("category"),
        "content": r["content"],
        "importance": r["importance"],
        "scope": r.get("scope"),
        "ts": r["created_at"].isoformat() if r.get("created_at") else None,
    }


@mcp.tool()
async def memory_search(
    query: str,
    agent: str = "",
    limit: int = 10,
    min_importance: int = 1,
) -> list[dict]:
    """Busca en el alma local por texto (full-text BM25 sobre embedding_bm25, con fallback ILIKE).
    agent vacio = todos los agentes (si MEMORY_SCOPE lo permite). Mas importantes/recientes primero."""
    agent = _enforce_scope(agent)   # scope enforced (no escalable por el caller)
    pool = await _pool_get()
    async with pool.acquire() as c:
        # 1) intento BM25 (websearch_to_tsquery sobre la tsvector ya poblada)
        try:
            base = """
                SELECT id, agent, scope, category, content, importance, created_at
                FROM soul_v3.memories
                WHERE embedding_bm25 @@ websearch_to_tsquery('simple', $1)
                  AND importance >= $2 {agent_filter}
                ORDER BY ts_rank(embedding_bm25, websearch_to_tsquery('simple', $1)) DESC,
                         importance DESC, created_at DESC
                LIMIT {lim}
            """
            if agent:
                rows = await c.fetch(base.format(agent_filter="AND agent=$4", lim="$3"),
                                     query, min_importance, limit, agent)
            else:
                rows = await c.fetch(base.format(agent_filter="", lim="$3"),
                                     query, min_importance, limit)
            if rows:
                return [_row_to_dict(r) for r in rows]
        except Exception:
            pass
        # 2) fallback ILIKE (por si el query no matchea el tsquery)
        if agent:
            rows = await c.fetch("""
                SELECT id, agent, scope, category, content, importance, created_at
                FROM soul_v3.memories
                WHERE content ILIKE $1 AND importance >= $2 AND agent=$4
                ORDER BY importance DESC, created_at DESC LIMIT $3
            """, f"%{query}%", min_importance, limit, agent)
        else:
            rows = await c.fetch("""
                SELECT id, agent, scope, category, content, importance, created_at
                FROM soul_v3.memories
                WHERE content ILIKE $1 AND importance >= $2
                ORDER BY importance DESC, created_at DESC LIMIT $3
            """, f"%{query}%", min_importance, limit)
        return [_row_to_dict(r) for r in rows]


@mcp.tool()
async def memory_recent(limit: int = 20, agent: str = "") -> list[dict]:
    """Devuelve las memorias mas recientes del alma local. agent vacio = todos (si el scope lo permite)."""
    agent = _enforce_scope(agent)   # scope enforced (no escalable por el caller)
    pool = await _pool_get()
    async with pool.acquire() as c:
        if agent:
            rows = await c.fetch("""
                SELECT id, agent, scope, category, content, importance, created_at
                FROM soul_v3.memories WHERE agent=$1
                ORDER BY created_at DESC LIMIT $2
            """, agent, limit)
        else:
            rows = await c.fetch("""
                SELECT id, agent, scope, category, content, importance, created_at
                FROM soul_v3.memories ORDER BY created_at DESC LIMIT $1
            """, limit)
        return [_row_to_dict(r) for r in rows]


@mcp.tool()
async def memory_store(
    content: str,
    category: str = "fact",
    importance: int = 5,
    agent: str = "",
    scope: str = "private",
    source: str = "device-agent",
) -> dict:
    """Guarda una memoria nueva en el alma local (soul_v3.memories).
    NOTA: sin embedding (no hay embedder local) -> buscable por texto/BM25, no por vector aun."""
    pool = await _pool_get()
    ag = agent or AGENT_NAME
    async with pool.acquire() as c:
        row = await c.fetchrow("""
            INSERT INTO soul_v3.memories (agent, scope, category, content, importance, source, created_at)
            VALUES ($1,$2,$3,$4,$5,$6, NOW())
            RETURNING id, created_at
        """, ag, scope, category, content, importance, source)
    return {"stored": True, "id": str(row["id"]), "ts": row["created_at"].isoformat()}


@mcp.tool()
async def health() -> dict:
    """Health check del alma local: conteo de memorias y agentes."""
    pool = await _pool_get()
    async with pool.acquire() as c:
        n = await c.fetchval("SELECT count(*) FROM soul_v3.memories")
        agents = await c.fetchval("SELECT count(DISTINCT agent) FROM soul_v3.memories")
    return {
        "status": "ok",
        "memories": n,
        "agents": agents,
        "db": "soul_v3 @ localhost",
        "ts": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
