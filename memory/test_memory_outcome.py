from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory_outcome import append_outcome_history, clamp01, normalized_outcome, score_values


def test_clamp_and_normalized_outcome() -> None:
    assert clamp01(-1) == 0.0
    assert clamp01(2) == 1.0
    assert normalized_outcome("SUCCESS") == "success"
    assert normalized_outcome("weird") == "unknown"


def test_success_increases_utility_and_confidence() -> None:
    reward, utility, confidence, surprise = score_values(
        "success",
        old_utility=0.5,
        old_confidence=0.7,
        old_surprise=0.1,
        evidence_quality=1.0,
    )

    assert reward == 1.0
    assert utility > 0.5
    assert confidence > 0.7
    assert 0.0 <= surprise <= 1.0


def test_failure_decreases_utility_and_confidence() -> None:
    reward, utility, confidence, surprise = score_values(
        "failure",
        old_utility=0.8,
        old_confidence=0.9,
        old_surprise=0.0,
        evidence_quality=1.0,
    )

    assert reward == 0.0
    assert utility < 0.8
    assert confidence < 0.9
    assert surprise > 0.0


def test_outcome_history_is_bounded_and_records_latest() -> None:
    metadata = {"outcome_history": [{"n": i} for i in range(25)]}
    updated = append_outcome_history(
        metadata,
        "success",
        1.0,
        "tests passed",
        "ADA",
        0.8,
        0.9,
        0.2,
    )

    assert len(updated["outcome_history"]) == 20
    assert updated["last_outcome"]["outcome"] == "success"
    assert updated["last_outcome"]["evidence"] == "tests passed"
