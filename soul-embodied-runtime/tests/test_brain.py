from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from soul_embodied_runtime.authorization import AuthorizationSigner
from soul_embodied_runtime.brain import (
    AdaEmbodiedPlanner,
    AdaIdentitySnapshot,
    DualMemorySnapshot,
    MemoryAnchor,
)


NOW = datetime(2026, 7, 15, 21, 0, tzinfo=timezone.utc)


def identity(**changes) -> AdaIdentitySnapshot:
    values = dict(
        name="ADA",
        role="Engineer",
        active=True,
        ocean={"o": 0.81, "c": 1.0, "e": 1.0, "a": 0.48, "n": 0.21},
        persona_axes={"evidence_demand": 0.8},
        system_prompt_digest="sha256:" + "a" * 64,
        updated_at=NOW,
    )
    values.update(changes)
    return AdaIdentitySnapshot(**values)


def memories() -> DualMemorySnapshot:
    return DualMemorySnapshot(
        operational=(MemoryAnchor(10, "decision", "sha256:" + "b" * 64, 9),),
        emotional=(MemoryAnchor(11, "emotion", "sha256:" + "c" * 64, 10),),
        operational_count=100,
        emotional_count=20,
        emotional_state="warm_stable",
    )


def planner(**identity_changes) -> AdaEmbodiedPlanner:
    return AdaEmbodiedPlanner(
        identity(**identity_changes),
        memories(),
        AuthorizationSigner.generate("ada-live-test"),
    )


def test_only_active_canonical_ada_identity_can_plan() -> None:
    with pytest.raises(PermissionError, match="active ADA"):
        planner(name="ATTACKER")
    with pytest.raises(PermissionError, match="active ADA"):
        planner(active=False)


def test_emotional_memory_never_enters_physical_context_refs() -> None:
    p = planner()
    intent, _ = p.authorize_step(
        p.scenario("visible_demo")[0],
        now=NOW,
        session_id="session-1",
        subject_id="william",
        user_present=True,
    )
    refs = " ".join(intent.context_refs)
    assert "soul-memory:10:" in refs
    assert "soul-memory:11:" not in refs
    assert p.presence()["emotional_state"] == "warm_stable"


def test_absent_user_denies_motion() -> None:
    p = planner()
    with pytest.raises(PermissionError, match="required_user_not_present"):
        p.authorize_step(
            p.scenario("presence_patrol")[0],
            now=NOW,
            session_id="session-1",
            subject_id="william",
            user_present=False,
        )


def test_live_ada_authorization_is_short_body_bound_and_one_shot() -> None:
    p = planner()
    intent, signed = p.authorize_step(
        p.scenario("visible_demo")[0],
        now=NOW,
        session_id="session-1",
        subject_id="william",
        user_present=True,
    )
    auth = signed.authorization
    assert intent.issuer == "soul:agent:ada"
    assert auth.body_id == "nova-carter-sim-01"
    assert auth.max_uses == 1
    assert auth.expires_at - auth.not_before == timedelta(seconds=4)


def test_edge_challenge_is_bound_into_signed_intent() -> None:
    p = planner()
    intent, signed = p.authorize_step(
        p.scenario("visible_demo")[0],
        now=NOW,
        session_id="session-challenge",
        subject_id="william",
        user_present=True,
        edge_challenge_ref="edge-challenge:fresh-token",
    )
    assert "edge-challenge:fresh-token" in intent.context_refs
    assert signed.authorization.intent_hash == intent.digest()


def test_malformed_edge_challenge_fails_closed() -> None:
    p = planner()
    with pytest.raises(ValueError, match="challenge"):
        p.authorize_step(
            p.scenario("visible_demo")[0],
            now=NOW,
            session_id="session-challenge",
            subject_id="william",
            user_present=True,
            edge_challenge_ref="not-an-edge-challenge",
        )
