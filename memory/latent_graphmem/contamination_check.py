"""Pre-train contamination gate for LatentGraphMem.

Rule (William, 2026-04-12, post-leak incident):
    Before any LoRA training run, verify zero overlap between
    training_pairs and the held-out test set. If any check fails,
    abort training.

Checks:
    L1 — query-level exact overlap (lowercased + stripped)
    L2 — query-level Jaccard > 0.6 (paraphrase detection)
    L3 — positive subgraph memory_id overlap with test_set expected_memory_ids
    L4 — negative subgraph memory_id overlap (warning only)

Usage:
    python3 contamination_check.py \\
        --pairs-source test_set_v1 \\
        --test-set diagnostic/test_set_v1.jsonl

Exit codes:
    0 — clean (training can proceed)
    1 — contamination detected (training must abort)
    2 — infrastructure error
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg

PG_DSN = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"


def _tokens(text: str) -> set[str]:
    return {t for t in text.lower().split() if len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


async def audit(
    pairs_source: str,
    test_set_path: Path,
    jaccard_threshold: float = 0.6,
    dsn: str = PG_DSN,
) -> dict:
    """Run all contamination checks. Returns dict with verdict + details."""
    if not test_set_path.exists():
        raise FileNotFoundError(f"test set not found: {test_set_path}")

    test_queries = [
        json.loads(l) for l in test_set_path.read_text().splitlines() if l.strip()
    ]
    test_query_texts = {q["query"].strip().lower() for q in test_queries}
    test_query_tokens = [_tokens(q["query"]) for q in test_queries]
    test_mem_ids: set[int] = set()
    for q in test_queries:
        for mid in q.get("expected_memory_ids", []) or []:
            try:
                test_mem_ids.add(int(mid))
            except (TypeError, ValueError):
                continue

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT query, label, subgraph "
            "FROM latent_graphmem_training_pairs WHERE source=$1",
            pairs_source,
        )
    finally:
        await conn.close()

    exact_overlap = 0
    paraphrase_overlap = 0
    pos_subgraph_leak = 0
    neg_subgraph_leak = 0
    pos_total = 0
    neg_total = 0
    contaminated_mem_ids: set[int] = set()

    for r in rows:
        q_text = (r["query"] or "").strip().lower()
        sg = r["subgraph"]
        if isinstance(sg, str):
            sg = json.loads(sg)

        if q_text in test_query_texts:
            exact_overlap += 1
        else:
            q_tok = _tokens(q_text)
            if any(_jaccard(q_tok, t) > jaccard_threshold for t in test_query_tokens):
                paraphrase_overlap += 1

        node_ids: set[int] = set()
        for n in (sg.get("nodes") or []):
            mid = n.get("memory_id") or n.get("id")
            try:
                if mid is not None:
                    node_ids.add(int(mid))
            except (TypeError, ValueError):
                continue
        hit = node_ids & test_mem_ids

        if r["label"] == 1:
            pos_total += 1
            if hit:
                pos_subgraph_leak += 1
                contaminated_mem_ids |= hit
        else:
            neg_total += 1
            if hit:
                neg_subgraph_leak += 1

    clean = (
        exact_overlap == 0
        and paraphrase_overlap == 0
        and pos_subgraph_leak == 0
    )

    return {
        "pairs_source": pairs_source,
        "test_set": str(test_set_path),
        "training_pairs_total": len(rows),
        "pos_total": pos_total,
        "neg_total": neg_total,
        "L1_exact_query_overlap": exact_overlap,
        "L2_paraphrase_overlap": paraphrase_overlap,
        "L3_positive_subgraph_leak": pos_subgraph_leak,
        "L4_negative_subgraph_leak": neg_subgraph_leak,
        "contaminated_mem_ids": sorted(contaminated_mem_ids),
        "test_mem_ids_total": len(test_mem_ids),
        "contamination_rate_pct": (
            100.0 * len(contaminated_mem_ids) / max(1, len(test_mem_ids))
        ),
        "clean": clean,
        "verdict": "CLEAN" if clean else "CONTAMINATED — ABORT TRAINING",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="LatentGraphMem contamination gate")
    parser.add_argument("--pairs-source", default="test_set_v1",
                        help="source tag in latent_graphmem_training_pairs")
    parser.add_argument("--test-set", type=Path,
                        default=Path(__file__).resolve().parents[1] /
                                "diagnostic" / "test_set_v1.jsonl")
    parser.add_argument("--jaccard-threshold", type=float, default=0.6)
    parser.add_argument("--json", action="store_true",
                        help="print full report as JSON")
    args = parser.parse_args()

    try:
        report = asyncio.run(audit(
            pairs_source=args.pairs_source,
            test_set_path=args.test_set,
            jaccard_threshold=args.jaccard_threshold,
        ))
    except Exception as e:
        print(f"[contamination_check] ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"[contamination_check] verdict: {report['verdict']}")
        print(f"  pairs: {report['training_pairs_total']} "
              f"(pos={report['pos_total']}, neg={report['neg_total']})")
        print(f"  L1 exact query overlap:    {report['L1_exact_query_overlap']}")
        print(f"  L2 paraphrase overlap:     {report['L2_paraphrase_overlap']}")
        print(f"  L3 positive subgraph leak: {report['L3_positive_subgraph_leak']}")
        print(f"  L4 negative subgraph leak: {report['L4_negative_subgraph_leak']} (warn)")
        print(f"  contamination rate:        {report['contamination_rate_pct']:.1f}%")

    return 0 if report["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
