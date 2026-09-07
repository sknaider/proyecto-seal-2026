from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from causal_chain import CHAIN_STAGES, CausalEventInput, default_chain, validate_chain


def test_default_chain_has_required_stages() -> None:
    chain = default_chain("test-correlation")
    complete, stages = validate_chain(chain)

    assert complete is True
    assert tuple(stages) == CHAIN_STAGES


def test_validate_chain_rejects_missing_stage() -> None:
    chain = [
        CausalEventInput("order", "order"),
        CausalEventInput("plan", "plan"),
        CausalEventInput("outcome", "outcome"),
    ]
    complete, stages = validate_chain(chain)

    assert complete is False
    assert stages == ["order", "plan", "outcome"]


def test_default_chain_has_artifact_and_outcome_evidence() -> None:
    chain = default_chain("abc")
    artifact = next(event for event in chain if event.stage == "artifact")
    outcome = next(event for event in chain if event.stage == "outcome")

    assert artifact.artifact_path == "memory/causal_chain.py"
    assert outcome.outcome == "success"
