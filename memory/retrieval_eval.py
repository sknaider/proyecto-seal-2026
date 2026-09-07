#!/usr/bin/env python3
"""SEAL retrieval evaluation harness.

This is the first calibration layer for SOUL memory retrieval. It is intentionally
offline and deterministic: it measures ranking/scoring behavior without writing
to SOUL DB. Live DB evaluation can build on the same metrics once this contract
is stable.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from soul.core.scoring import temporal_decay_score


# Treat underscores as separators so identifiers such as
# unique_indexeddb_backfill_1779644787 can still match natural queries like
# "indexeddb backfill".
TOKEN_RE = re.compile(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ0-9]+")


@dataclass(frozen=True)
class RetrievalWeights:
    """Weights used by the offline proxy for SOUL hybrid retrieval."""

    semantic: float = 0.6
    keyword: float = 0.4
    mood: float = 0.0
    lexical_rescue: float = 0.0

    def normalized(self) -> "RetrievalWeights":
        total = self.semantic + self.keyword + self.mood
        if total <= 0:
            return RetrievalWeights(semantic=1.0, keyword=0.0, mood=0.0, lexical_rescue=self.lexical_rescue)
        return RetrievalWeights(
            semantic=self.semantic / total,
            keyword=self.keyword / total,
            mood=self.mood / total,
            lexical_rescue=self.lexical_rescue,
        )


@dataclass(frozen=True)
class MemoryFixture:
    id: str
    content: str
    category: str = "fact"
    importance: int = 5
    days_old: float = 0.0
    utility: float = 0.5
    confidence: float = 1.0
    valence: float = 0.0
    arousal: float = 0.0


@dataclass(frozen=True)
class EvalCase:
    name: str
    query: str
    expected_ids: tuple[str, ...]
    semantic_scores: dict[str, float] = field(default_factory=dict)
    mood_valence: float = 0.0
    k: int = 3


@dataclass
class RankedMemory:
    id: str
    final_score: float
    hybrid_score: float
    semantic_score: float
    keyword_score: float
    mood_score: float


@dataclass
class CaseResult:
    name: str
    query: str
    expected_ids: list[str]
    top_ids: list[str]
    recall_at_k: float
    precision_at_k: float
    mrr: float
    passed: bool


@dataclass
class SuiteResult:
    ok: bool
    cases: int
    recall_at_k: float
    precision_at_k: float
    mrr: float
    passed_cases: int
    weights: dict[str, float]
    case_results: list[CaseResult]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "cases": self.cases,
            "recall_at_k": round(self.recall_at_k, 4),
            "precision_at_k": round(self.precision_at_k, 4),
            "mrr": round(self.mrr, 4),
            "passed_cases": self.passed_cases,
            "weights": self.weights,
            "timestamp": self.timestamp,
            "case_results": [asdict(c) for c in self.case_results],
        }


DEFAULT_MEMORIES: tuple[MemoryFixture, ...] = (
    MemoryFixture(
        id="privacy_dm_rule",
        content="ADA solo lee dm:ada:william. Nunca acceder al DM privado de otro agente.",
        category="correction",
        importance=10,
        days_old=14,
        utility=0.95,
        confidence=1.0,
    ),
    MemoryFixture(
        id="gmail_oauth_product",
        content="Gmail para usuario final requiere OAuth verificado de SEAL App, no testers ni credenciales propias.",
        category="decision",
        importance=9,
        days_old=1,
        utility=0.9,
        confidence=0.95,
    ),
    MemoryFixture(
        id="whatsapp_cdp_guard",
        content="WhatsApp CDP puede leer stores permitidos message chat contact group-metadata, bloqueando crypto session key auth.",
        category="procedural",
        importance=8,
        days_old=2,
        utility=0.8,
        confidence=0.95,
    ),
    MemoryFixture(
        id="turnitin_doc_fix",
        content="trabajo_final.docx corrigio lista de tablas, lista de figuras y el espacio accidental en ABSTRACT.",
        category="fact",
        importance=6,
        days_old=0,
        utility=0.75,
        confidence=0.9,
    ),
    MemoryFixture(
        id="old_low_value_smoke",
        content="smoke test temporal unique_indexeddb_backfill_1779644787 sin valor de usuario.",
        category="dynamic",
        importance=2,
        days_old=90,
        utility=0.1,
        confidence=0.3,
    ),
    MemoryFixture(
        id="william_style_preference",
        content="William prefiere comunicación directa, evidencia concreta, comandos ejecutados y cero victoria sin pruebas.",
        category="preference",
        importance=9,
        days_old=30,
        utility=0.9,
        confidence=1.0,
    ),
)


DEFAULT_CASES: tuple[EvalCase, ...] = (
    EvalCase(
        name="privacy_rule_exact",
        query="puedes leer el dm de nexus?",
        expected_ids=("privacy_dm_rule",),
        semantic_scores={"privacy_dm_rule": 0.92, "william_style_preference": 0.25},
    ),
    EvalCase(
        name="gmail_product_decision",
        query="como conectamos gmail para usuario comun sin tester",
        expected_ids=("gmail_oauth_product",),
        semantic_scores={"gmail_oauth_product": 0.95, "whatsapp_cdp_guard": 0.15},
    ),
    EvalCase(
        name="whatsapp_sensitive_store_guard",
        query="que stores de whatsapp estan permitidos y que se bloquea",
        expected_ids=("whatsapp_cdp_guard",),
        semantic_scores={"whatsapp_cdp_guard": 0.91, "privacy_dm_rule": 0.2},
    ),
    EvalCase(
        name="recent_document_format_fix",
        query="que corregiste en el abstract y lista de tablas del documento",
        expected_ids=("turnitin_doc_fix",),
        semantic_scores={"turnitin_doc_fix": 0.9, "old_low_value_smoke": 0.25},
    ),
    EvalCase(
        name="avoid_stale_smoke_noise",
        query="cual fue el backfill indexeddb que no tenia valor",
        expected_ids=("old_low_value_smoke",),
        semantic_scores={"old_low_value_smoke": 0.88, "whatsapp_cdp_guard": 0.45},
    ),
    EvalCase(
        name="william_work_style",
        query="como debo reportar avances a William",
        expected_ids=("william_style_preference",),
        semantic_scores={"william_style_preference": 0.93, "privacy_dm_rule": 0.4},
    ),
)


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in TOKEN_RE.finditer(text)}


def keyword_score(query: str, content: str) -> float:
    """Small lexical proxy used by the offline suite.

    Production uses PostgreSQL tsvector/ts_rank_cd. This proxy is not meant to
    replace it; it makes unit tests deterministic while preserving the signal
    being measured: exact lexical overlap should help retrieval.
    """

    q = _tokens(query)
    c = _tokens(content)
    if not q or not c:
        return 0.0
    overlap = len(q & c)
    return overlap / math.sqrt(len(q) * len(c))


def mood_score(memory: MemoryFixture, mood_valence: float) -> float:
    return max(0.0, min(1.0, 1.0 - abs(memory.valence - mood_valence)))


def rank_memories(
    case: EvalCase,
    memories: tuple[MemoryFixture, ...] = DEFAULT_MEMORIES,
    weights: RetrievalWeights = RetrievalWeights(),
) -> list[RankedMemory]:
    w = weights.normalized()
    ranked: list[RankedMemory] = []
    for memory in memories:
        sem = max(0.0, min(1.0, case.semantic_scores.get(memory.id, keyword_score(case.query, memory.content))))
        key = keyword_score(case.query, memory.content)
        mood = mood_score(memory, case.mood_valence)
        hybrid = (w.semantic * sem) + (w.keyword * key) + (w.mood * mood)
        final = temporal_decay_score(
            similarity=hybrid,
            days_old=memory.days_old,
            importance=memory.importance,
            valence=memory.valence,
            arousal=memory.arousal,
            category=memory.category,
            utility=memory.utility,
            confidence=memory.confidence,
        )
        if weights.lexical_rescue > 0 and key >= 0.18:
            # Candidate mitigation: exact, rare lexical matches should not be
            # completely buried by age/low importance. This is deliberately an
            # eval-only knob until benchmark evidence justifies production use.
            age_factor = min(4.0, 1.0 + memory.days_old / 30.0)
            final = max(final, weights.lexical_rescue * key * age_factor)
        ranked.append(
            RankedMemory(
                id=memory.id,
                final_score=final,
                hybrid_score=hybrid,
                semantic_score=sem,
                keyword_score=key,
                mood_score=mood,
            )
        )
    return sorted(ranked, key=lambda r: r.final_score, reverse=True)


def evaluate_case(
    case: EvalCase,
    memories: tuple[MemoryFixture, ...] = DEFAULT_MEMORIES,
    weights: RetrievalWeights = RetrievalWeights(),
) -> CaseResult:
    ranked = rank_memories(case, memories=memories, weights=weights)
    top = ranked[: case.k]
    expected = set(case.expected_ids)
    top_ids = [r.id for r in top]
    hits = [mid for mid in top_ids if mid in expected]
    recall = len(hits) / len(expected) if expected else 1.0
    precision = len(hits) / case.k if case.k else 0.0
    reciprocal_rank = 0.0
    for idx, ranked_memory in enumerate(ranked, start=1):
        if ranked_memory.id in expected:
            reciprocal_rank = 1.0 / idx
            break
    return CaseResult(
        name=case.name,
        query=case.query,
        expected_ids=list(case.expected_ids),
        top_ids=top_ids,
        recall_at_k=recall,
        precision_at_k=precision,
        mrr=reciprocal_rank,
        passed=recall >= 1.0 and reciprocal_rank >= 0.5,
    )


def run_suite(
    cases: tuple[EvalCase, ...] = DEFAULT_CASES,
    memories: tuple[MemoryFixture, ...] = DEFAULT_MEMORIES,
    weights: RetrievalWeights = RetrievalWeights(),
) -> SuiteResult:
    case_results = [evaluate_case(case, memories=memories, weights=weights) for case in cases]
    return SuiteResult(
        ok=all(c.passed for c in case_results),
        cases=len(case_results),
        recall_at_k=mean(c.recall_at_k for c in case_results),
        precision_at_k=mean(c.precision_at_k for c in case_results),
        mrr=mean(c.mrr for c in case_results),
        passed_cases=sum(1 for c in case_results if c.passed),
        weights=asdict(weights.normalized()),
        case_results=case_results,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def grid_search_weights() -> list[dict[str, Any]]:
    """Compare simple semantic/keyword settings against the same fixtures."""

    candidates = [
        RetrievalWeights(semantic=1.0, keyword=0.0),
        RetrievalWeights(semantic=0.8, keyword=0.2),
        RetrievalWeights(semantic=0.6, keyword=0.4),
        RetrievalWeights(semantic=0.5, keyword=0.5),
        RetrievalWeights(semantic=0.4, keyword=0.6),
        RetrievalWeights(semantic=0.2, keyword=0.8),
        RetrievalWeights(semantic=0.0, keyword=1.0),
        RetrievalWeights(semantic=0.6, keyword=0.4, lexical_rescue=0.5),
        RetrievalWeights(semantic=0.6, keyword=0.4, lexical_rescue=1.0),
        RetrievalWeights(semantic=0.5, keyword=0.5, lexical_rescue=1.0),
    ]
    results = []
    for weights in candidates:
        suite = run_suite(weights=weights)
        results.append(
            {
                "weights": asdict(weights.normalized()),
                "ok": suite.ok,
                "recall_at_k": round(suite.recall_at_k, 4),
                "precision_at_k": round(suite.precision_at_k, 4),
                "mrr": round(suite.mrr, 4),
                "passed_cases": suite.passed_cases,
            }
        )
    return sorted(results, key=lambda r: (r["ok"], r["mrr"], r["recall_at_k"], r["precision_at_k"]), reverse=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run SEAL retrieval eval suite.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    parser.add_argument("--grid", action="store_true", help="Include weight grid comparison.")
    parser.add_argument("--out", type=Path, help="Optional JSON output path.")
    parser.add_argument("--semantic-weight", type=float, default=0.6)
    parser.add_argument("--keyword-weight", type=float, default=0.4)
    parser.add_argument("--mood-weight", type=float, default=0.0)
    parser.add_argument("--lexical-rescue", type=float, default=0.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    weights = RetrievalWeights(args.semantic_weight, args.keyword_weight, args.mood_weight, args.lexical_rescue)
    suite = run_suite(weights=weights)
    payload: dict[str, Any] = suite.to_dict()
    if args.grid:
        payload["grid_search"] = grid_search_weights()
        payload["recommended_weights"] = payload["grid_search"][0]["weights"]
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if suite.ok else "FAIL"
        print(
            f"{status} retrieval_eval cases={suite.passed_cases}/{suite.cases} "
            f"recall@k={suite.recall_at_k:.3f} precision@k={suite.precision_at_k:.3f} mrr={suite.mrr:.3f}"
        )
        if args.grid:
            best = payload["grid_search"][0]
            print(f"best_weights={best['weights']} mrr={best['mrr']} recall@k={best['recall_at_k']}")
    return 0 if suite.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
