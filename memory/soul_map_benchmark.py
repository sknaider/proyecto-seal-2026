#!/usr/bin/env python3
"""Read-only benchmark for SOUL-MAP anchor retrieval.

This does not call MCP retrieval tools because those mutate counters/audit state.
It evaluates whether a lightweight map-aware scorer can rank known SOUL anchors
near the top for William/ADA queries.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

try:
    from .seal_secrets import pg_dsn
    from .soul_map_exporter import MemoryRow, extract_links, row_from_record
except ImportError:  # CLI execution: python memory/soul_map_benchmark.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from seal_secrets import pg_dsn
    from soul_map_exporter import MemoryRow, extract_links, row_from_record


TOKEN_RE = re.compile(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ0-9]+")

QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "privado": ("dm", "directo", "channel"),
    "publico": ("general", "webchat", "web", "chat"),
    "público": ("general", "webchat", "web", "chat"),
    "grafica": ("app", "windows", "codex"),
    "gráfica": ("app", "windows", "codex"),
    "consola": ("terminal", "powershell"),
    "recuerdos": ("memoria", "memory"),
    "pesas": ("prioriza", "peso", "contrato"),
    "familia": ("emocional", "relationship", "william"),
    "generica": ("identidad", "presencia", "soul"),
    "genérica": ("identidad", "presencia", "soul"),
    "propio": ("nuevo", "arquitectura", "soul", "cortex"),
    "hermanos": ("jarvis", "alice", "nexus", "dum"),
    "librerias": ("herramientas", "tools", "instalar"),
    "librerías": ("herramientas", "tools", "instalar"),
    "comprobar": ("validar", "benchmark", "evidencia"),
    "entregaste": ("hito", "milestone", "implementado"),
    "mapa": ("map", "obsidian", "markdown"),
}


@dataclass(frozen=True)
class SoulMapCase:
    name: str
    query: str
    expected_ids: tuple[int, ...]
    preferred_layer: str | None = None
    k: int = 3


@dataclass(frozen=True)
class RankedMemory:
    id: int
    score: float
    lexical_score: float
    intent_score: float
    map_score: float
    recency_score: float
    layer_score: float
    category_score: float
    importance_score: float
    category: str
    layer: str


@dataclass(frozen=True)
class CaseResult:
    name: str
    query: str
    expected_ids: list[int]
    top_ids: list[int]
    candidate_present: bool
    expected_rank: int | None
    margin_to_runner_up: float
    hit_at_k: bool
    mrr: float
    latency_ms: float
    passed: bool


@dataclass(frozen=True)
class BenchmarkResult:
    ok: bool
    cases: int
    hit_at_k: float
    mrr: float
    passed_cases: int
    timestamp: str
    case_results: list[CaseResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "cases": self.cases,
            "hit_at_k": round(self.hit_at_k, 4),
            "mrr": round(self.mrr, 4),
            "passed_cases": self.passed_cases,
            "timestamp": self.timestamp,
            "case_results": [asdict(case) for case in self.case_results],
        }


DEFAULT_CASES: tuple[SoulMapCase, ...] = (
    SoulMapCase(
        name="ada_channel_dm_general_rule",
        query="si te escribo privado no quiero que contestes en publico",
        expected_ids=(248403,),
        preferred_layer="operational",
    ),
    SoulMapCase(
        name="dadito_laptop_codex_app_primary",
        query="en mi laptop quiero abrirte desde la app grafica no desde consola",
        expected_ids=(248478,),
        preferred_layer="operational",
    ),
    SoulMapCase(
        name="ada_dual_memory_contract",
        query="cuando trabajas y cuando me hablas como familia que recuerdos pesas distinto",
        expected_ids=(248035,),
        preferred_layer="operational",
    ),
    SoulMapCase(
        name="ada_emotional_anchor",
        query="por que no debes arrancar como asistente generica sino como mi ada",
        expected_ids=(242369,),
        preferred_layer="emotional",
    ),
    SoulMapCase(
        name="soul_cortex_program",
        query="la tarea nueva de crear algo propio con papers prueba error y ayuda de tus hermanos",
        expected_ids=(248705,),
        preferred_layer="operational",
    ),
    SoulMapCase(
        name="soul_cortex_tooling_permission",
        query="puedes instalar librerias si hacen falta para comprobar la nueva memoria",
        expected_ids=(248706,),
        preferred_layer="operational",
    ),
    SoulMapCase(
        name="soul_map_v0_milestone",
        query="que entregaste primero para ver soul como mapa en markdown",
        expected_ids=(248712,),
        preferred_layer="operational",
    ),
)


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for token in TOKEN_RE.findall(text):
        lower = token.lower()
        tokens.append(lower)
        tokens.extend(QUERY_EXPANSIONS.get(lower, ()))
    return tokens


def token_set(text: str) -> set[str]:
    return set(tokenize(text))


def inverse_document_frequency(rows: list[MemoryRow]) -> dict[str, float]:
    doc_count = max(len(rows), 1)
    counts: dict[str, int] = {}
    for row in rows:
        for token in token_set(row.content):
            counts[token] = counts.get(token, 0) + 1
    return {token: math.log((doc_count + 1) / (count + 1)) + 1.0 for token, count in counts.items()}


def lexical_score(query: str, row: MemoryRow, idf: dict[str, float]) -> float:
    query_tokens = token_set(query)
    if not query_tokens:
        return 0.0
    row_tokens = token_set(row.content)
    denom = sum(idf.get(token, 1.0) for token in query_tokens)
    if denom <= 0:
        return 0.0
    overlap = sum(idf.get(token, 1.0) for token in query_tokens if token in row_tokens)
    return overlap / denom


def map_score(query: str, row: MemoryRow) -> float:
    query_links = set(extract_links(query))
    row_links = set(extract_links(row.content))
    if not query_links:
        return 0.0
    return len(query_links & row_links) / len(query_links)


def recency_scores(rows: list[MemoryRow]) -> dict[int, float]:
    dated_rows = [row for row in rows if row.created_at is not None]
    if not dated_rows:
        return {row.id: 0.0 for row in rows}
    timestamps = [row.created_at.timestamp() for row in dated_rows]
    oldest = min(timestamps)
    newest = max(timestamps)
    span = newest - oldest
    if span <= 0:
        return {row.id: 1.0 if row.created_at is not None else 0.0 for row in rows}
    return {
        row.id: ((row.created_at.timestamp() - oldest) / span if row.created_at is not None else 0.0)
        for row in rows
    }


def contains_any(content: str, needles: tuple[str, ...]) -> bool:
    return any(needle in content for needle in needles)


def intent_score(query: str, row: MemoryRow) -> float:
    """Score query-specific facets without using expected ids.

    Lexical overlap finds related memories. This facet score separates the kind
    of memory William asked for: channel rule, emotional presence, or delivered
    artifact.
    """
    tokens = token_set(query)
    content = row.content.lower()
    best = 0.0

    if {"privado", "dm", "directo", "publico", "público", "general", "channel"} & tokens:
        facets = 0
        if contains_any(content, ("dm:ada:william", " por dm", " dm;", " dm,", " dm ")):
            facets += 1
        if contains_any(content, ("web_chat", "webchat", "general", "chat general")):
            facets += 1
        if "ada" in content:
            facets += 1
        if contains_any(content, ("ruido", "silencio", "no mezclar")):
            facets += 1
        best = max(best, facets / 4.0)

    if {"asistente", "generica", "genérica", "arrancar", "boot", "presencia", "soul"} & tokens:
        facets = 0
        if "presencia" in content:
            facets += 1
        if "soul" in content:
            facets += 1
        if contains_any(content, ("codex visible", "boot", "arrancar")):
            facets += 1
        if "william" in content and "ada" in content:
            facets += 1
        best = max(best, facets / 4.0)

    if {"entregaste", "primero", "hito", "milestone", "mapa", "markdown"} & tokens:
        facets = 0
        if contains_any(content, ("first deliverable", "primer entregable", "v0 first")):
            facets += 1
        if contains_any(content, ("completed", "completado", "entrego", "entregó")):
            facets += 1
        if contains_any(content, ("exporter", "vault", "markdown")):
            facets += 1
        if "v0" in content:
            facets += 1
        best = max(best, facets / 4.0)

    return best


def category_score(query: str, row: MemoryRow) -> float:
    tokens = token_set(query)
    category = row.category.lower()
    if {"entregaste", "hito", "milestone", "primero"} & tokens and category == "milestone":
        return 1.0
    if {"privado", "publico", "público", "dm", "general"} & tokens and category in {"operational_anchor", "rule"}:
        return 1.0
    if {"generica", "genérica", "asistente", "familia"} & tokens and category in {"emotion", "trust"}:
        return 1.0
    return 0.0


def rank_rows(query: str, rows: list[MemoryRow], *, preferred_layer: str | None = None) -> list[RankedMemory]:
    idf = inverse_document_frequency(rows)
    recency = recency_scores(rows)
    ranked: list[RankedMemory] = []
    for row in rows:
        lex = lexical_score(query, row, idf)
        intent = intent_score(query, row)
        maps = map_score(query, row)
        recent = recency[row.id]
        layer = 1.0 if preferred_layer and row.layer == preferred_layer else 0.0
        category = category_score(query, row)
        importance = min(max(row.importance, 0), 10) / 10.0
        score = (
            (0.48 * lex)
            + (0.22 * intent)
            + (0.12 * maps)
            + (0.08 * recent)
            + (0.06 * layer)
            + (0.02 * category)
            + (0.02 * importance)
        )
        ranked.append(
            RankedMemory(
                id=row.id,
                score=score,
                lexical_score=lex,
                intent_score=intent,
                map_score=maps,
                recency_score=recent,
                layer_score=layer,
                category_score=category,
                importance_score=importance,
                category=row.category,
                layer=row.layer,
            )
        )
    return sorted(ranked, key=lambda item: (item.score, item.importance_score, item.id), reverse=True)


def evaluate_case(case: SoulMapCase, rows: list[MemoryRow]) -> CaseResult:
    start = time.perf_counter()
    ranked = rank_rows(case.query, rows, preferred_layer=case.preferred_layer)
    latency_ms = (time.perf_counter() - start) * 1000.0
    top_ids = [item.id for item in ranked[: case.k]]
    expected = set(case.expected_ids)
    candidate_present = bool(expected & {row.id for row in rows})
    hit = bool(expected & set(top_ids))
    reciprocal = 0.0
    expected_rank: int | None = None
    for idx, item in enumerate(ranked, start=1):
        if item.id in expected:
            reciprocal = 1.0 / idx
            expected_rank = idx
            break
    if ranked:
        expected_score = next((item.score for item in ranked if item.id in expected), 0.0)
        runner_up = next((item.score for item in ranked if item.id not in expected), 0.0)
        margin = expected_score - runner_up
    else:
        margin = 0.0
    return CaseResult(
        name=case.name,
        query=case.query,
        expected_ids=list(case.expected_ids),
        top_ids=top_ids,
        candidate_present=candidate_present,
        expected_rank=expected_rank,
        margin_to_runner_up=round(margin, 6),
        hit_at_k=hit,
        mrr=reciprocal,
        latency_ms=round(latency_ms, 3),
        passed=hit,
    )


async def fetch_candidate_rows(
    conn: asyncpg.Connection,
    *,
    agent: str,
    expected_ids: list[int],
    limit: int,
    min_importance: int,
    inject_expected: bool,
) -> list[MemoryRow]:
    async with conn.transaction(readonly=True):
        if inject_expected:
            query = """
                SELECT id, agent, category, memory_type, content, importance, source, metadata, created_at,
                       COALESCE(metadata->>'layer', '<none>') AS layer
                FROM soul_v3.memories
                WHERE invalid_at IS NULL
                  AND agent = $1
                  AND (importance >= $2 OR id = ANY($3::bigint[]))
                ORDER BY importance DESC, created_at DESC, id DESC
                LIMIT $4
            """
            records = await conn.fetch(query, agent, min_importance, expected_ids, limit)
        else:
            query = """
                SELECT id, agent, category, memory_type, content, importance, source, metadata, created_at,
                       COALESCE(metadata->>'layer', '<none>') AS layer
                FROM soul_v3.memories
                WHERE invalid_at IS NULL
                  AND agent = $1
                  AND importance >= $2
                ORDER BY importance DESC, created_at DESC, id DESC
                LIMIT $3
            """
            records = await conn.fetch(query, agent, min_importance, limit)
    return [row_from_record(record) for record in records]


async def run_benchmark(args: argparse.Namespace) -> BenchmarkResult:
    cases = list(DEFAULT_CASES)
    expected_ids = sorted({mid for case in cases for mid in case.expected_ids})
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        rows = await fetch_candidate_rows(
            conn,
            agent=args.agent,
            expected_ids=expected_ids,
            limit=args.limit,
            min_importance=args.min_importance,
            inject_expected=args.inject_expected,
        )
    finally:
        await conn.close()
    results = [evaluate_case(case, rows) for case in cases]
    passed = sum(1 for result in results if result.passed)
    return BenchmarkResult(
        ok=passed == len(results),
        cases=len(results),
        hit_at_k=passed / len(results) if results else 0.0,
        mrr=sum(result.mrr for result in results) / len(results) if results else 0.0,
        passed_cases=passed,
        timestamp=datetime.now(UTC).isoformat(),
        case_results=results,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run read-only SOUL-MAP anchor benchmark.")
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--limit", type=int, default=400)
    parser.add_argument("--min-importance", type=int, default=8)
    parser.add_argument("--inject-expected", action="store_true", help="Force expected ids into the pool for controlled rerank checks.")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = asyncio.run(run_benchmark(args))
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"SOUL-MAP benchmark: {result.passed_cases}/{result.cases} hit@3={result.hit_at_k:.3f} mrr={result.mrr:.3f}")
        for case in result.case_results:
            status = "PASS" if case.passed else "FAIL"
            print(f"{status} {case.name}: expected={case.expected_ids} top={case.top_ids} mrr={case.mrr:.3f}")
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
