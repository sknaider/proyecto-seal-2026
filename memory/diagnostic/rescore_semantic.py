#!/usr/bin/env python3
"""Re-score usage metric in latest.json via semantic similarity.

Fixes JARVIS audit finding: the token-overlap scorer produces false negatives
when the LLM answers correctly but paraphrases differently than ALICE's
expected_answer. Replaces usage=bool with semantic cosine similarity against
ground_truth_content, which is richer and less sensitive to brevity.

Input:  memory/diagnostic/results/latest.json
Output: memory/diagnostic/results/latest_v2.json
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from embeddings import get_embedding

ROOT = Path(__file__).parent.parent
LATEST = ROOT / "diagnostic" / "results" / "latest.json"
TEST_SET = ROOT / "diagnostic" / "test_set_v1.jsonl"
OUT = ROOT / "diagnostic" / "results" / "latest_v2.json"

USAGE_THRESHOLD = 0.70  # cosine ≥ 0.70 counts as "used the memory"


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def load_test_set() -> dict[str, dict]:
    out = {}
    with TEST_SET.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            out[d["id"]] = d
    return out


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


async def main() -> None:
    report = json.loads(LATEST.read_text())
    test_set = load_test_set()

    results = report["results"]
    print(f"Re-scoring {len(results)} results via semantic similarity...")

    # Cache: embedding of each ground_truth_content (reused across modes)
    gt_cache: dict[str, list[float]] = {}
    changed = 0
    scored = 0

    for rec in results:
        if rec.get("error"):
            continue
        q = test_set.get(rec["query_id"])
        if not q:
            continue
        gt = q.get("ground_truth_content") or q.get("expected_answer", "")
        answer = rec.get("answer") or ""
        if not gt or not answer or answer.strip().lower() in {"no sé", "no se", "no sé."}:
            rec["semantic_score"] = 0.0
            rec["usage_v2"] = False
            rec["usage_old"] = rec.get("usage", False)
            scored += 1
            continue

        if gt not in gt_cache:
            gt_cache[gt] = await get_embedding(gt)
        ans_emb = await get_embedding(answer)
        score = cosine(gt_cache[gt], ans_emb)
        new_usage = score >= USAGE_THRESHOLD

        rec["semantic_score"] = round(score, 4)
        rec["usage_old"] = rec.get("usage", False)
        rec["usage_v2"] = new_usage
        if new_usage != rec["usage_old"]:
            changed += 1
        scored += 1

    print(f"  scored: {scored} | usage flip: {changed}")

    # Re-aggregate using usage_v2
    from collections import defaultdict
    by_mode: dict[str, dict] = defaultdict(lambda: {"n": 0, "recall": 0.0, "usage": 0, "ignorance": 0, "lats": []})
    for rec in results:
        if rec.get("error"):
            continue
        mode = rec["mode"]
        b = by_mode[mode]
        b["n"] += 1
        b["recall"] += rec.get("recall_at_k", 0.0)
        if rec.get("usage_v2"):
            b["usage"] += 1
        if rec.get("recall_at_k", 0.0) > 0 and not rec.get("usage_v2"):
            b["ignorance"] += 1
        b["lats"].append(rec.get("latency_ms", 0))

    new_by_mode = {}
    for mode, b in by_mode.items():
        n = b["n"]
        new_by_mode[mode] = {
            "n": n,
            "recall_at_k": round(b["recall"] / n, 4),
            "usage_rate": round(b["usage"] / n, 4),
            "ignorance_rate": round(b["ignorance"] / n, 4),
            "avg_latency_ms": round(sum(b["lats"]) / n, 1),
            "p50_latency_ms": round(percentile(b["lats"], 50), 1),
            "p95_latency_ms": round(percentile(b["lats"], 95), 1),
        }

    # Diagnosis (same logic as original)
    diagnosis = "insufficient_data"
    if "hybrid_search" in new_by_mode:
        h = new_by_mode["hybrid_search"]
        if h["recall_at_k"] < 0.5:
            diagnosis = "retrieval_bound"
        elif h["usage_rate"] < 0.5 and h["recall_at_k"] >= 0.7:
            diagnosis = "utilization_bound"
        elif h["recall_at_k"] >= 0.7 and h["usage_rate"] >= 0.7:
            diagnosis = "healthy"
        else:
            diagnosis = "mixed"

    report["by_mode_v2"] = new_by_mode
    report["diagnosis_v2"] = diagnosis
    report["rescore_meta"] = {
        "method": "semantic_cosine_vs_ground_truth_content",
        "threshold": USAGE_THRESHOLD,
        "flipped": changed,
        "scored": scored,
    }

    OUT.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n✅ Wrote {OUT}")
    print(f"\nDiagnosis v2: {diagnosis}")
    for mode, stats in new_by_mode.items():
        print(f"  {mode}: recall={stats['recall_at_k']:.3f} usage_v2={stats['usage_rate']:.3f} ignorance={stats['ignorance_rate']:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
