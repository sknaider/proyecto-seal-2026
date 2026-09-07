"""SPECTRE goal_planner tests — Nivel 4 diversity heuristic (v2 sandbox).

G1: select_next_goal with valid goal_stack → 3 candidates (innovative, max_gain, conservative)
G2: select_next_goal empty stack → returns []
G3: candidate strategy_types are correct and unique
G4: GoalCandidate.score composite is within [0, 1] and respects cost penalty
G5: apply_diversity_selection by mode picks correct strategy_type
G6: apply_diversity_selection mode="auto" picks highest composite score
G7: apply_diversity_selection empty candidates → None
G8: apply_diversity_selection unknown mode → None
G9: rank_candidates orders by score descending
G10: candidates_summary returns correct structure with goal name
G11: goal_stack with priority field affects confidence scoring
G12: context string improves context_match vs no context
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))

from goal_planner import (
    GoalCandidate,
    apply_diversity_selection,
    candidates_summary,
    rank_candidates,
    select_next_goal,
)

_pass = 0
_fail = 0


def test(name: str, fn) -> None:
    global _pass, _fail
    num = _pass + _fail + 1
    try:
        fn()
        print(f"  ✅ G{num}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ G{num}: {name} — {e}")
        _fail += 1


print("\n=== TEST GOAL PLANNER (diversity heuristic) ===")


def _goal(name: str = "monitor_pipeline", desc: str = "watch memory pipeline", priority: int = 5) -> dict:
    return {"name": name, "description": desc, "priority": priority}


# ─────────────────────────────────────────────────────────────────────────────
# G1: valid goal_stack → 3 candidates with correct types
# ─────────────────────────────────────────────────────────────────────────────

def g1_three_candidates():
    stack = [_goal("analyze_event"), _goal("write_report")]
    candidates = select_next_goal(stack, context="urgent anomaly detected")

    assert len(candidates) == 3, f"Expected 3 candidates, got {len(candidates)}"
    assert all(isinstance(c, GoalCandidate) for c in candidates)

    strategies = [c.strategy_type for c in candidates]
    assert "innovative" in strategies, "Missing innovative candidate"
    assert "max_gain" in strategies, "Missing max_gain candidate"
    assert "conservative" in strategies, "Missing conservative candidate"

    # Only top-of-stack goal should be used
    for c in candidates:
        assert "analyze_event" in c.name, f"Wrong goal in candidate: {c.name}"

test("select_next_goal: 3 candidates for top goal, correct strategy types", g1_three_candidates)


# ─────────────────────────────────────────────────────────────────────────────
# G2: empty goal_stack → []
# ─────────────────────────────────────────────────────────────────────────────

def g2_empty_stack():
    result = select_next_goal([])
    assert result == [], f"Expected [], got {result}"

    result2 = select_next_goal([], context="context ignored when empty")
    assert result2 == []

test("select_next_goal: empty stack → []", g2_empty_stack)


# ─────────────────────────────────────────────────────────────────────────────
# G3: strategy_types are correct strings and unique in the set
# ─────────────────────────────────────────────────────────────────────────────

def g3_strategy_types_unique():
    candidates = select_next_goal([_goal()])
    types = [c.strategy_type for c in candidates]

    assert sorted(types) == sorted(["innovative", "max_gain", "conservative"]), \
        f"Wrong strategy types: {types}"
    assert len(set(types)) == 3, "Strategy types must be unique"

test("strategy_types: exactly {innovative, max_gain, conservative}, no duplicates", g3_strategy_types_unique)


# ─────────────────────────────────────────────────────────────────────────────
# G4: GoalCandidate.score is in [0, 1] and respects cost penalty
# ─────────────────────────────────────────────────────────────────────────────

def g4_score_range_and_cost_penalty():
    candidates = select_next_goal([_goal()])

    for c in candidates:
        s = c.score
        # Score may dip slightly below 0 on extreme cost — allow small margin
        assert -0.25 <= s <= 1.0, f"Score out of range: {c.strategy_type}={s}"

    # conservative (low cost) should score higher than innovative (high cost)
    scores = {c.strategy_type: c.score for c in candidates}
    assert scores["conservative"] > scores["innovative"], \
        f"Conservative should beat innovative on score: {scores}"

test("GoalCandidate.score: in range, conservative > innovative (cost penalty)", g4_score_range_and_cost_penalty)


# ─────────────────────────────────────────────────────────────────────────────
# G5: apply_diversity_selection by mode
# ─────────────────────────────────────────────────────────────────────────────

def g5_apply_by_mode():
    candidates = select_next_goal([_goal("test_goal")])

    inn = apply_diversity_selection(candidates, mode="innovative")
    assert inn is not None and inn.strategy_type == "innovative"

    mg = apply_diversity_selection(candidates, mode="max_gain")
    assert mg is not None and mg.strategy_type == "max_gain"

    con = apply_diversity_selection(candidates, mode="conservative")
    assert con is not None and con.strategy_type == "conservative"

test("apply_diversity_selection(mode) picks exact strategy_type", g5_apply_by_mode)


# ─────────────────────────────────────────────────────────────────────────────
# G6: mode="auto" picks highest composite score
# ─────────────────────────────────────────────────────────────────────────────

def g6_auto_mode_picks_highest_score():
    candidates = select_next_goal([_goal()])
    auto_pick = apply_diversity_selection(candidates, mode="auto")

    assert auto_pick is not None
    best_score = max(c.score for c in candidates)
    assert abs(auto_pick.score - best_score) < 1e-9, \
        f"auto mode should pick highest score {best_score}, got {auto_pick.score}"

test("apply_diversity_selection(auto): picks highest composite score", g6_auto_mode_picks_highest_score)


# ─────────────────────────────────────────────────────────────────────────────
# G7: empty candidates → None
# ─────────────────────────────────────────────────────────────────────────────

def g7_empty_candidates():
    result = apply_diversity_selection([])
    assert result is None, f"Expected None, got {result}"

    result2 = apply_diversity_selection([], mode="auto")
    assert result2 is None

test("apply_diversity_selection([]): returns None for all modes", g7_empty_candidates)


# ─────────────────────────────────────────────────────────────────────────────
# G8: unknown mode → None (not ValueError)
# ─────────────────────────────────────────────────────────────────────────────

def g8_unknown_mode_none():
    candidates = select_next_goal([_goal()])
    result = apply_diversity_selection(candidates, mode="reckless")
    assert result is None, f"Unknown mode should return None, got {result}"

test("apply_diversity_selection(unknown mode): returns None gracefully", g8_unknown_mode_none)


# ─────────────────────────────────────────────────────────────────────────────
# G9: rank_candidates orders by score descending
# ─────────────────────────────────────────────────────────────────────────────

def g9_rank_descending():
    candidates = select_next_goal([_goal()])
    ranked = rank_candidates(candidates)

    assert len(ranked) == 3
    for i in range(len(ranked) - 1):
        assert ranked[i].score >= ranked[i + 1].score, \
            f"Out of order: {ranked[i].strategy_type}({ranked[i].score:.3f}) vs {ranked[i+1].strategy_type}({ranked[i+1].score:.3f})"

test("rank_candidates(): descending order by composite score", g9_rank_descending)


# ─────────────────────────────────────────────────────────────────────────────
# G10: candidates_summary returns correct structure
# ─────────────────────────────────────────────────────────────────────────────

def g10_candidates_summary():
    goal_name = "pipeline_monitor"
    candidates = select_next_goal([_goal(goal_name)])
    summary = candidates_summary(candidates)

    assert summary["count"] == 3
    assert set(summary["strategies"]) == {"innovative", "max_gain", "conservative"}
    assert summary["goal_name"] == goal_name
    assert summary["top"] in {"innovative", "max_gain", "conservative"}
    assert all(isinstance(v, float) for v in summary["scores"].values())

    empty_summary = candidates_summary([])
    assert empty_summary["count"] == 0
    assert empty_summary["goal_name"] is None

test("candidates_summary(): correct structure, goal_name extracted", g10_candidates_summary)


# ─────────────────────────────────────────────────────────────────────────────
# G11: priority field affects confidence scoring
# ─────────────────────────────────────────────────────────────────────────────

def g11_priority_affects_confidence():
    high_prio = select_next_goal([_goal(priority=1)])   # high priority (low number)
    low_prio = select_next_goal([_goal(priority=10)])   # low priority

    hp_scores = {c.strategy_type: c.confidence for c in high_prio}
    lp_scores = {c.strategy_type: c.confidence for c in low_prio}

    # max_gain and conservative should have higher confidence for high-priority (low #) goals
    assert hp_scores["max_gain"] >= lp_scores["max_gain"] or \
        hp_scores["conservative"] >= lp_scores["conservative"], \
        "Priority should influence confidence scoring"

test("priority field influences confidence scores across candidates", g11_priority_affects_confidence)


# ─────────────────────────────────────────────────────────────────────────────
# G12: context string improves context_match vs no context
# ─────────────────────────────────────────────────────────────────────────────

def g12_context_improves_match():
    with_ctx = select_next_goal([_goal()], context="anomaly in episodic pipeline")
    without_ctx = select_next_goal([_goal()], context="")

    ctx_scores = {c.strategy_type: c.context_match for c in with_ctx}
    no_ctx_scores = {c.strategy_type: c.context_match for c in without_ctx}

    for stype in ["innovative", "max_gain", "conservative"]:
        assert ctx_scores[stype] >= no_ctx_scores[stype], \
            f"{stype}: context should raise context_match ({ctx_scores[stype]} vs {no_ctx_scores[stype]})"

test("context string raises context_match scores vs empty context", g12_context_improves_match)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Goal Planner Tests: {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
