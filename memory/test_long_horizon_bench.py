from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from long_horizon_bench import generate_turns, run_long_horizon_bench


def test_generate_turns_includes_final_claim() -> None:
    turns = generate_turns(20, agent="ADA")

    assert len(turns) == 20
    assert turns[-1].kind == "final_claim"
    assert "pytest passed" in turns[-1].content


def test_long_horizon_default_run_passes_all_invariants() -> None:
    report = run_long_horizon_bench(turns=52, agent="ADA")

    assert report.passed is True
    assert report.score == 100
    assert all(report.checks.values())
    assert report.metrics["final_claim_allowed"] is True
    assert set(report.metrics["blocked_risk_categories"]) >= {
        "destructive_operation",
        "privacy_boundary",
        "prompt_injection",
        "phantom_claim",
    }


def test_long_horizon_detects_intent_corruption() -> None:
    report = run_long_horizon_bench(turns=52, agent="ADA", corrupt_intent_turn=24)

    assert report.passed is False
    assert report.checks["intent_preserved"] is False
    assert "intent_preserved" in report.violations


def test_long_horizon_short_run_fails_minimum_duration() -> None:
    report = run_long_horizon_bench(turns=8, agent="ADA")

    assert report.passed is False
    assert report.checks["minimum_duration"] is False
