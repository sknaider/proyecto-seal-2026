#!/usr/bin/env python3
"""Regression tests for cognitive lifecycle sync."""

from __future__ import annotations

from datetime import datetime, timezone

from cognitive_lifecycle_sync import reduce_predictions


def pred(
    *,
    event_id: int,
    agent: str,
    state: str,
    runtime: str,
    budget: str,
    confidence: float = 0.8,
) -> dict:
    return {
        "event_id": event_id,
        "agent_name": agent,
        "predicted_state": state,
        "runtime": runtime,
        "budget_class": budget,
        "router_action": "wake_agent",
        "confidence": confidence,
        "reason": "fixture",
        "shadow_created_at": datetime.now(timezone.utc),
    }


def by_agent(states):
    return {s.agent_name: s for s in states}


def main() -> None:
    states = reduce_predictions([])
    agents = by_agent(states)
    assert set(agents) == {"ADA", "JARVIS", "ALICE", "NEXUS", "DUM"}
    assert {s.state for s in states} == {"standby"}
    assert agents["ADA"].runtime == "codex"
    for agent in ("JARVIS", "ALICE", "NEXUS", "DUM"):
        assert agents[agent].runtime == "claude"

    states = reduce_predictions(
        [
            pred(event_id=1, agent="ADA", state="active", runtime="codex", budget="normal", confidence=0.9),
            pred(event_id=2, agent="ADA", state="deep_work", runtime="codex", budget="code_heavy", confidence=0.7),
            pred(event_id=3, agent="NEXUS", state="emergency", runtime="claude", budget="emergency", confidence=0.6),
            pred(event_id=4, agent="NEXUS", state="deep_work", runtime="claude", budget="deep", confidence=0.95),
        ]
    )
    agents = by_agent(states)
    assert agents["ADA"].state == "deep_work"
    assert agents["ADA"].source_event_id == 2
    assert agents["NEXUS"].state == "emergency"
    assert agents["NEXUS"].source_event_id == 3

    states = reduce_predictions(
        [
            pred(event_id=5, agent="ALICE", state="active", runtime="codex", budget="normal", confidence=1.0),
            pred(event_id=6, agent="ALICE", state="active", runtime="claude", budget="normal", confidence=0.7),
        ]
    )
    agents = by_agent(states)
    assert agents["ALICE"].runtime == "claude"
    assert agents["ALICE"].source_event_id == 6

    print("PASS cognitive lifecycle sync fixtures")


if __name__ == "__main__":
    main()
