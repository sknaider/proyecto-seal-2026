#!/usr/bin/env python3
"""Live SOUL retrieval evaluation against PostgreSQL/pgvector.

This runner is read-only. It samples real memories, builds deterministic
query→expected-memory cases, then compares ranking strategies:

- weighted: current semantic/BM25 blend with temporal decay
- lexical_rescue: weighted plus exact lexical floor
- rrf: reciprocal-rank fusion of semantic and BM25 ranks
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from embeddings import get_embedding
from seal_secrets import pg_dsn
from soul.core.scoring import temporal_decay_score


DB_URL = os.environ.get("SEAL_PG_DSN", pg_dsn(required=True))

CATEGORIES = ("correction", "decision", "milestone", "pattern", "trust", "preference", "dynamic")
TOKEN_RE = re.compile(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ0-9]+")
STOPWORDS = {
    "para", "como", "este", "esta", "estos", "estas", "pero", "porque", "cuando", "donde",
    "desde", "sobre", "entre", "todo", "toda", "todos", "todas", "debe", "deben", "dejo",
    "quedo", "quedó", "william", "ada", "seal", "memory", "memoria", "recuerda", "recupera",
    "2026", "lima", "orden", "pidio", "pidió", "dice", "dijo", "hacer", "tiene", "tienen",
}


@dataclass(frozen=True)
class LiveCase:
    name: str
    query: str
    expected_id: int
    category: str


@dataclass
class Candidate:
    id: int
    category: str
    content: str
    importance: int
    created_at: datetime | None
    valence: float
    arousal: float
    utility: float
    confidence: float
    semantic_score: float = 0.0
    keyword_score: float = 0.0
    semantic_rank: int = 9999
    keyword_rank: int = 9999


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for match in TOKEN_RE.finditer(text or ""):
        token = match.group(0).lower()
        if len(token) < 4 or token in STOPWORDS:
            continue
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _expand_identifier_text(text: str) -> str:
    expanded = re.sub(r"[_./:\-]+", " ", text or "").strip()
    if not expanded or expanded == text:
        return text
    return f"{text} {expanded}"


def make_query(content: str, category: str) -> str:
    terms = _tokens(content)[:8]
    if not terms:
        return f"recupera memoria {category}"
    prefix = {
        "correction": "que correccion recuerda",
        "decision": "que decision tomamos sobre",
        "milestone": "que hito importante hubo sobre",
        "pattern": "que patron se detecto en",
        "trust": "que regla de confianza aplica a",
        "preference": "que preferencia recuerda sobre",
        "dynamic": "que dato temporal habia sobre",
    }.get(category, "recupera memoria sobre")
    return f"{prefix} {' '.join(terms)}"


def _days_old(created_at: datetime | None) -> float:
    if not created_at:
        return 0.0
    now = datetime.now(created_at.tzinfo or timezone.utc)
    return max(0.0, (now - created_at).total_seconds() / 86400)


def _rrf_score(rank: int, k: int = 60) -> float:
    if rank >= 9999:
        return 0.0
    return 1.0 / (k + rank)


def quality_multiplier(content: str) -> float:
    """Conservative penalty for terse chat-excerpt memories that often outrank richer memories."""
    text = re.sub(r"\s+", " ", content or "").strip()
    tokens = _tokens(text)
    multiplier = 1.0
    if re.match(r"^\[[A-ZÁÉÍÓÚÑ]+\]:", text) and len(text) < 180:
        multiplier *= 0.72
    if len(tokens) < 10 and len(text) < 140:
        multiplier *= 0.78
    return max(0.45, multiplier)


def exact_token_overlap(query: str, content: str) -> tuple[float, int]:
    query_tokens = set(_tokens(_expand_identifier_text(query)))
    content_tokens = set(_tokens(_expand_identifier_text(content)))
    if not query_tokens:
        return 0.0, 0
    shared = query_tokens & content_tokens
    return len(shared) / len(query_tokens), len(shared)


def exact_match_rescue_floor(query: str, content: str) -> float:
    """Floor score for memories with many exact technical/entity tokens in common."""
    ratio, shared_count = exact_token_overlap(query, content)
    if shared_count < 4 or ratio < 0.65:
        return 0.0
    return min(0.72, 0.42 + (0.22 * ratio))


def _base_decay(score: float, cand: Candidate) -> float:
    return temporal_decay_score(
        similarity=score,
        days_old=_days_old(cand.created_at),
        importance=cand.importance,
        valence=cand.valence,
        arousal=cand.arousal,
        category=cand.category,
        utility=cand.utility,
        confidence=cand.confidence,
    )


def rank_candidates(
    candidates: list[Candidate],
    strategy: str,
    category: str | None = None,
    query: str = "",
) -> list[tuple[int, float]]:
    use_quality = "_quality" in strategy
    strategy = strategy.replace("_quality", "")
    if strategy.endswith("_hmem") and category:
        candidates = [cand for cand in candidates if cand.category == category]
        strategy = strategy.removesuffix("_hmem")
    ranked: list[tuple[int, float]] = []
    for cand in candidates:
        if strategy == "rrf":
            fused = _rrf_score(cand.semantic_rank) + _rrf_score(cand.keyword_rank)
            score = _base_decay(min(1.0, fused * 30.0), cand)
        else:
            hybrid = (0.6 * cand.semantic_score) + (0.4 * cand.keyword_score)
            score = _base_decay(hybrid, cand)
            if strategy == "lexical_rescue" and cand.keyword_score >= 0.75:
                score = max(score, 0.5 * cand.keyword_score)
        if use_quality:
            score *= quality_multiplier(cand.content)
        score = max(score, exact_match_rescue_floor(query, cand.content))
        ranked.append((cand.id, score))
    return sorted(ranked, key=lambda item: item[1], reverse=True)


def eval_case(case: LiveCase, candidates: list[Candidate], strategy: str, k: int) -> dict[str, Any]:
    ranked = rank_candidates(candidates, strategy, case.category, case.query)
    top_ids = [mid for mid, _ in ranked[:k]]
    rank = next((idx for idx, (mid, _) in enumerate(ranked, start=1) if mid == case.expected_id), 0)
    return {
        "name": case.name,
        "query": case.query,
        "expected_id": case.expected_id,
        "category": case.category,
        "top_ids": top_ids,
        "hit_at_k": case.expected_id in top_ids,
        "mrr": (1.0 / rank) if rank else 0.0,
        "rank": rank,
    }


async def load_cases(conn: asyncpg.Connection, agent: str, per_category: int) -> list[LiveCase]:
    rows = await conn.fetch(
        """
        WITH ranked AS (
            SELECT id, category, content,
                   row_number() OVER (
                     PARTITION BY category
                     ORDER BY importance DESC, created_at DESC, id DESC
                   ) AS rn
            FROM soul_v3.memories
            WHERE agent = $1
              AND invalid_at IS NULL
              AND embedding IS NOT NULL
              AND embedding_bm25 IS NOT NULL
              AND category = ANY($2::text[])
              AND content IS NOT NULL
              AND length(content) >= 60
        )
        SELECT id, category, content
        FROM ranked
        WHERE rn <= $3
        ORDER BY category, rn
        """,
        agent,
        list(CATEGORIES),
        per_category,
    )
    cases: list[LiveCase] = []
    for row in rows:
        mem_id = int(row["id"])
        category = row["category"]
        cases.append(
            LiveCase(
                name=f"{category}_{mem_id}",
                query=make_query(row["content"], category),
                expected_id=mem_id,
                category=category,
            )
        )
    return cases


async def fetch_candidates(
    conn: asyncpg.Connection,
    agent: str,
    case: LiveCase,
    pool_size: int,
) -> list[Candidate]:
    qvec = await get_embedding(case.query)
    vec_json = json.dumps(qvec)
    query_text = _expand_identifier_text(case.query)
    rows = await conn.fetch(
        """
        WITH semantic AS (
            SELECT id, 1 - (embedding <=> $2::vector) AS score,
                   row_number() OVER (ORDER BY embedding <=> $2::vector) AS s_rank
            FROM soul_v3.memories
            WHERE agent = $1 AND invalid_at IS NULL AND embedding IS NOT NULL
            ORDER BY embedding <=> $2::vector
            LIMIT $4
        ),
        keyword AS (
            SELECT id,
                   ts_rank_cd(
                     COALESCE(embedding_bm25, ''::tsvector) ||
                     to_tsvector('simple', regexp_replace(COALESCE(content, ''), '[_./:\\-]+', ' ', 'g')),
                     websearch_to_tsquery('simple', $3)
                   ) AS score,
                   row_number() OVER (
                     ORDER BY ts_rank_cd(
                       COALESCE(embedding_bm25, ''::tsvector) ||
                       to_tsvector('simple', regexp_replace(COALESCE(content, ''), '[_./:\\-]+', ' ', 'g')),
                       websearch_to_tsquery('simple', $3)
                     ) DESC
                   ) AS k_rank
            FROM soul_v3.memories
            WHERE agent = $1
              AND invalid_at IS NULL
              AND (
                COALESCE(embedding_bm25, ''::tsvector) ||
                to_tsvector('simple', regexp_replace(COALESCE(content, ''), '[_./:\\-]+', ' ', 'g'))
              ) @@ websearch_to_tsquery('simple', $3)
            ORDER BY score DESC
            LIMIT $4
        ),
        ids AS (
            SELECT id FROM semantic
            UNION
            SELECT id FROM keyword
            UNION
            SELECT $5::bigint AS id
        )
        SELECT m.id, m.category, m.content, m.importance, m.created_at,
               COALESCE(m.valence, 0) AS valence,
               COALESCE(m.arousal, 0) AS arousal,
               COALESCE(m.utility_score, 0.5) AS utility,
               COALESCE(m.confidence_score, 1.0) AS confidence,
               COALESCE(s.score, 0) AS semantic_score,
               COALESCE(k.score, 0) AS keyword_score,
               COALESCE(s.s_rank, 9999) AS semantic_rank,
               COALESCE(k.k_rank, 9999) AS keyword_rank
        FROM ids
        JOIN soul_v3.memories m ON m.id = ids.id
        LEFT JOIN semantic s ON s.id = m.id
        LEFT JOIN keyword k ON k.id = m.id
        WHERE m.invalid_at IS NULL
        """,
        agent,
        vec_json,
        query_text,
        pool_size,
        case.expected_id,
    )
    max_sem = max((float(r["semantic_score"] or 0.0) for r in rows), default=1.0) or 1.0
    max_kw = max((float(r["keyword_score"] or 0.0) for r in rows), default=1.0) or 1.0
    candidates: list[Candidate] = []
    for row in rows:
        candidates.append(
            Candidate(
                id=int(row["id"]),
                category=row["category"],
                content=row["content"] or "",
                importance=int(row["importance"] or 5),
                created_at=row["created_at"],
                valence=float(row["valence"] or 0.0),
                arousal=float(row["arousal"] or 0.0),
                utility=float(row["utility"] or 0.5),
                confidence=float(row["confidence"] or 1.0),
                semantic_score=max(0.0, float(row["semantic_score"] or 0.0) / max_sem),
                keyword_score=max(0.0, float(row["keyword_score"] or 0.0) / max_kw),
                semantic_rank=int(row["semantic_rank"] or 9999),
                keyword_rank=int(row["keyword_rank"] or 9999),
            )
        )
    return candidates


def summarize(strategy: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    hits = sum(1 for r in results if r["hit_at_k"])
    return {
        "strategy": strategy,
        "cases": total,
        "hit_at_k": round(hits / total, 4) if total else 0.0,
        "mrr": round(mean(r["mrr"] for r in results), 4) if results else 0.0,
        "passed_cases": hits,
        "failed_cases": total - hits,
    }


async def run(agent: str, per_category: int, pool_size: int, k: int) -> dict[str, Any]:
    started = time.perf_counter()
    conn = await asyncpg.connect(DB_URL)
    try:
        cases = await load_cases(conn, agent, per_category)
        strategies = (
            "weighted",
            "lexical_rescue",
            "rrf",
            "weighted_hmem",
            "lexical_rescue_hmem",
            "rrf_hmem",
            "weighted_quality_hmem",
            "lexical_rescue_quality_hmem",
            "rrf_quality_hmem",
        )
        by_strategy: dict[str, list[dict[str, Any]]] = {name: [] for name in strategies}
        for case in cases:
            candidates = await fetch_candidates(conn, agent, case, pool_size)
            for strategy in strategies:
                by_strategy[strategy].append(eval_case(case, candidates, strategy, k))
        summaries = [summarize(strategy, by_strategy[strategy]) for strategy in strategies]
        best = sorted(summaries, key=lambda r: (r["hit_at_k"], r["mrr"]), reverse=True)[0] if summaries else None
        return {
            "ok": bool(best and best["hit_at_k"] >= 0.95),
            "agent": agent,
            "categories": list(CATEGORIES),
            "case_count": len(cases),
            "per_category": per_category,
            "pool_size": pool_size,
            "k": k,
            "summaries": summaries,
            "best_strategy": best,
            "results": by_strategy,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        await conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run live SOUL retrieval eval.")
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--per-category", type=int, default=6)
    parser.add_argument("--pool-size", type=int, default=30)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = await run(args.agent.upper(), args.per_category, args.pool_size, args.k)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        best = payload.get("best_strategy") or {}
        parts = [
            f"{s['strategy']}={s['passed_cases']}/{s['cases']} hit@{payload['k']}={s['hit_at_k']:.3f} mrr={s['mrr']:.3f}"
            for s in payload["summaries"]
        ]
        print("LIVE retrieval_eval " + " | ".join(parts))
        print(f"best_strategy={best.get('strategy')} duration={payload['duration_seconds']}s")
    return 0 if payload["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
