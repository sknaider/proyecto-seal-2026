from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import pytest

from soul_embodied_runtime.audit import AuditChain
from soul_embodied_runtime.authorization import (
    AuthorizationSigner,
    AuthorizationVerifier,
    InMemoryNonceStore,
)
from soul_embodied_runtime.brain import (
    AdaHumanoidPlanner,
    AdaIdentitySnapshot,
    DualMemorySnapshot,
    MemoryAnchor,
)
from soul_embodied_runtime.contracts import (
    ActionConstraints,
    ActionIntent,
    BodySafetyProfile,
    ConsentGrant,
    Decision,
    PolicyContext,
    RiskTier,
)
from soul_embodied_runtime.humanoid import (
    G1_ARM_JOINT_LIMITS_RAD,
    G1_BOUNDED_POSE,
    G1_SIM_BODY,
    G1HumanoidSimulationAdapter,
    HumanoidPoseCommand,
    JointStateSample,
)
from soul_embodied_runtime.policy import PolicyEngine


NOW = datetime(2026, 7, 15, 22, 0, tzinfo=timezone.utc)


@dataclass
class FakeJointTransport:
    state: JointStateSample | None
    fail: bool = False
    measured_hold_error: float | None = 0.0

    def __post_init__(self) -> None:
        self.commands: list[HumanoidPoseCommand] = []
        self.holds: list[str] = []

    def latest_joint_state(self) -> JointStateSample | None:
        return self.state

    def run_pose(
        self, command: HumanoidPoseCommand, *, heartbeat_timeout_ms: int
    ) -> JointStateSample:
        assert heartbeat_timeout_ms == 500
        self.commands.append(command)
        if self.fail:
            raise RuntimeError("joint-state heartbeat lost")
        assert self.state is not None
        self.state = JointStateSample(
            {**self.state.positions, **command.joint_positions}, NOW
        )
        self.hold("bounded_pose_complete")
        return self.state

    def hold(self, reason: str) -> None:
        self.holds.append(reason)

    def hold_error_max_rad(self) -> float | None:
        return self.measured_hold_error


def fresh_state() -> JointStateSample:
    return JointStateSample(
        {
            "left_shoulder_pitch_joint": 0.0,
            "right_shoulder_pitch_joint": 0.0,
            "left_shoulder_roll_joint": 0.0,
            "right_shoulder_roll_joint": 0.0,
            "left_shoulder_yaw_joint": 0.0,
            "right_shoulder_yaw_joint": 0.0,
            "left_elbow_joint": 0.0,
            "right_elbow_joint": 0.0,
            "left_knee_joint": 0.0,
        },
        NOW - timedelta(milliseconds=20),
    )


def make_intent(
    positions: dict[str, float] | None = None, duration_ms: int = 1200
) -> ActionIntent:
    return ActionIntent(
        intent_id="g1-step-1",
        issuer="soul:agent:ada",
        body_id=G1_SIM_BODY,
        capability=G1_BOUNDED_POSE,
        goal={
            "joint_positions": positions
            or {"right_shoulder_yaw_joint": 0.3, "right_elbow_joint": 0.6},
            "duration_ms": duration_ms,
        },
        constraints=ActionConstraints(0.0, 0.0, 4_000),
        context_refs=("isaac:unitree-g1-29dof:fixed-base",),
        risk_tier=RiskTier.MOTION_LOW,
        created_at=NOW - timedelta(milliseconds=50),
        expires_at=NOW + timedelta(seconds=5),
    )


def body() -> BodySafetyProfile:
    return BodySafetyProfile(
        body_id=G1_SIM_BODY,
        capabilities=frozenset({G1_BOUNDED_POSE}),
        ceilings={G1_BOUNDED_POSE: ActionConstraints(0.0, 0.0, 4_000)},
        risk_tiers={G1_BOUNDED_POSE: RiskTier.MOTION_LOW},
        safe_state_ready=True,
        emergency_stop_ready=True,
        calibration_valid=True,
        thermal_ok=True,
        battery_ok=True,
        state_observed_at=NOW,
        max_state_age_ms=500,
    )


def context() -> PolicyContext:
    return PolicyContext(
        subject_id="william",
        mode="developer",
        now=NOW,
        user_present=True,
        consent_grants=(
            ConsentGrant(
                consent_id="william-g1-sim",
                subject_id="william",
                body_id=G1_SIM_BODY,
                mode="developer",
                capabilities=frozenset({G1_BOUNDED_POSE}),
                granted_at=NOW - timedelta(minutes=1),
                expires_at=NOW + timedelta(minutes=5),
            ),
        ),
    )


def make_adapter(intent: ActionIntent, transport: FakeJointTransport):
    decision = PolicyEngine().evaluate(intent, body(), context())
    assert decision.decision is Decision.ALLOW
    signer = AuthorizationSigner.generate("g1-test-key")
    signed = signer.issue(intent, decision, now=NOW)
    verifier = AuthorizationVerifier(
        {signer.key_id: signer.public_key_bytes()}, InMemoryNonceStore()
    )
    audit = AuditChain()
    return G1HumanoidSimulationAdapter(
        G1_SIM_BODY, verifier, transport, audit, clock=lambda: NOW
    ), signed, audit


def test_signed_bounded_arm_pose_moves_and_holds() -> None:
    intent = make_intent()
    transport = FakeJointTransport(fresh_state())
    adapter, signed, audit = make_adapter(intent, transport)
    prepared = adapter.prepare(signed, intent=intent, now=NOW)
    result = adapter.execute(prepared)
    assert result.target_error_max_rad == 0.0
    assert result.max_joint_delta_rad == pytest.approx(0.6)
    assert result.hold_verified
    assert transport.holds == ["bounded_pose_complete"]
    assert audit.verify()


@pytest.mark.parametrize(
    "positions,match",
    [
        ({"left_knee_joint": 0.1}, "upper-body whitelist"),
        ({"right_elbow_joint": 1.5}, "safe envelope"),
    ],
)
def test_legs_out_of_envelope_and_nonfinite_targets_fail_closed(
    positions: dict[str, float], match: str
) -> None:
    intent = make_intent(positions)
    adapter, signed, _ = make_adapter(intent, FakeJointTransport(fresh_state()))
    with pytest.raises((PermissionError, ValueError), match=match):
        adapter.prepare(signed, intent=intent, now=NOW)


def test_nonfinite_target_is_rejected_at_the_typed_intent_boundary() -> None:
    with pytest.raises(ValueError, match="JSON compliant"):
        make_intent({"right_elbow_joint": float("nan")})


def test_nonfinite_observed_joint_state_is_rejected_before_nonce_consumption() -> None:
    intent = make_intent()
    state = fresh_state()
    state.positions["right_elbow_joint"] = float("nan")
    transport = FakeJointTransport(state)
    adapter, signed, _ = make_adapter(intent, transport)
    with pytest.raises(RuntimeError, match="non-finite"):
        adapter.prepare(signed, intent=intent, now=NOW)
    transport.state = fresh_state()
    assert adapter.prepare(signed, intent=intent, now=NOW).startswith("prepared:")


def test_stale_state_does_not_consume_authorization_and_replay_is_denied() -> None:
    intent = make_intent()
    transport = FakeJointTransport(
        replace(fresh_state(), observed_at=NOW - timedelta(seconds=2))
    )
    adapter, signed, _ = make_adapter(intent, transport)
    with pytest.raises(RuntimeError, match="stale"):
        adapter.prepare(signed, intent=intent, now=NOW)
    transport.state = fresh_state()
    adapter.prepare(signed, intent=intent, now=NOW)
    with pytest.raises(PermissionError, match="replay_detected"):
        adapter.prepare(signed, intent=intent, now=NOW)


def test_execution_fault_forces_safe_hold() -> None:
    intent = make_intent()
    transport = FakeJointTransport(fresh_state(), fail=True)
    adapter, signed, audit = make_adapter(intent, transport)
    prepared = adapter.prepare(signed, intent=intent, now=NOW)
    with pytest.raises(RuntimeError, match="heartbeat lost"):
        adapter.execute(prepared)
    assert transport.holds == ["execution_fault"]
    assert audit.events[-1].event_type == "humanoid.safe_hold"


def test_authorization_must_cover_the_full_bounded_pose() -> None:
    intent = make_intent(duration_ms=3800)
    transport = FakeJointTransport(fresh_state())
    adapter, signed, _ = make_adapter(intent, transport)
    with pytest.raises(PermissionError, match="cannot cover"):
        adapter.prepare(
            signed, intent=intent, now=NOW + timedelta(seconds=1)
        )


def test_prepared_pose_expires_and_state_is_revalidated_before_execute() -> None:
    intent = make_intent()
    transport = FakeJointTransport(fresh_state())
    adapter, signed, _ = make_adapter(intent, transport)
    prepared = adapter.prepare(signed, intent=intent, now=NOW)
    adapter.clock = lambda: NOW + timedelta(seconds=5)
    with pytest.raises(PermissionError, match="expired"):
        adapter.execute(prepared)
    assert transport.commands == []


def test_unstable_post_pose_hold_fails_acceptance() -> None:
    intent = make_intent()
    transport = FakeJointTransport(fresh_state(), measured_hold_error=0.2)
    adapter, signed, audit = make_adapter(intent, transport)
    prepared = adapter.prepare(signed, intent=intent, now=NOW)
    with pytest.raises(RuntimeError, match="hold unstable"):
        adapter.execute(prepared)
    assert "hold_unstable" in transport.holds
    assert audit.events[-1].event_type == "humanoid.safe_hold"


def test_missing_fresh_hold_feedback_fails_acceptance() -> None:
    intent = make_intent()
    transport = FakeJointTransport(fresh_state(), measured_hold_error=None)
    adapter, signed, audit = make_adapter(intent, transport)
    prepared = adapter.prepare(signed, intent=intent, now=NOW)
    with pytest.raises(RuntimeError, match="hold was not measured"):
        adapter.execute(prepared)
    assert "execution_fault" in transport.holds
    assert audit.events[-1].event_type == "humanoid.safe_hold"


def test_g1_edge_rejects_authorization_issuer_and_capability_mismatch() -> None:
    intent = make_intent()
    transport = FakeJointTransport(fresh_state())
    adapter, signed, _ = make_adapter(intent, transport)
    wrong_issuer = replace(
        signed,
        authorization=replace(signed.authorization, issuer="soul:agent:attacker"),
    )
    with pytest.raises(PermissionError, match="issuer does not match"):
        adapter.prepare(wrong_issuer, intent=intent, now=NOW)
    wrong_capability = replace(
        signed,
        authorization=replace(
            signed.authorization, allowed_capability="humanoid.navigate"
        ),
    )
    with pytest.raises(PermissionError, match="capability does not match"):
        adapter.prepare(wrong_capability, intent=intent, now=NOW)
    assert transport.commands == []


def test_ada_humanoid_planner_excludes_emotional_memory_from_authority() -> None:
    identity = AdaIdentitySnapshot(
        name="ADA",
        role="Engineer",
        active=True,
        ocean={"o": 0.81, "c": 1.0, "e": 1.0, "a": 0.48, "n": 0.21},
        persona_axes={},
        system_prompt_digest="sha256:" + "a" * 64,
        updated_at=NOW,
    )
    memories = DualMemorySnapshot(
        operational=(MemoryAnchor(10, "decision", "sha256:" + "b" * 64, 9),),
        emotional=(MemoryAnchor(11, "emotion", "sha256:" + "c" * 64, 10),),
        operational_count=1,
        emotional_count=1,
        emotional_state="warm_stable",
    )
    planner = AdaHumanoidPlanner(
        identity, memories, AuthorizationSigner.generate("ada-g1-test")
    )
    intent, signed = planner.authorize_pose(
        planner.scenario("humanoid_wave")[0],
        now=NOW,
        session_id="g1-session",
        subject_id="william",
        user_present=True,
        edge_challenge_ref="edge-challenge:fresh",
    )
    refs = " ".join(intent.context_refs)
    assert intent.body_id == G1_SIM_BODY
    assert "soul-memory:10:" in refs
    assert "soul-memory:11:" not in refs
    assert signed.authorization.max_uses == 1
    assert signed.authorization.expires_at - signed.authorization.not_before == timedelta(seconds=4)


def test_ada_bimanual_demo_is_bounded_and_returns_to_neutral() -> None:
    identity = AdaIdentitySnapshot(
        name="ADA",
        role="Engineer",
        active=True,
        ocean={"o": 0.81, "c": 1.0, "e": 1.0, "a": 0.48, "n": 0.21},
        persona_axes={},
        system_prompt_digest="sha256:" + "a" * 64,
        updated_at=NOW,
    )
    planner = AdaHumanoidPlanner(
        identity,
        DualMemorySnapshot((), (), 0, 0, "stable"),
        AuthorizationSigner.generate("ada-g1-test"),
    )

    steps = planner.scenario("humanoid_bimanual_demo")
    commanded = {name for step in steps for name in step.joint_positions}

    assert len(steps) == 9
    assert all(1 <= len(step.joint_positions) <= 4 for step in steps)
    assert all(500 <= step.duration_ms <= 4_000 for step in steps)
    assert commanded == set(G1_ARM_JOINT_LIMITS_RAD)
    assert sum(step.duration_ms for step in steps) == 15_000
    final_targets: dict[str, float] = {}
    for step in reversed(steps):
        for name, target in step.joint_positions.items():
            final_targets.setdefault(name, target)
    assert all(final_targets[name] == 0.0 for name in commanded)


def test_ada_humanoid_planner_rejects_unknown_scenario() -> None:
    identity = AdaIdentitySnapshot(
        name="ADA",
        role="Engineer",
        active=True,
        ocean={"o": 0.81, "c": 1.0, "e": 1.0, "a": 0.48, "n": 0.21},
        persona_axes={},
        system_prompt_digest="sha256:" + "a" * 64,
        updated_at=NOW,
    )
    planner = AdaHumanoidPlanner(
        identity,
        DualMemorySnapshot((), (), 0, 0, "stable"),
        AuthorizationSigner.generate("ada-g1-test"),
    )
    with pytest.raises(ValueError, match="unsupported ADA humanoid scenario"):
        planner.scenario("humanoid_walk")
