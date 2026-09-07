#!/usr/bin/env python3
"""Read-only known-item shadow evaluation of live SOUL pgvector retrieval."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "memory"))
from embeddings import get_embeddings_batch  # noqa: E402
from seal_secrets import pg_dsn  # noqa: E402


TOKEN_RE = re.compile(r"[A-Za-zÁÉÍÓÚáéíóúÑñ0-9_./:-]{4,}")
STOP = {"william", "soul", "memoria", "memory", "para", "como", "esta", "este", "todo", "debe", "2026"}


def make_query(content: str, category: str) -> str:
    terms: list[str] = []
    for token in TOKEN_RE.findall(content):
        normalized = token.lower().strip("_.:/-")
        if normalized in STOP or normalized in terms:
            continue
        terms.append(normalized)
        if len(terms) == 8:
            break
    return f"recupera {category} " + " ".join(terms)


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * p
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


async def run(agent: str, sample: int, k: int, seed: str) -> dict[str, Any]:
    conn = await asyncpg.connect(pg_dsn(required=True))
    started = time.perf_counter()
    try:
        async with conn.transaction(readonly=True, isolation="repeatable_read"):
            readonly = await conn.fetchval("SHOW transaction_read_only")
            rows = await conn.fetch(
                """
                WITH candidates AS (
                  SELECT id, category, content, temporal_cluster, created_at,
                         row_number() OVER (
                           PARTITION BY category ORDER BY md5(id::text || $2)
                         ) category_rank
                  FROM soul_v3.memories
                  WHERE agent=$1 AND invalid_at IS NULL AND superseded_by IS NULL
                    AND embedding IS NOT NULL AND content IS NOT NULL AND length(content)>=80
                    AND category IN ('correction','decision','milestone','pattern','preference','fact','technical-fact')
                )
                SELECT id,category,content,temporal_cluster,created_at
                FROM candidates
                WHERE category_rank <= GREATEST(2, CEIL($3::numeric / 7)::int)
                ORDER BY md5(id::text || $2)
                LIMIT $3
                """,
                agent,
                seed,
                sample,
            )
            queries = [make_query(row["content"], row["category"]) for row in rows]
            vectors = await get_embeddings_batch(queries)
            observations: list[dict[str, Any]] = []
            ages: list[float] = []
            stale_count = 0
            relevant_returned = 0
            returned_total = 0
            exact_outcomes = 0
            exact_returned_total = 0
            for target, query, vector in zip(rows, queries, vectors):
                hits = await conn.fetch(
                    """
                    SELECT id,category,temporal_cluster,created_at,invalid_at,superseded_by,
                           1-(embedding <=> $2::vector) similarity
                    FROM soul_v3.memories
                    WHERE agent=$1 AND invalid_at IS NULL AND embedding IS NOT NULL
                    ORDER BY embedding <=> $2::vector
                    LIMIT $3
                    """,
                    agent,
                    json.dumps(vector),
                    k,
                )
                # Exact scan is a read-only diagnostic oracle for filtered ANN starvation.
                await conn.execute("SET LOCAL enable_indexscan = off")
                exact_hits = await conn.fetch(
                    """
                    SELECT id
                    FROM soul_v3.memories
                    WHERE agent=$1 AND invalid_at IS NULL AND embedding IS NOT NULL
                    ORDER BY embedding <=> $2::vector
                    LIMIT $3
                    """,
                    agent,
                    json.dumps(vector),
                    k,
                )
                await conn.execute("SET LOCAL enable_indexscan = on")
                ids = [int(hit["id"]) for hit in hits]
                exact_ids = [int(hit["id"]) for hit in exact_hits]
                target_cluster = target["temporal_cluster"]
                relevant = [
                    hit for hit in hits
                    if int(hit["id"]) == int(target["id"])
                    or (target_cluster is not None and hit["temporal_cluster"] == target_cluster)
                ]
                stale = [hit for hit in hits if hit["invalid_at"] is not None or hit["superseded_by"] is not None]
                now = datetime.now(timezone.utc)
                for hit in hits:
                    if hit["created_at"]:
                        ages.append(max(0.0, (now - hit["created_at"]).total_seconds() / 86400))
                relevant_returned += len(relevant)
                stale_count += len(stale)
                returned_total += len(hits)
                exact_returned_total += len(exact_hits)
                exact_outcomes += int(int(target["id"]) in exact_ids)
                observations.append(
                    {
                        "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                        "expected_id": int(target["id"]),
                        "category": target["category"],
                        "top_ids": ids,
                        "exact_top_ids": exact_ids,
                        "target_hit": int(target["id"]) in ids,
                        "exact_target_hit": int(target["id"]) in exact_ids,
                        "relevant_count": len(relevant),
                        "stale_count": len(stale),
                    }
                )
            outcomes = sum(obs["target_hit"] for obs in observations)
            return {
                "schema": "soul-live-retrieval-shadow-v1",
                "label": "controlled_eval_readonly",
                "agent": agent,
                "transaction_read_only": readonly == "on",
                "isolation": "repeatable_read",
                "sample_n": len(observations),
                "k": k,
                "known_item_recall_at_k": outcomes / len(observations) if observations else None,
                "exact_known_item_recall_at_k": exact_outcomes / len(observations) if observations else None,
                "ann_result_coverage": returned_total / (len(observations) * k) if observations else None,
                "exact_result_coverage": exact_returned_total / (len(observations) * k) if observations else None,
                "precision_at_k": relevant_returned / returned_total if returned_total else None,
                "outcome_success_rate": outcomes / len(observations) if observations else None,
                "stale_leakage_rate": stale_count / returned_total if returned_total else None,
                "returned_age_days_p50": percentile(ages, 0.5),
                "returned_age_days_p80": percentile(ages, 0.8),
                "observations": observations,
                "duration_seconds": round(time.perf_counter() - started, 3),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "claim_boundary": (
                    "Known-item read-only pgvector shadow. Queries are derived from sampled live records; "
                    "this is not organic user-query precision and does not invoke the write-logging active_recall path."
                ),
            }
    finally:
        await conn.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--sample", type=int, default=24)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--seed", default="soul-live-shadow-20260710")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = await run(args.agent.upper(), args.sample, args.k, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "transaction_read_only", "sample_n", "known_item_recall_at_k", "precision_at_k",
        "exact_known_item_recall_at_k", "ann_result_coverage", "exact_result_coverage",
        "outcome_success_rate", "stale_leakage_rate", "returned_age_days_p50", "returned_age_days_p80",
        "duration_seconds",
    )}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
