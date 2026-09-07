#!/usr/bin/env python3
"""Regression tests for Executive Event Router dry-run rules."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

from executive_event_router import route_event

REPO_ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_DRYRUN = REPO_ROOT / "agents/ADA/executive_router_dryrun_24h_20260518_194442.json"


def row(
    *,
    event_id: int,
    sender: str,
    content: str,
    channel: str = "web_chat",
    message_type: str = "conversation",
    metadata: dict | None = None,
) -> dict:
    return {
        "id": event_id,
        "sender_name": sender,
        "channel": channel,
        "message_type": message_type,
        "content": content,
        "metadata": metadata or {"to": "equipo"},
        "created_at": datetime.now(timezone.utc),
    }


def assert_route(
    name: str,
    event: dict,
    *,
    action: str,
    target: str | None,
    budget: str,
    runtime: str | None = None,
    min_confidence: float | None = None,
) -> None:
    decision = route_event(event)
    assert decision.action == action, f"{name}: action {decision.action} != {action}"
    assert decision.target_agent == target, f"{name}: target {decision.target_agent} != {target}"
    assert decision.budget_class == budget, f"{name}: budget {decision.budget_class} != {budget}"
    if runtime is not None:
        assert decision.runtime == runtime, f"{name}: runtime {decision.runtime} != {runtime}"
    if min_confidence is not None:
        assert decision.confidence >= min_confidence, f"{name}: confidence {decision.confidence} < {min_confidence}"


def historical_row(decision: dict) -> dict:
    return {
        "id": decision["event_id"],
        "sender_name": decision["sender"],
        "channel": decision["channel"],
        "message_type": decision["message_type"],
        "content": decision["content_preview"],
        "metadata": {"to": "equipo"},
        "created_at": decision["created_at"],
    }


def assert_historical_fixture_set() -> None:
    payload = json.loads(HISTORICAL_DRYRUN.read_text(encoding="utf-8"))
    critical_ids = {71850, 71870, 71883, 71937, 71961}
    fixture_decisions = payload["decisions"][:80]
    seen_ids = {d["event_id"] for d in fixture_decisions}
    fixture_decisions.extend(d for d in payload["decisions"] if d["event_id"] in critical_ids - seen_ids)
    rows = [historical_row(d) for d in fixture_decisions]
    assert len(rows) >= 50, f"historical fixture too small: {len(rows)}"

    routed = [route_event(r) for r in rows]
    assert len(routed) >= 50

    for decision in routed:
        if decision.action == "delegate_codex":
            assert decision.target_agent == "ADA", f"only ADA can delegate to Codex: {decision}"
            assert decision.runtime == "codex", f"Codex delegate must use codex runtime: {decision}"
        if decision.target_agent == "ADA":
            assert decision.runtime == "codex", f"ADA must use Codex runtime: {decision}"
        if decision.target_agent in {"ALICE", "JARVIS", "NEXUS", "DUM"}:
            assert decision.runtime in {None, "claude"}, f"siblings must stay on Claude: {decision}"
        if decision.action == "wake_team":
            assert decision.runtime == "mixed", f"team wake must use mixed runtime: {decision}"
        assert decision.budget_class in {"zero", "cheap", "normal", "deep", "code_heavy", "emergency"}
        assert decision.reason

    expected = {
        71850: ("persist_only", None, "zero", 0.95),
        71870: ("delegate_codex", "ADA", "code_heavy", 0.8),
        71883: ("wake_agent", "JARVIS", "normal", 0.75),
        71937: ("wake_team", None, "normal", 0.8),
        71961: ("wake_team", None, "normal", 0.8),
    }
    by_id = {d.event_id: d for d in routed}
    for event_id, (action, target, budget, min_confidence) in expected.items():
        decision = by_id[event_id]
        assert decision.action == action, f"{event_id}: action {decision.action} != {action}"
        assert decision.target_agent == target, f"{event_id}: target {decision.target_agent} != {target}"
        assert decision.budget_class == budget, f"{event_id}: budget {decision.budget_class} != {budget}"
        assert decision.confidence >= min_confidence, f"{event_id}: confidence {decision.confidence} < {min_confidence}"


def main() -> None:
    assert_route(
        "diagnostic heartbeat persists only",
        row(event_id=1, sender="ALICE", channel="seal_diagnostic", message_type="hb", content="[HB] ALICE alive"),
        action="persist_only",
        target=None,
        budget="zero",
    )
    assert_route(
        "nerves check-in persists only",
        row(event_id=2, sender="JARVIS", channel="dm:alice:jarvis", message_type="nerves_fire", content="[NERVES/JARVIS] ALICE, como vas?"),
        action="persist_only",
        target=None,
        budget="zero",
    )
    assert_route(
        "William direct ADA code task delegates to Codex",
        row(event_id=3, sender="William", content="ada corrige el bug del backend"),
        action="delegate_codex",
        target="ADA",
        budget="code_heavy",
        runtime="codex",
    )
    assert_route(
        "William direct ALICE UI call wakes ALICE",
        row(event_id=4, sender="William", content="alice muestrame la app"),
        action="wake_agent",
        target="ALICE",
        budget="normal",
        runtime="claude",
    )
    assert_route(
        "William direct ALICE code task stays on Claude",
        row(event_id=12, sender="William", content="alice corrige el frontend"),
        action="wake_agent",
        target="ALICE",
        budget="code_heavy",
        runtime="claude",
    )
    assert_route(
        "William general question defaults to JARVIS",
        row(event_id=5, sender="William", content="que novedades"),
        action="wake_team",
        target=None,
        budget="normal",
        runtime="mixed",
        min_confidence=0.8,
    )
    assert_route(
        "agent response to William persists only",
        row(event_id=6, sender="NEXUS", content="Estado verde", metadata={"to": "William"}),
        action="persist_only",
        target=None,
        budget="zero",
    )
    assert_route(
        "scheduled research loop persists with high confidence",
        row(
            event_id=7,
            sender="SEAL_SYSTEM",
            content="[RESEARCH LOOP] Si no hay trabajo urgente del equipo, investiga mejoras para SOUL usando WebSearch.",
        ),
        action="persist_only",
        target=None,
        budget="zero",
        min_confidence=0.95,
    )
    assert_route(
        "William implementation request without target goes to ADA Codex",
        row(event_id=8, sender="William", content="[Matrix] ok arregla"),
        action="delegate_codex",
        target="ADA",
        budget="code_heavy",
        runtime="codex",
        min_confidence=0.8,
    )
    assert_route(
        "William no-target security issue routes to NEXUS Claude",
        row(
            event_id=13,
            sender="William",
            content="[Matrix] DB password hardcoded, WS acepta username sin verify, error de token",
        ),
        action="wake_agent",
        target="NEXUS",
        budget="deep",
        runtime="claude",
        min_confidence=0.8,
    )
    assert_route(
        "William team coordination wakes team",
        row(event_id=9, sender="William", content="[Matrix] cada uno haga una tarea sin pisarse y luego se juntan"),
        action="wake_team",
        target=None,
        budget="normal",
        runtime="mixed",
        min_confidence=0.8,
    )
    assert_route(
        "William model comparison routes to JARVIS",
        row(event_id=10, sender="William", content="[Matrix] codex mejor que antropic?"),
        action="wake_agent",
        target="JARVIS",
        budget="normal",
        runtime="claude",
        min_confidence=0.75,
    )
    assert_route(
        "William progress question wakes team",
        row(event_id=11, sender="William", content="[Matrix] como compruebo el trabajo?"),
        action="wake_team",
        target=None,
        budget="normal",
        runtime="mixed",
        min_confidence=0.8,
    )
    assert_historical_fixture_set()
    print("PASS executive event router fixtures")


if __name__ == "__main__":
    main()
