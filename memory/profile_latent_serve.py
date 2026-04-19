#!/usr/bin/env python3
"""profile_latent_serve.py — empirical breakdown of retrieve() latency.

Instruments seeds / BFS loop / rerank / fuse on 5 test_set queries.
Goal: verify whether BFS (sequential, per-seed Neo4j session) or encode
dominates, so we optimize the right thing.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import latent_graphmem_serve as L

TEST_SET = HERE / "diagnostic" / "test_set_v1.jsonl"


async def profile_one(query: str) -> dict:
    stages: dict[str, float] = {}
    t0 = time.perf_counter()
    seeds = await L._candidate_seeds(query, L.CANDIDATE_POOL_M)
    stages["seeds_ms"] = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    bfs_results = await asyncio.gather(*[L._bfs(s) for s in seeds])
    subgraphs = [sg for sg in bfs_results if sg.get("nodes")]
    stages["bfs_total_ms"] = (time.perf_counter() - t1) * 1000
    stages["bfs_n"] = len(bfs_results)
    stages["bfs_avg_ms"] = stages["bfs_total_ms"] / max(1, len(bfs_results))

    t2 = time.perf_counter()
    scores = await L._rerank(query, subgraphs) if subgraphs else []
    stages["rerank_ms"] = (time.perf_counter() - t2) * 1000

    t3 = time.perf_counter()
    if subgraphs:
        await L._fuse(subgraphs, scores, 10, 1500)
    stages["fuse_ms"] = (time.perf_counter() - t3) * 1000

    stages["total_ms"] = (time.perf_counter() - t0) * 1000
    return stages


async def main() -> int:
    queries = []
    with TEST_SET.open() as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                queries.append(json.loads(line))
    sample = queries[:5]

    print(f"[profile] warming model…")
    await L._load_model()
    await profile_one(sample[0]["query"])  # warmup

    results = []
    for q in sample:
        r = await profile_one(q["query"])
        r["qid"] = q["id"]
        results.append(r)
        print(
            f"  {q['id']:<6} total={r['total_ms']:7.1f}ms  "
            f"seeds={r['seeds_ms']:6.1f}  "
            f"bfs={r['bfs_total_ms']:7.1f} (n={r['bfs_n']}, avg={r['bfs_avg_ms']:5.1f})  "
            f"rerank={r['rerank_ms']:6.1f}  fuse={r['fuse_ms']:5.1f}"
        )

    def avg(k):
        return sum(r[k] for r in results) / len(results)

    print("\n[averages]")
    print(f"  total      = {avg('total_ms'):.1f} ms")
    print(f"  seeds      = {avg('seeds_ms'):.1f} ms ({avg('seeds_ms')/avg('total_ms')*100:.1f}%)")
    print(f"  bfs_total  = {avg('bfs_total_ms'):.1f} ms ({avg('bfs_total_ms')/avg('total_ms')*100:.1f}%)")
    print(f"  rerank     = {avg('rerank_ms'):.1f} ms ({avg('rerank_ms')/avg('total_ms')*100:.1f}%)")
    print(f"  fuse       = {avg('fuse_ms'):.1f} ms ({avg('fuse_ms')/avg('total_ms')*100:.1f}%)")
    print(f"  bfs_avg    = {avg('bfs_avg_ms'):.1f} ms per seed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
