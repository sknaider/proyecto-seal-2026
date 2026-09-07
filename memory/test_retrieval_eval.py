from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retrieval_eval import (
    DEFAULT_CASES,
    DEFAULT_MEMORIES,
    RetrievalWeights,
    evaluate_case,
    grid_search_weights,
    keyword_score,
    rank_memories,
    run_suite,
)


def test_keyword_score_rewards_lexical_overlap() -> None:
    strong = keyword_score("gmail oauth usuario comun", "Gmail requiere OAuth para usuario comun")
    weak = keyword_score("gmail oauth usuario comun", "WhatsApp usa CDP con stores permitidos")

    assert strong > weak
    assert 0.0 <= weak <= 1.0
    assert 0.0 <= strong <= 1.0


def test_keyword_score_splits_identifier_underscores() -> None:
    score = keyword_score(
        "indexeddb backfill",
        "smoke test temporal unique_indexeddb_backfill_1779644787 sin valor de usuario",
    )

    assert score > 0.25


def test_current_hybrid_weights_expose_known_stale_exact_match_gap() -> None:
    result = run_suite(weights=RetrievalWeights(semantic=0.6, keyword=0.4))

    assert result.ok is False
    assert result.passed_cases == len(DEFAULT_CASES) - 1
    assert result.recall_at_k >= 0.8
    assert result.mrr >= 0.8


def test_eval_candidate_rescues_stale_exact_match_gap() -> None:
    result = run_suite(weights=RetrievalWeights(semantic=0.6, keyword=0.4, lexical_rescue=0.5))

    assert result.ok is True
    assert result.passed_cases == len(DEFAULT_CASES)
    assert result.mrr == 1.0


def test_privacy_rule_ranks_above_style_preference_for_dm_query() -> None:
    case = next(c for c in DEFAULT_CASES if c.name == "privacy_rule_exact")
    ranked = rank_memories(case, DEFAULT_MEMORIES, RetrievalWeights(semantic=0.6, keyword=0.4))

    assert ranked[0].id == "privacy_dm_rule"


def test_current_weights_bury_stale_low_value_exact_match() -> None:
    case = next(c for c in DEFAULT_CASES if c.name == "avoid_stale_smoke_noise")
    result = evaluate_case(case, DEFAULT_MEMORIES, RetrievalWeights(semantic=0.6, keyword=0.4))

    assert "old_low_value_smoke" not in result.top_ids
    assert result.passed is False


def test_grid_search_returns_ranked_weight_candidates() -> None:
    results = grid_search_weights()

    assert results
    assert results[0]["mrr"] >= results[-1]["mrr"]
    assert any(
        r["weights"] == {"semantic": 0.6, "keyword": 0.4, "mood": 0.0, "lexical_rescue": 0.0}
        for r in results
    )
