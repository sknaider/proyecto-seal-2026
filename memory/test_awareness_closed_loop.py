from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from awareness_closed_loop import (
    build_closed_loop_from_dataset,
    build_closed_loop_outcomes,
    build_learning_schedule,
    build_parser,
    evaluate_closed_loop_contract,
)
from awareness_experience_dataset import build_dataset_from_loop_run
from awareness_loop import run_fixture_loop


def _dataset():
    loop = asyncio.run(run_fixture_loop(agent="ADA", count=10, dry_run=True, enable_reflex=True))
    return asyncio.run(build_dataset_from_loop_run(loop, persist=False))


def test_closed_loop_builds_outcomes_for_each_example() -> None:
    dataset = _dataset()
    outcomes = build_closed_loop_outcomes(dataset)

    assert len(outcomes) == len(dataset.examples)
    assert any(outcome.memory_update_required for outcome in outcomes)
    assert all(outcome.status == "pending_nexus_review" for outcome in outcomes)


def test_closed_loop_contains_promote_rollback_and_guardrail() -> None:
    outcomes = build_closed_loop_outcomes(_dataset())
    decisions = {outcome.promotion_decision for outcome in outcomes}

    assert "promote_pending_nexus" in decisions
    assert "rollback_pending_nexus" in decisions
    assert any(outcome.guardrail_candidate for outcome in outcomes)
    assert any(outcome.regression_test_candidate for outcome in outcomes)


def test_learning_schedule_is_weekly_shadow_only() -> None:
    schedule = build_learning_schedule("ADA")

    assert schedule.agent == "ADA"
    assert schedule.cadence == "weekly"
    assert schedule.benchmark_suite == "awareness_closed_loop"
    assert schedule.status == "scheduled_shadow_only"
    assert schedule.nexus_review_required is True


def test_closed_loop_dry_run_does_not_persist() -> None:
    closed = asyncio.run(build_closed_loop_from_dataset(_dataset(), persist=False))

    assert closed.persisted is False
    assert all(outcome.db_id is None for outcome in closed.outcomes)
    assert closed.schedule.db_id is None
    assert closed.boundary == "closed_loop_measured_no_auto_training_no_auto_promotion"


def test_closed_loop_contract_passes_and_cleans_up() -> None:
    result = evaluate_closed_loop_contract()

    assert result["passed"] == result["total"]
    assert result["cleanup_deleted"] >= 1
    assert result["experience_cleanup_deleted"] >= 1
    assert result["awareness_cleanup_deleted"] >= 1


def test_cli_parser_accepts_fixture_and_contract() -> None:
    parser = build_parser()

    fixture = parser.parse_args(["--agent", "ADA", "--persist", "fixture", "--count", "3"])
    contract = parser.parse_args(["contract"])

    assert fixture.agent == "ADA"
    assert fixture.persist is True
    assert fixture.command == "fixture"
    assert fixture.count == 3
    assert contract.command == "contract"
