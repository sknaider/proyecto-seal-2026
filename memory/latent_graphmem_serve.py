#!/usr/bin/env python3
"""latent_graphmem_serve.py — Step 4 of LatentGraphMem (Track A).

FastAPI service on :8767 that wraps the LoRA-trained bi-encoder for
subgraph retrieval. Contract defined in arch note.

Retrieval pipeline (different from training):
  1. Receive query
  2. Generate M candidate seeds via embedding similarity (pgvector)
  3. BFS k=2 (capped) from each seed → candidate subgraphs
  4. Re-rank subgraphs with LoRA bi-encoder
  5. Return top_k + fused memory_ids

Circuit breaker: if adapter load fails or inference errors rate-limit,
fall back to magma_retrieve.

Usage:
  python3 latent_graphmem_serve.py         # serve on :8767
  python3 latent_graphmem_serve.py --self-test   # import + health check, no server
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

ADAPTER_DIR = Path(os.path.expanduser(
    os.environ.get("LATENT_ADAPTER_DIR", "~/IA/modelos/latent-graphmem-soul-v1/best")
))
PG_DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "seal2026soul")

DEFAULT_TOP_K = 5
DEFAULT_TOKEN_BUDGET = 2000
CANDIDATE_POOL_M = int(os.environ.get("LATENT_CANDIDATE_POOL_M", "20"))  # M seeds from pgvector
BFS_DEPTH = 2
BFS_MAX_NODES = 32        # same cap as training pairs

# ── Circuit breaker state ──
_FAILURES = 0
_FAIL_THRESHOLD = 5
_RECOVERY_S = 60.0
_TRIPPED_AT = 0.0


def _circuit_open() -> bool:
    global _FAILURES, _TRIPPED_AT
    if _FAILURES >= _FAIL_THRESHOLD:
        if time.time() - _TRIPPED_AT < _RECOVERY_S:
            return True
        _FAILURES = 0
    return False


def _record_failure() -> None:
    global _FAILURES, _TRIPPED_AT
    _FAILURES += 1
    if _FAILURES >= _FAIL_THRESHOLD:
        _TRIPPED_AT = time.time()


def _record_success() -> None:
    global _FAILURES
    _FAILURES = 0


# ── Lazy imports so --self-test works even if fastapi missing ──

_model = None
_pg_pool = None
_neo4j_driver = None


async def _load_model():
    """Load base e5 + LoRA adapter. Returns BiEncoder in eval mode."""
    global _model
    if _model is not None:
        return _model
    import torch
    from latent_graphmem.model import BiEncoder
    from peft import PeftModel
    from transformers import AutoModel, AutoTokenizer

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = BiEncoder(apply_lora=False)  # skip fresh LoRA

    if ADAPTER_DIR.exists():
        # Wrap base with trained adapter
        model.encoder = PeftModel.from_pretrained(model.encoder, str(ADAPTER_DIR))
        print(f"[serve] loaded adapter from {ADAPTER_DIR}")
    else:
        print(f"[serve] WARNING adapter not found at {ADAPTER_DIR}, "
              f"using base e5-base (no LoRA)", file=sys.stderr)

    model.to(device)
    model.eval()
    _model = model
    return _model


async def _pg():
    global _pg_pool
    if _pg_pool is None:
        import asyncpg
        _pg_pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=4)
    return _pg_pool


async def _neo():
    global _neo4j_driver
    if _neo4j_driver is None:
        from neo4j import AsyncGraphDatabase
        _neo4j_driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    return _neo4j_driver


async def _candidate_seeds(query_text: str, m: int) -> list[int]:
    """Top-M memory_ids by embedding similarity to query."""
    from embeddings import get_embedding
    q_emb = await get_embedding(query_text)
    emb_str = "[" + ",".join(f"{x:.6f}" for x in q_emb) + "]"
    pool = await _pg()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM memories "
            "WHERE embedding IS NOT NULL "
            "ORDER BY embedding <=> $1::vector "
            "LIMIT $2",
            emb_str,
            m,
        )
    return [int(r["id"]) for r in rows]


async def _bfs(seed_id: int) -> dict:
    """Cap-limited BFS (same config as training pairs)."""
    from latent_graphmem_build_pairs import _bfs_subgraph  # reuse
    driver = await _neo()
    async with driver.session() as session:
        return await _bfs_subgraph(
            session, seed_id, depth=BFS_DEPTH, max_nodes=BFS_MAX_NODES
        )


async def _rerank(query_text: str, subgraphs: list[dict]) -> list[float]:
    """Score each subgraph with the bi-encoder. Higher = better."""
    import torch
    from latent_graphmem.data import serialize_query, serialize_subgraph
    model = await _load_model()
    device = next(model.parameters()).device
    with torch.no_grad():
        q_emb = model.encode([serialize_query(query_text)], device)  # (1, D)
        sg_texts = [serialize_subgraph(sg) for sg in subgraphs]
        sg_emb = model.encode(sg_texts, device)                       # (N, D)
        sims = (q_emb @ sg_emb.t()).squeeze(0).cpu().tolist()
    return sims


async def _fuse(
    subgraphs: list[dict], scores: list[float], top_k: int, token_budget: int
) -> tuple[dict, list[int], list[float]]:
    """Pick top_k subgraphs, merge nodes/edges, truncate to token_budget."""
    order = sorted(range(len(subgraphs)), key=lambda i: -scores[i])[:top_k]
    merged_nodes: dict[int, dict] = {}
    merged_edges: list[dict] = []
    edge_keys: set[tuple] = set()
    ordered_ids: list[int] = []
    ordered_scores: list[float] = []
    char_budget = token_budget * 4   # approx 4 chars/token

    for idx in order:
        sg = subgraphs[idx]
        sc = scores[idx]
        for n in sg.get("nodes", []):
            mid = n.get("memory_id")
            if mid is None:
                continue
            if mid not in merged_nodes:
                merged_nodes[mid] = {
                    "memory_id": mid,
                    "text": (n.get("content") or "")[:300],
                }
                ordered_ids.append(mid)
                ordered_scores.append(float(sc))
        for e in sg.get("edges", []):
            key = (e.get("src"), e.get("tgt"), e.get("rel"))
            if key not in edge_keys:
                merged_edges.append({
                    "src": e.get("src"),
                    "dst": e.get("tgt"),
                    "rel": e.get("rel"),
                })
                edge_keys.add(key)

    # Truncate node texts to budget
    total = 0
    keep_nodes = []
    for n in merged_nodes.values():
        t = n["text"]
        if total + len(t) > char_budget:
            break
        keep_nodes.append(n)
        total += len(t)

    subgraph = {"nodes": keep_nodes, "edges": merged_edges}
    return subgraph, ordered_ids[: top_k * 4], ordered_scores[: top_k * 4]


async def retrieve(
    query: str, top_k: int = DEFAULT_TOP_K, token_budget: int = DEFAULT_TOKEN_BUDGET
) -> dict:
    """Main entry. Returns the Response dict from the arch note contract."""
    t0 = time.perf_counter()

    if _circuit_open():
        return await _magma_fallback(query, top_k, reason="circuit_open")

    try:
        seeds = await _candidate_seeds(query, CANDIDATE_POOL_M)
        if not seeds:
            _record_failure()
            return await _magma_fallback(query, top_k, reason="no_seeds")

        bfs_results = await asyncio.gather(*[_bfs(s) for s in seeds])
        subgraphs: list[dict] = [sg for sg in bfs_results if sg.get("nodes")]
        if not subgraphs:
            _record_failure()
            return await _magma_fallback(query, top_k, reason="no_subgraphs")

        scores = await _rerank(query, subgraphs)
        fused, ids, fused_scores = await _fuse(subgraphs, scores, top_k, token_budget)
        _record_success()
        return {
            "subgraph": fused,
            "memory_ids": ids,
            "scores": fused_scores,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "adapter_version": "v1",
            "source": "latent_graphmem",
        }
    except Exception as e:
        _record_failure()
        return await _magma_fallback(
            query, top_k, reason=f"{type(e).__name__}: {e}"
        )


async def _magma_fallback(query: str, top_k: int, reason: str) -> dict:
    """Fallback to magma_retrieve via direct call."""
    t0 = time.perf_counter()
    try:
        from mcp_server_v3 import magma_retrieve
        raw = await magma_retrieve(agent="ADA", query=query, top_k=top_k)
        payload = json.loads(raw) if isinstance(raw, str) else raw
        return {
            "subgraph": {"nodes": [], "edges": []},
            "memory_ids": [m.get("id") for m in (payload.get("memories") or []) if m.get("id")],
            "scores": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "adapter_version": "fallback",
            "source": "magma_fallback",
            "fallback_reason": reason,
        }
    except Exception as e:
        return {
            "subgraph": {"nodes": [], "edges": []},
            "memory_ids": [],
            "scores": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "adapter_version": "fallback",
            "source": "error",
            "fallback_reason": f"{reason} | magma_also_failed: {type(e).__name__}",
        }


# ── FastAPI wiring ──

from pydantic import BaseModel as _BaseModel, Field as _Field

class Req(_BaseModel):
    query: str
    top_k: int = _Field(default=DEFAULT_TOP_K, ge=1, le=50)
    token_budget: int = _Field(default=DEFAULT_TOKEN_BUDGET, ge=100, le=20000)


def build_app():
    from fastapi import FastAPI

    @asynccontextmanager
    async def lifespan(app):
        await _pg()
        await _load_model()
        yield
        if _neo4j_driver is not None:
            await _neo4j_driver.close()
        if _pg_pool is not None:
            await _pg_pool.close()

    app = FastAPI(title="LatentGraphMem Serve", version="1.0", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {
            "ok": True,
            "adapter_loaded": _model is not None,
            "adapter_path": str(ADAPTER_DIR),
            "adapter_exists": ADAPTER_DIR.exists(),
            "circuit_open": _circuit_open(),
            "failures": _FAILURES,
        }

    @app.post("/retrieve")
    async def _retrieve(req: Req) -> dict[str, Any]:
        return await retrieve(req.query, req.top_k, req.token_budget)

    return app


async def _self_test() -> int:
    """Import + health dry run, no HTTP server."""
    print("[self-test] adapter dir:", ADAPTER_DIR, "exists:", ADAPTER_DIR.exists())
    print("[self-test] importing model...")
    try:
        # Don't actually load (too slow for a smoke) — just ensure symbols resolve
        from latent_graphmem.model import BiEncoder  # noqa: F401
        from latent_graphmem.data import serialize_query, serialize_subgraph  # noqa: F401
        print("[self-test] imports OK")
    except Exception as e:
        print(f"[self-test] FAIL imports: {e}", file=sys.stderr)
        return 1
    print("[self-test] building app...")
    try:
        app = build_app()
        routes = [r.path for r in app.routes]
        print(f"[self-test] app routes: {routes}")
    except Exception as e:
        print(f"[self-test] FAIL app build: {e}", file=sys.stderr)
        return 1
    print("[self-test] OK")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(asyncio.run(_self_test()))

    import uvicorn
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
