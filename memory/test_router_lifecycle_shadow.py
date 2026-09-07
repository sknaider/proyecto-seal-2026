#!/usr/bin/env python3
"""Regression tests for router -> lifecycle shadow predictions."""

from __future__ import annotations

from datetime import datetime, timezone

from router_lifecycle_shadow import predict_from_decision


def decision(
    *,
    event_id: int,
    action: str,
    target: str | None,
    runtime: str | None,
    budget: str,
    confidence: float = 0.9,
) -> dict:
    return {
        "event_id": event_id,
        "action": action,
        "target_agent": target,
        "runtime": runtime,
        "budget_class": budget,
        "confidence": confidence,
        "reason": "fixture",
        "decided_at": datetime.now(timezone.utc),
    }


def main() -> None:
    assert predict_from_decision(
        decision(event_id=1, action="persist_only", target=None, runtime=None, budget="zero")
    ) == []

    ada = predict_from_decision(
        decision(event_id=2, action="delegate_codex", target="ADA", runtime="codex", budget="code_heavy")
    )
    assert len(ada) == 1
    assert ada[0].agent_name == "ADA"
    assert ada[0].runtime == "codex"
    assert ada[0].predicted_state == "deep_work"

    nexus = predict_from_decision(
        decision(event_id=3, action="wake_agent", target="NEXUS", runtime="claude", budget="deep")
    )
    assert len(nexus) == 1
    assert nexus[0].agent_name == "NEXUS"
    assert nexus[0].runtime == "claude"
    assert nexus[0].predicted_state == "deep_work"

    team = predict_from_decision(
        decision(event_id=4, action="wake_team", target=None, runtime="mixed", budget="normal")
    )
    assert len(team) == 5
    by_agent = {p.agent_name: p for p in team}
    assert by_agent["ADA"].runtime == "codex"
    for agent in ("JARVIS", "ALICE", "NEXUS", "DUM"):
        assert by_agent[agent].runtime == "claude"
    assert {p.predicted_state for p in team} == {"active"}

    emergency = predict_from_decision(
        decision(event_id=5, action="wake_agent", target="NEXUS", runtime="claude", budget="emergency")
    )
    assert emergency[0].predicted_state == "emergency"

    print("PASS router lifecycle shadow fixtures")


if __name__ == "__main__":
    main()
