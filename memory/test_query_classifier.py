"""test_query_classifier.py — accuracy sanity check on test_set_v1 (64 queries)."""
from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import httpx

from latent_graphmem.query_classifier import classify, route

TEST_SET = HERE / "diagnostic" / "test_set_v1.jsonl"


async def main() -> int:
    queries = []
    with TEST_SET.open() as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                queries.append(json.loads(line))
    print(f"[load] {len(queries)} queries")

    correct = 0
    errors: list[tuple[str, str, str, str]] = []
    conf_matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    route_correct_latent = 0
    route_total_latent = 0
    route_correct_magma = 0
    route_total_magma = 0

    LATENT = {"causal", "temporal", "multi_hop", "inference"}

    async with httpx.AsyncClient() as client:
        for q in queries:
            qid = q["id"]
            true_type = q["query_type"]
            pred_type = await classify(q["query"], client)
            conf_matrix[true_type][pred_type] += 1
            if pred_type == true_type:
                correct += 1
            else:
                errors.append((qid, q["query"][:60], true_type, pred_type))
            # Route-level accuracy: what matters is whether we picked right backend
            true_backend = "latent" if true_type in LATENT else "magma"
            pred_backend = route(pred_type)
            if true_backend == "latent":
                route_total_latent += 1
                if pred_backend == "latent":
                    route_correct_latent += 1
            else:
                route_total_magma += 1
                if pred_backend == "magma":
                    route_correct_magma += 1

    n = len(queries)
    print(f"\n[type accuracy]   {correct}/{n} = {correct/n:.3f}")
    print(f"[route accuracy]  latent: {route_correct_latent}/{route_total_latent} = "
          f"{route_correct_latent/max(1,route_total_latent):.3f}")
    print(f"[route accuracy]  magma:  {route_correct_magma}/{route_total_magma} = "
          f"{route_correct_magma/max(1,route_total_magma):.3f}")
    overall_route = (route_correct_latent + route_correct_magma) / n
    print(f"[route accuracy]  overall: {overall_route:.3f}")

    print("\n[confusion matrix] (rows=true, cols=pred)")
    types = sorted({t for t in conf_matrix} | {t for d in conf_matrix.values() for t in d})
    header = "true\\pred   " + " ".join(f"{t[:8]:>8}" for t in types)
    print(header)
    for t in types:
        row = f"{t[:10]:<10}" + " ".join(f"{conf_matrix[t].get(p,0):>8}" for p in types)
        print(row)

    if errors:
        print(f"\n[errors] {len(errors)}")
        for qid, q, tt, pt in errors[:20]:
            print(f"  {qid:<6} true={tt:<10} pred={pt:<10}  {q}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
