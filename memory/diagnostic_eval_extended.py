#!/usr/bin/env python3
"""diagnostic_eval_extended.py — Extended eval harness for LatentGraphMem V1.1 ship review.

Built on top of diagnostic_eval.py. Runs MAGMA (champion baseline) and
LATENT_GRAPHMEM (V1.1 challenger) side-by-side on test_set_v1, reporting:

  1. recall@1, recall@5, recall@10 (single retrieval with top_k=10, sliced)
  2. breakdown by query_type (factual/temporal/causal/entity/multi_hop/...)
  3. latency p50/p95/p99 per mode
  4. per-query side-by-side: rank of ground-truth memory in each mode,
     diff highlight when one finds it and the other doesn't
  5. structured JSON output + human-readable summary

Honest-eval contract (L1-L4 safe):
  - test_set_v1 is held-out; never used for training (refused in data.py)
  - runs recall ONLY — no LLM utilization scoring in this pass (that's a
    separate concern and Ollama latency would dominate the report)

Usage:
  python3 diagnostic_eval_extended.py                  # full run, top_k=10
  python3 diagnostic_eval_extended.py --top-k 10 --modes magma,latent
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from diagnostic_eval import (
    DEFAULT_AGENT,
    Mode,
    TestQuery,
    _load_test_set,
    _percentile,
    _results_to_context,
)

RESULTS_DIR = HERE / "diagnostic" / "results"


@dataclass
class ExtendedResult:
    query_id: str
    query_type: str
    mode: str
    retrieved_ids: list[int] = field(default_factory=list)
    expected_ids: list[int] = field(default_factory=list)
    # Rank (1-based) of the FIRST expected id found, or None
    first_hit_rank: int | None = None
    recall_at_1: float = 0.0
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    latency_ms: int = 0
    error: str | None = None


def _recall_at(retrieved: list[int], expected: set[int], k: int) -> float:
    if not expected:
        return 0.0
    top = retrieved[:k]
    return len(expected.intersection(top)) / len(expected)


def _first_hit(retrieved: list[int], expected: set[int]) -> int | None:
    for i, rid in enumerate(retrieved, 1):
        if rid in expected:
            return i
    return None


async def _retrieve_magma(q: TestQuery, top_k: int) -> tuple[list[int], int, str | None]:
    from mcp_server_v3 import magma_retrieve
    t0 = time.perf_counter()
    try:
        raw = await magma_retrieve(agent=DEFAULT_AGENT, query=q.query, top_k=top_k)
        payload = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(payload, dict):
            results = payload.get("memories") or payload.get("results") or []
        elif isinstance(payload, list):
            results = payload
        else:
            results = []
        ids = [int(r["id"]) for r in results if isinstance(r.get("id"), int)][:top_k]
        return ids, int((time.perf_counter() - t0) * 1000), None
    except Exception as e:
        return [], int((time.perf_counter() - t0) * 1000), f"{type(e).__name__}: {e}"


async def _retrieve_latent(q: TestQuery, top_k: int) -> tuple[list[int], int, str | None]:
    from latent_graphmem_serve import retrieve as lg_retrieve
    t0 = time.perf_counter()
    try:
        payload = await lg_retrieve(query=q.query, top_k=top_k, token_budget=1500)
        ids: list[int] = []
        if payload.get("memory_ids"):
            ids = [int(i) for i in payload["memory_ids"] if i is not None][:top_k]
        else:
            nodes = (payload.get("subgraph") or {}).get("nodes") or []
            ids = [int(n["memory_id"]) for n in nodes if n.get("memory_id") is not None][:top_k]
        return ids, int((time.perf_counter() - t0) * 1000), None
    except Exception as e:
        return [], int((time.perf_counter() - t0) * 1000), f"{type(e).__name__}: {e}"


RETRIEVERS = {
    "magma": _retrieve_magma,
    "latent": _retrieve_latent,
}


def _score_result(r: ExtendedResult) -> None:
    expected = set(r.expected_ids)
    r.recall_at_1 = _recall_at(r.retrieved_ids, expected, 1)
    r.recall_at_5 = _recall_at(r.retrieved_ids, expected, 5)
    r.recall_at_10 = _recall_at(r.retrieved_ids, expected, 10)
    r.first_hit_rank = _first_hit(r.retrieved_ids, expected)


def _aggregate(results: list[ExtendedResult], modes: list[str]) -> dict[str, Any]:
    by_mode: dict[str, Any] = {}
    for mode in modes:
        mrs = [r for r in results if r.mode == mode and r.error is None]
        if not mrs:
            continue
        n = len(mrs)
        lats = [r.latency_ms for r in mrs]
        agg = {
            "n": n,
            "recall@1": sum(r.recall_at_1 for r in mrs) / n,
            "recall@5": sum(r.recall_at_5 for r in mrs) / n,
            "recall@10": sum(r.recall_at_10 for r in mrs) / n,
            "hit_rate@10": sum(1 for r in mrs if r.first_hit_rank is not None) / n,
            "mrr@10": sum(
                1.0 / r.first_hit_rank for r in mrs if r.first_hit_rank is not None
            ) / n,
            "latency_p50_ms": _percentile(lats, 50),
            "latency_p95_ms": _percentile(lats, 95),
            "latency_p99_ms": _percentile(lats, 99),
            "errors": sum(1 for r in results if r.mode == mode and r.error is not None),
        }
        # Breakdown by query_type
        by_type: dict[str, dict[str, float]] = {}
        groups: dict[str, list[ExtendedResult]] = defaultdict(list)
        for r in mrs:
            groups[r.query_type or "unknown"].append(r)
        for qtype, grs in groups.items():
            gn = len(grs)
            by_type[qtype] = {
                "n": gn,
                "recall@5": sum(r.recall_at_5 for r in grs) / gn,
                "recall@10": sum(r.recall_at_10 for r in grs) / gn,
            }
        agg["by_query_type"] = by_type
        by_mode[mode] = agg
    return by_mode


def _side_by_side(results: list[ExtendedResult]) -> list[dict[str, Any]]:
    """Per-query diff: magma rank vs latent rank of first expected id."""
    by_q: dict[str, dict[str, ExtendedResult]] = defaultdict(dict)
    for r in results:
        by_q[r.query_id][r.mode] = r
    diffs: list[dict[str, Any]] = []
    for qid, entry in by_q.items():
        m = entry.get("magma")
        l = entry.get("latent")
        if not (m and l):
            continue
        m_rank = m.first_hit_rank
        l_rank = l.first_hit_rank
        # Highlight flag: one finds it, the other doesn't, or large rank delta
        highlight = False
        if (m_rank is None) != (l_rank is None):
            highlight = True
        elif m_rank is not None and l_rank is not None and abs(m_rank - l_rank) >= 3:
            highlight = True
        diffs.append({
            "query_id": qid,
            "query_type": m.query_type,
            "magma_rank": m_rank,
            "latent_rank": l_rank,
            "magma_recall@5": m.recall_at_5,
            "latent_recall@5": l.recall_at_5,
            "highlight": highlight,
        })
    return diffs


async def run(top_k: int = 10, modes: list[str] | None = None) -> dict[str, Any]:
    modes = modes or ["magma", "latent"]
    for mname in modes:
        if mname not in RETRIEVERS:
            raise ValueError(f"unknown mode {mname!r}; valid: {list(RETRIEVERS)}")

    queries = _load_test_set()
    if not queries:
        return {"status": "blocked", "reason": "test_set_v1 not found"}

    print(f"[eval] {len(queries)} queries × {len(modes)} modes, top_k={top_k}")
    results: list[ExtendedResult] = []
    for i, q in enumerate(queries, 1):
        expected = [int(x) for x in (q.expected_memory_ids or [])]
        for mname in modes:
            ids, lat_ms, err = await RETRIEVERS[mname](q, top_k)
            r = ExtendedResult(
                query_id=q.query_id,
                query_type=q.query_type or "unknown",
                mode=mname,
                retrieved_ids=ids,
                expected_ids=expected,
                latency_ms=lat_ms,
                error=err,
            )
            if not err:
                _score_result(r)
            results.append(r)
        if i % 10 == 0:
            print(f"  [progress] {i}/{len(queries)}")

    by_mode = _aggregate(results, modes)
    diffs = _side_by_side(results)
    wins_latent = sum(
        1 for d in diffs
        if d["latent_rank"] is not None and (d["magma_rank"] is None or d["latent_rank"] < d["magma_rank"])
    )
    wins_magma = sum(
        1 for d in diffs
        if d["magma_rank"] is not None and (d["latent_rank"] is None or d["magma_rank"] < d["latent_rank"])
    )
    ties = len(diffs) - wins_latent - wins_magma

    report = {
        "queries_total": len(queries),
        "top_k": top_k,
        "modes": modes,
        "by_mode": by_mode,
        "head_to_head": {
            "latent_wins": wins_latent,
            "magma_wins": wins_magma,
            "ties": ties,
            "n": len(diffs),
        },
        "side_by_side": diffs,
        "results": [asdict(r) for r in results],
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "latent_v11_extended.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    _print_summary(report)
    print(f"\n[write] {out}")
    return report


def _print_summary(report: dict[str, Any]) -> None:
    print("\n" + "=" * 72)
    print("LatentGraphMem V1.1 — Extended Eval Summary")
    print("=" * 72)
    for mode, agg in report["by_mode"].items():
        print(f"\n[{mode}]  n={agg['n']}  errors={agg['errors']}")
        print(f"  recall@1  = {agg['recall@1']:.4f}")
        print(f"  recall@5  = {agg['recall@5']:.4f}")
        print(f"  recall@10 = {agg['recall@10']:.4f}")
        print(f"  mrr@10    = {agg['mrr@10']:.4f}")
        print(f"  hit@10    = {agg['hit_rate@10']:.4f}")
        print(f"  latency   p50={agg['latency_p50_ms']:.0f}ms  p95={agg['latency_p95_ms']:.0f}ms  p99={agg['latency_p99_ms']:.0f}ms")
        print(f"  by query_type:")
        for qt, v in sorted(agg["by_query_type"].items()):
            print(f"    {qt:<12} n={v['n']:<3}  r@5={v['recall@5']:.3f}  r@10={v['recall@10']:.3f}")
    h2h = report["head_to_head"]
    print(f"\n[head_to_head]  latent_wins={h2h['latent_wins']}  magma_wins={h2h['magma_wins']}  ties={h2h['ties']}  (n={h2h['n']})")
    highlights = [d for d in report["side_by_side"] if d["highlight"]]
    if highlights:
        print(f"\n[highlights] {len(highlights)} queries with rank gap ≥3 or one-sided hit:")
        for d in highlights[:10]:
            print(f"  {d['query_id']:<20} [{d['query_type']:<10}]  magma={d['magma_rank']}  latent={d['latent_rank']}")
    print("=" * 72)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--modes", type=str, default="magma,latent",
                    help="comma-separated list from {magma,latent}")
    args = ap.parse_args()
    mlist = [m.strip() for m in args.modes.split(",") if m.strip()]
    asyncio.run(run(top_k=args.top_k, modes=mlist))
