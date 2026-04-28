"""mcp_server_v3.py — Test adapter for soul-v3 MCP server (port 8771).
Provides direct-callable async functions. Not a monolith — delegates to
the live soul-v3 server via MCP client protocol.
"""
from __future__ import annotations
import collections
import json
import re
import time
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client

_START_TIME = time.time()

_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(
            "intfloat/multilingual-e5-base",
            device="cpu",
            cache_folder="/home/dadito/IA/cache/huggingface",
        )
    return _embedder


async def _erl_call_ollama(prompt: str, timeout: float = 30.0) -> Optional[str]:
    """Calls Ollama generate API. Module-level so tests can replace it."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                "http://localhost:11434/api/generate",
                json={"model": "qwen2.5:7b", "prompt": prompt, "stream": False,
                      "options": {"temperature": 0.4, "num_predict": 400}},
            )
            return resp.json().get("response", "").strip()
    except Exception:
        return None


def _build_reflect_prompt(task_description, outcome, trajectory, context, max_heuristics):
    ctx_block = f"\nContext:\n{context}" if context else ""
    return (
        f"You are an AI agent doing post-task reflection to extract transferable heuristics.\n\n"
        f"Task: {task_description}\nOutcome: {outcome}\n"
        f"Trajectory (steps/errors/decisions):\n{trajectory}{ctx_block}\n\n"
        f"Extract up to {max_heuristics} transferable heuristics as JSON array. "
        f"Each heuristic: {{\"title\": \"short title\", \"lesson\": \"what to do/avoid\", "
        f"\"confidence\": 0.0-1.0}}. "
        f"Focus on non-obvious insights that generalize beyond this specific task. "
        f"Respond ONLY with valid JSON array, no explanation."
    )

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
    agent: str = "",
    period: str = "day",
    date_str: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
) -> str:
    result = await _call(
        "temporal_summary_get",
        agent=agent, period=period, date_str=date_str, year=year, month=month,
    )
    return result if isinstance(result, str) else json.dumps(result)


async def temporal_query(
    agent: str = "",
    query: str = "",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    strategy: str = "local",
    top_k: int = 5,
) -> str:
    result = await _call(
        "temporal_query",
        agent=agent, query=query, start_date=start_date,
        end_date=end_date, strategy=strategy, top_k=top_k,
    )
    return result if isinstance(result, str) else json.dumps(result)


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
    if not fuse and isinstance(result, dict):
        result["memories"] = result.pop("results", {})
        result.setdefault("context", "")
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
    if outcome not in ("success", "failure", "partial"):
        outcome = "partial"
    max_heuristics = max(1, min(5, max_heuristics))

    prompt = _build_reflect_prompt(task_description, outcome, trajectory, context, max_heuristics)
    raw = await _erl_call_ollama(prompt)

    def _parse_json_array(text: str) -> Optional[list]:
        if not text:
            return None
        start = text.find("[")
        end = text.rfind("]") + 1
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start:end])
        except Exception:
            return None

    heuristics = _parse_json_array(raw)
    if heuristics is None:
        raw2 = await _erl_call_ollama(prompt + "\n\nIMPORTANT: Respond ONLY with JSON array.")
        heuristics = _parse_json_array(raw2) or []

    if not heuristics:
        return json.dumps({"heuristics_generated": 0, "error": "malformed_json", "ids": []})

    stored_ids = []
    for h in heuristics[:max_heuristics]:
        if not isinstance(h, dict):
            continue
        lesson = h.get("lesson") or h.get("heuristic")
        if not lesson:
            continue
        title = h.get("title", "")
        confidence = float(h.get("confidence", 0.7))
        importance = min(9, max(5, int(confidence * 10)))
        content = f"[ERL {outcome.upper()}] {title}: {lesson}" if title else f"[ERL {outcome.upper()}] {lesson}"
        res = await memory_store(
            agent=agent,
            category="insight",
            content=content,
            importance=importance,
            source="erl_reflect",
            metadata=json.dumps({
                "tags": ["heuristic", "erl", outcome],
                "outcome": outcome,
                "task": task_description[:100],
                "confidence": confidence,
                "activation_count": 0,
            }),
        )
        m = re.search(r"#?(\d+)", res or "")
        if m:
            stored_ids.append(int(m.group(1)))

    return json.dumps({
        "heuristics_generated": len(stored_ids),
        "ids": stored_ids,
        "outcome": outcome,
    })


async def erl_inject(
    agent: str,
    task_description: str,
    top_k: int = 3,
    min_confidence: float = 0.5,
    min_activation_count: int = 0,
) -> str:
    query_emb = _get_embedder().encode(task_description, normalize_embeddings=True).tolist()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, content, importance, metadata, created_at, "
            "1 - (embedding <=> $1::vector) AS score "
            "FROM memories "
            "WHERE agent = $2 AND category = 'insight' AND invalid_at IS NULL "
            "AND metadata->>'tags' LIKE '%heuristic%' "
            "AND COALESCE((metadata->>'confidence')::float, 0) >= $3 "
            "ORDER BY embedding <=> $1::vector LIMIT $4",
            json.dumps(query_emb), agent, min_confidence, top_k,
        )

        heuristics = []
        for r in rows:
            meta = r["metadata"] or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            old_count = int(meta.get("activation_count", 0))
            new_count = old_count + 1
            new_meta = {**meta, "activation_count": new_count}
            await conn.execute(
                "UPDATE memories SET metadata = $1::jsonb WHERE id = $2",
                json.dumps(new_meta), r["id"],
            )
            heuristics.append({
                "id": r["id"],
                "heuristic": r["content"] or "",
                "score": round(float(r["score"]), 4),
                "confidence": float(meta.get("confidence", 0.7)),
                "activation_count": new_count,
                "outcome": meta.get("outcome", "unknown"),
            })

    context = "Lecciones aprendidas:\n" + "\n".join(f"• {h['heuristic']}" for h in heuristics)
    if not heuristics:
        context = ""

    return json.dumps({
        "heuristics": heuristics,
        "count": len(heuristics),
        "formatted_context": context,
    })


# ── Helpers used by tests (not MCP tools) ────────────────────────────────────

async def memory_store(agent: str, category: str, content: str, **kwargs) -> str:
    """Direct DB insert — bypasses MCP protocol JSON coercion issues with metadata."""
    importance = int(kwargs.get("importance", 5))
    scope = kwargs.get("scope", "private")
    source_tier = kwargs.get("source", kwargs.get("source_tier", "ltm"))
    raw_meta = kwargs.get("metadata")
    if raw_meta is None:
        meta_dict = {}
    elif isinstance(raw_meta, str):
        try:
            meta_dict = json.loads(raw_meta)
        except Exception:
            meta_dict = {}
    else:
        meta_dict = raw_meta

    emb = _get_embedder().encode(content, normalize_embeddings=True).tolist()
    pool = await get_pool()
    async with pool.acquire() as conn:
        mem_id = await conn.fetchval(
            "INSERT INTO memories (agent, scope, category, content, importance, "
            "embedding, embedding_bm25, source_tier, metadata) "
            "VALUES ($1, $2, $3, $4, $5, $6::vector, to_tsvector('simple', $4), $7, $8) "
            "RETURNING id",
            agent, scope, category, content, importance,
            json.dumps(emb), source_tier, json.dumps(meta_dict),
        )
    return f"Memory #{mem_id} stored"


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
    return {"promoted": len(promoted), "promoted_ids": promoted}


# ── Rate limiting ─────────────────────────────────────────────────────────────

_RATE_LIMIT_DEFAULT: int = 60
_RATE_LIMITS_OVERRIDE: dict = {
    "connectome_build": 5,
    "temporal_graph_build": 3,
    "cold_archive_migrate": 10,
    "memory_broadcast_read": 30,
}
_rate_windows: dict = collections.defaultdict(collections.deque)


def _rate_check(tool_name: str, window_seconds: float = 60.0) -> tuple:
    """Sliding window rate check. Returns (allowed: bool, remaining: int)."""
    now = time.time()
    limit = _RATE_LIMITS_OVERRIDE.get(tool_name, _RATE_LIMIT_DEFAULT)
    dq = _rate_windows[tool_name]
    while dq and now - dq[0] > window_seconds:
        dq.popleft()
    if len(dq) < limit:
        dq.append(now)
        return True, limit - len(dq)
    return False, 0


# ── Health check ──────────────────────────────────────────────────────────────

async def health_check() -> str:
    uptime = round(time.time() - _START_TIME, 2)

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        pg_status = {"status": "ok"}
    except Exception as e:
        pg_status = {"status": "error", "error": str(e)[:100]}

    try:
        from neo4j import AsyncGraphDatabase
        _drv = AsyncGraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "seal2026soul"))
        async with _drv.session() as _s:
            await _s.run("RETURN 1")
        await _drv.close()
        neo4j_status = {"status": "ok"}
    except Exception as e:
        neo4j_status = {"status": "degraded", "error": str(e)[:80]}

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get("http://localhost:6333/collections")
            qdrant_status = {"status": "ok" if r.status_code < 400 else "degraded"}
    except Exception as e:
        qdrant_status = {"status": "degraded", "error": str(e)[:80]}

    overall = "ok" if pg_status["status"] == "ok" else "degraded"
    return json.dumps({
        "status": overall,
        "uptime_seconds": uptime,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": {"postgresql": pg_status, "neo4j": neo4j_status, "qdrant": qdrant_status},
    })


# ── Graceful shutdown stubs ───────────────────────────────────────────────────

async def _async_cleanup() -> None:
    global _pool
    if _pool is not None and not _pool._closed:
        await _pool.close()
        _pool = None


def _sync_cleanup() -> None:
    pass


def _signal_handler(signum, frame) -> None:
    pass


# ── Proxy: belief_query, boot_context ────────────────────────────────────────

async def belief_query(agent: str, topic: Optional[str] = None, status: str = "active",
                       limit: int = 20) -> str:
    result = await _call("belief_query", agent=agent, topic=topic, status=status, limit=limit)
    return result if isinstance(result, str) else json.dumps(result)


async def boot_context(agent: str) -> str:
    result = await _call("boot_context", agent=agent)
    return result if isinstance(result, str) else json.dumps(result)


# ── Neo4j accessor ────────────────────────────────────────────────────────────

def get_neo4j():
    from neo4j import AsyncGraphDatabase
    return AsyncGraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "seal2026soul"))


# ── Proxy: memory_search ──────────────────────────────────────────────────────

async def memory_search(query: str, agent: str = "", limit: int = 10,
                        memory_type: Optional[str] = None,
                        include_archived: bool = False) -> str:
    result = await _call("memory_search", agent=agent, query=query, limit=limit,
                         memory_type=memory_type, include_archived=include_archived)
    return result if isinstance(result, str) else json.dumps(result)


# ── Cold archive ─────────────────────────────────────────────────────────────

async def _cold_archive_migrate(pool, agent: str, min_age_days: int = 90,
                                 ttl_days: int = 365, dry_run: bool = True) -> dict:
    cutoff_interval = f"{min_age_days} days"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, content, category, importance FROM memories "
            "WHERE agent = $1 AND invalid_at IS NOT NULL "
            "AND invalid_at < NOW() - $2::interval",
            agent, cutoff_interval,
        )
        if dry_run:
            return {"archived": len(rows), "dry_run": True, "singletons": 0}
        count = 0
        for r in rows:
            await conn.execute(
                "INSERT INTO cold_archive (agent, original_memory_ids, summary, "
                "source_count, importance_max, category, expires_at) "
                "VALUES ($1, $2, $3, 1, $4, $5, NOW() + $6::interval)",
                agent, [r["id"]], r["content"][:500], r["importance"],
                r["category"], f"{ttl_days} days",
            )
            await conn.execute("DELETE FROM memories WHERE id = $1", r["id"])
            count += 1
        return {"archived": count, "dry_run": False, "singletons": 0}


async def cold_archive_query(query: str, agent: Optional[str] = None,
                              limit: int = 10) -> str:
    query_emb = _get_embedder().encode(query, normalize_embeddings=True).tolist()
    pool = await get_pool()
    async with pool.acquire() as conn:
        if agent:
            rows = await conn.fetch(
                "SELECT id, agent, summary, category, importance_max "
                "FROM cold_archive WHERE agent = $1 AND embedding IS NOT NULL "
                "ORDER BY embedding <=> $2::vector LIMIT $3",
                agent, json.dumps(query_emb), limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, agent, summary, category, importance_max "
                "FROM cold_archive WHERE embedding IS NOT NULL "
                "ORDER BY embedding <=> $1::vector LIMIT $2",
                json.dumps(query_emb), limit,
            )
    results = [{"id": r["id"], "agent": r["agent"], "content": r["summary"],
                "category": r["category"], "source": "cold_archive"} for r in rows]
    return json.dumps({"results": results, "count": len(results)})


async def cold_archive_stats() -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM cold_archive") or 0
        rows = await conn.fetch(
            "SELECT agent, count(*) AS cnt FROM cold_archive GROUP BY agent ORDER BY cnt DESC"
        )
    agents = {r["agent"]: int(r["cnt"]) for r in rows}
    return json.dumps({"total": total, "agents": agents})


async def _cold_archive_purge_expired(pool, dry_run: bool = True) -> dict:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM cold_archive WHERE expires_at IS NOT NULL AND expires_at < NOW()"
        )
        if dry_run:
            return {"purged": len(rows), "dry_run": True}
        for r in rows:
            await conn.execute("DELETE FROM cold_archive WHERE id = $1", r["id"])
        return {"purged": len(rows)}


# ── MIRIX classification ──────────────────────────────────────────────────────

MIRIX_CATEGORY_MAP: dict = {
    "emotion": "core",
    "trust": "core",
    "preference": "core",
    "insight": "semantic",
    "fact": "semantic",
    "pattern": "semantic",
    "decision": "semantic",
    "milestone": "episodic",
    "event": "episodic",
    "correction": "episodic",
    "general": "episodic",
}

_VAULT_PATTERNS = ("api_key=", "password=", "secret=", "token=", "sk-", "Bearer ", "-----BEGIN")


def _mirix_classify(category: str, content: str, memory_type: Optional[str] = None) -> str:
    if memory_type:
        return memory_type
    if any(p in content for p in _VAULT_PATTERNS):
        return "vault"
    return MIRIX_CATEGORY_MAP.get(category, "episodic")
