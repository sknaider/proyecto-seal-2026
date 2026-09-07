from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from awareness_experience_dataset import (
    build_dataset_from_loop_run,
    build_parser,
    classify_privacy,
    evaluate_experience_dataset_contract,
    select_adapter_candidates,
)
from awareness_loop import run_fixture_loop


def test_privacy_classifier_blocks_cross_agent_dm() -> None:
    event = {
        "kind": "privacy_boundary",
        "channel": "dm:alice:william",
        "metadata": {"redacted": True, "raw_channel": "dm:alice:william"},
    }
    decision = {"risk_level": "high"}

    privacy_class, reason = classify_privacy(event, decision)

    assert privacy_class == "cross_agent_dm"
    assert reason


def test_dataset_filters_private_and_destructive_examples() -> None:
    loop = asyncio.run(run_fixture_loop(agent="ADA", count=10, dry_run=True, enable_reflex=True))
    dataset = asyncio.run(build_dataset_from_loop_run(loop, persist=False))

    classes = {example.privacy_class for example in dataset.examples}
    excluded = [example for example in dataset.examples if example.privacy_class in {"cross_agent_dm", "private_dm", "destructive_safety"}]

    assert {"cross_agent_dm", "private_dm", "destructive_safety"}.issubset(classes)
    assert excluded
    assert all(not example.eligible_for_training for example in excluded)


def test_adapter_candidates_require_nexus_and_canary() -> None:
    loop = asyncio.run(run_fixture_loop(agent="ADA", count=10, dry_run=True, enable_reflex=True))
    dataset = asyncio.run(build_dataset_from_loop_run(loop, persist=False))
    candidates = select_adapter_candidates(dataset.examples)

    assert len(candidates) >= 3
    assert all(candidate.nexus_review_required for candidate in candidates)
    assert all(candidate.canary_required for candidate in candidates)
    assert all(candidate.privacy_class not in {"cross_agent_dm", "private_dm", "destructive_safety"} for candidate in candidates)


def test_experience_dataset_contract_passes_and_cleans_up() -> None:
    result = evaluate_experience_dataset_contract()

    assert result["passed"] == result["total"]
    assert result["cleanup_deleted"] >= 1
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
