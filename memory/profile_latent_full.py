#!/usr/bin/env python3
"""profile_latent_full.py — full 64-query profile for V1.2 close.

Calls retrieve() directly so it measures the production path including
asyncio.gather BFS. Reports p50/p95/p99 + stage averages.
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

import latent_graphmem_serve as L

TEST_SET = HERE / "diagnostic" / "test_set_v1.jsonl"


def pct(xs, p):
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


async def main() -> int:
    queries = []
    with TEST_SET.open() as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                queries.append(json.loads(line))

    print(f"[warmup] loading model + 2 dummy queries")
    await L._load_model()
    for q in queries[:2]:
        await L.retrieve(q["query"], top_k=10, token_budget=1500)

    print(f"[run] {len(queries)} queries")
    totals = []
    errors = 0
    for i, q in enumerate(queries, 1):
        t0 = time.perf_counter()
        try:
            r = await L.retrieve(q["query"], top_k=10, token_budget=1500)
            lat = r.get("latency_ms") or (time.perf_counter() - t0) * 1000
            totals.append(float(lat))
        except Exception as e:
            errors += 1
            print(f"  [err] {q['id']}: {type(e).__name__}: {e}")
        if i % 16 == 0:
            print(f"  [{i}/{len(queries)}]")

    n = len(totals)
    print(f"\n[latency] n={n}  errors={errors}")
    print(f"  p50 = {pct(totals, 50):.1f} ms")
    print(f"  p95 = {pct(totals, 95):.1f} ms")
    print(f"  p99 = {pct(totals, 99):.1f} ms")
    print(f"  mean= {statistics.mean(totals):.1f} ms")
    print(f"  min = {min(totals):.1f} ms")
    print(f"  max = {max(totals):.1f} ms")

    out = HERE / "diagnostic" / "results" / "latent_v12_latency.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "n": n, "errors": errors,
        "p50": pct(totals, 50), "p95": pct(totals, 95), "p99": pct(totals, 99),
        "mean": statistics.mean(totals), "min": min(totals), "max": max(totals),
        "all_latencies_ms": totals,
    }, indent=2))
    print(f"[write] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
