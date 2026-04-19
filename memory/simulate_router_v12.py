#!/usr/bin/env python3
"""simulate_router_v12.py — router simulation over 64 test queries (V1.2).

Hybrid approach to avoid dep-hell: we reuse magma results from the existing
`latent_v11_extended.json` file (magma code unchanged → same ids, same latency)
and re-run only LATENT in the V1.2 serving path (GPU + asyncio.gather BFS).

For each query:
  1. classifier.classify() → qtype
  2. route(qtype) → "latent" or "magma"
  3. pick ids+latency from corresponding backend run
  4. compute recall@5

Reports aggregate + latent/magma bucket breakdown.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import httpx

import latent_graphmem_serve as L
from latent_graphmem.query_classifier import classify, route

TEST_SET = HERE / "diagnostic" / "test_set_v1.jsonl"
V11_EXT = HERE / "diagnostic" / "results" / "latent_v11_extended.json"
OUT = HERE / "diagnostic" / "results" / "latent_v12_router_simulated.json"


def pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


def recall_at(retrieved: list[int], expected: set[int], k: int) -> float:
    if not expected:
        return 0.0
    return len(expected.intersection(retrieved[:k])) / len(expected)


async def _latent_run(query: str, top_k: int = 10) -> tuple[list[int], float]:
    t0 = time.perf_counter()
    payload = await L.retrieve(query=query, top_k=top_k, token_budget=1500)
    lat = (time.perf_counter() - t0) * 1000
    ids: list[int] = []
    if payload.get("memory_ids"):
        ids = [int(i) for i in payload["memory_ids"] if i is not None][:top_k]
    else:
        nodes = (payload.get("subgraph") or {}).get("nodes") or []
        ids = [int(n["memory_id"]) for n in nodes if n.get("memory_id") is not None][:top_k]
    return ids, lat


async def main() -> int:
    queries = []
    with TEST_SET.open() as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                queries.append(json.loads(line))
    print(f"[load] {len(queries)} queries")

    # Magma results from V1.1 extended (magma code unchanged between V1.1 & V1.2)
    v11 = json.load(V11_EXT.open())
    magma_by_qid: dict[str, dict] = {}
    for r in v11["results"]:
        if r["mode"] == "magma" and not r.get("error"):
            magma_by_qid[r["query_id"]] = r
    print(f"[load] magma rows: {len(magma_by_qid)}")

    # Warm up latent
    print("[warmup] latent V1.2")
    await L._load_model()
    await _latent_run(queries[0]["query"])

    # Run latent for all queries under V1.2 path
    print("[run] latent V1.2 over 64 queries")
    latent_by_qid: dict[str, dict] = {}
    for i, q in enumerate(queries, 1):
        ids, lat = await _latent_run(q["query"])
        latent_by_qid[q["id"]] = {"ids": ids, "latency_ms": lat}
        if i % 16 == 0:
            print(f"  [{i}/{len(queries)}]")

    # Simulate router
    print("[route] classifying + routing")
    rows: list[dict] = []
    async with httpx.AsyncClient() as http:
        for q in queries:
            qid = q["id"]
            expected = set(int(x) for x in (q.get("expected_memory_ids") or []))
            qtype_pred = await classify(q["query"], http)
            backend = route(qtype_pred)
            if backend == "latent":
                rec = latent_by_qid[qid]
                ids = rec["ids"]
                lat = rec["latency_ms"]
            else:
                mrow = magma_by_qid.get(qid)
                if mrow is None:
                    continue
                ids = mrow["retrieved_ids"]
                lat = float(mrow["latency_ms"])
            r5 = recall_at(ids, expected, 5)
            rows.append({
                "qid": qid,
                "true_type": q.get("query_type"),
                "pred_type": qtype_pred,
                "backend": backend,
                "latency_ms": lat,
                "recall@5": r5,
                "retrieved_ids": ids,
                "expected_ids": sorted(expected),
            })

    # Aggregates
    all_lat = [r["latency_ms"] for r in rows]
    all_r5 = [r["recall@5"] for r in rows]
    lat_rows = [r for r in rows if r["backend"] == "latent"]
    mag_rows = [r for r in rows if r["backend"] == "magma"]

    def bucket(rs: list[dict]) -> dict:
        if not rs:
            return {"n": 0}
        lats = [r["latency_ms"] for r in rs]
        return {
            "n": len(rs),
            "recall@5_mean": statistics.mean(r["recall@5"] for r in rs),
            "latency_p50_ms": pct(lats, 50),
            "latency_p95_ms": pct(lats, 95),
            "latency_mean_ms": statistics.mean(lats),
        }

    # Baseline comparisons from V1.1 extended (magma-only and latent-only, no router)
    baseline_magma = {
        "recall@5": v11["by_mode"]["magma"]["recall@5"],
        "latency_p50_ms": v11["by_mode"]["magma"]["latency_p50_ms"],
        "latency_p95_ms": v11["by_mode"]["magma"]["latency_p95_ms"],
    }
    # V1.2 latent-only (measured live this run)
    v12_latent_lats = [latent_by_qid[q["id"]]["latency_ms"] for q in queries]
    v12_latent_r5 = [
        recall_at(
            latent_by_qid[q["id"]]["ids"],
            set(int(x) for x in (q.get("expected_memory_ids") or [])),
            5,
        )
        for q in queries
    ]
    baseline_latent_v12 = {
        "recall@5": statistics.mean(v12_latent_r5),
        "latency_p50_ms": pct(v12_latent_lats, 50),
        "latency_p95_ms": pct(v12_latent_lats, 95),
    }

    report = {
        "n_queries": len(rows),
        "router_aggregate": {
            "recall@5_mean": statistics.mean(all_r5),
            "latency_p50_ms": pct(all_lat, 50),
            "latency_p95_ms": pct(all_lat, 95),
            "latency_p99_ms": pct(all_lat, 99),
            "latency_mean_ms": statistics.mean(all_lat),
            "latency_max_ms": max(all_lat),
        },
        "latent_bucket": bucket(lat_rows),
        "magma_bucket": bucket(mag_rows),
        "baseline_magma_only": baseline_magma,
        "baseline_latent_v12_only": baseline_latent_v12,
        "rows": rows,
    }

    a = report["router_aggregate"]
    print("\n" + "=" * 64)
    print("ROUTER SIMULATION V1.2 — aggregate (64 queries)")
    print("=" * 64)
    print(f"  recall@5 mean  = {a['recall@5_mean']:.4f}")
    print(f"  latency p50    = {a['latency_p50_ms']:.1f} ms")
    print(f"  latency p95    = {a['latency_p95_ms']:.1f} ms")
    print(f"  latency p99    = {a['latency_p99_ms']:.1f} ms")
    print(f"  latency mean   = {a['latency_mean_ms']:.1f} ms")
    print("\n[breakdown]")
    for name, b in [("latent", report["latent_bucket"]), ("magma", report["magma_bucket"])]:
        if b["n"] == 0:
            print(f"  {name}: n=0")
            continue
        print(f"  {name}: n={b['n']:2d}  r@5={b['recall@5_mean']:.4f}  "
              f"p50={b['latency_p50_ms']:7.1f}ms  p95={b['latency_p95_ms']:7.1f}ms")

    print("\n[baselines]")
    print(f"  magma-only:       r@5={baseline_magma['recall@5']:.4f}  "
          f"p50={baseline_magma['latency_p50_ms']:.1f}ms  p95={baseline_magma['latency_p95_ms']:.1f}ms")
    print(f"  latent-only V1.2: r@5={baseline_latent_v12['recall@5']:.4f}  "
          f"p50={baseline_latent_v12['latency_p50_ms']:.1f}ms  p95={baseline_latent_v12['latency_p95_ms']:.1f}ms")

    delta_r5 = a['recall@5_mean'] - baseline_magma['recall@5']
    delta_p50 = a['latency_p50_ms'] - baseline_magma['latency_p50_ms']
    print(f"\n[router vs magma-only]  Δr@5 = {delta_r5:+.4f}  Δp50 = {delta_p50:+.1f}ms")

    zeros = [r for r in rows if r["recall@5"] == 0.0]
    print(f"\n[zero-recall] {len(zeros)}/{len(rows)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n[write] {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
