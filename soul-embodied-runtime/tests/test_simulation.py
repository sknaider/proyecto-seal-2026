from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from soul_embodied_runtime.audit import AuditChain
from soul_embodied_runtime.authorization import (
    AuthorizationSigner,
    AuthorizationVerifier,
    InMemoryNonceStore,
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
from soul_embodied_runtime.policy import PolicyEngine
from soul_embodied_runtime.simulation import (
    NOVA_TIMED_DRIVE,
    NovaCarterSimulationAdapter,
    OdometrySample,
    TimedDriveCommand,
)
from soul_embodied_runtime.wire import parse_plan_bundle, plan_bundle


NOW = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)
BODY_ID = "nova-carter-sim-01"


@dataclass
class FakeVelocityTransport:
    odometry: OdometrySample | None
    fail_motion: bool = False

    def __post_init__(self) -> None:
        self.commands: list[TimedDriveCommand] = []
        self.stops: list[str] = []

    def latest_odometry(self) -> OdometrySample | None:
        return self.odometry

    def run_velocity(
        self,
        command: TimedDriveCommand,
        *,
        heartbeat_timeout_ms: int,
    ) -> OdometrySample:
        assert heartbeat_timeout_ms == 500
        self.commands.append(command)
        if self.fail_motion:
            raise RuntimeError("odometry heartbeat lost")
        assert self.odometry is not None
        self.odometry = OdometrySample(
            self.odometry.x + command.linear_m_s * command.duration_ms / 1000,
            self.odometry.y,
            self.odometry.yaw + command.angular_rad_s * command.duration_ms / 1000,
            NOW,
        )
        self.stop("bounded_action_complete")
        return self.odometry

    def stop(self, reason: str) -> None:
        self.stops.append(reason)


def make_intent(**goal_changes) -> ActionIntent:
    goal = {"linear_m_s": 0.2, "angular_rad_s": 0.1, "duration_ms": 1000}
    goal.update(goal_changes)
    return ActionIntent(
        intent_id="brain-plan-step-1",
        issuer="soul:agent:ada",
        body_id=BODY_ID,
        capability=NOVA_TIMED_DRIVE,
        goal=goal,
        constraints=ActionConstraints(0.25, 0.0, 2_000),
        context_refs=("isaac-scene:nova-carter",),
        risk_tier=RiskTier.MOTION_LOW,
        created_at=NOW - timedelta(milliseconds=50),
        expires_at=NOW + timedelta(seconds=5),
    )


def make_body() -> BodySafetyProfile:
    return BodySafetyProfile(
        body_id=BODY_ID,
        capabilities=frozenset({NOVA_TIMED_DRIVE}),
        ceilings={NOVA_TIMED_DRIVE: ActionConstraints(0.3, 0.0, 3_000)},
        risk_tiers={NOVA_TIMED_DRIVE: RiskTier.MOTION_LOW},
        safe_state_ready=True,
        emergency_stop_ready=True,
        calibration_valid=True,
        thermal_ok=True,
        battery_ok=True,
        state_observed_at=NOW - timedelta(milliseconds=20),
    )


def make_context() -> PolicyContext:
    consent = ConsentGrant(
        consent_id="william-sim-consent",
        subject_id="william",
        body_id=BODY_ID,
        mode="developer",
        capabilities=frozenset({NOVA_TIMED_DRIVE}),
        granted_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        purposes=frozenset({"robot_simulation"}),
    )
    return PolicyContext(
        subject_id="william",
        mode="developer",
        now=NOW,
        consent_grants=(consent,),
        user_present=True,
    )


def make_adapter(intent: ActionIntent, transport: FakeVelocityTransport):
    decision = PolicyEngine().evaluate(intent, make_body(), make_context())
    assert decision.decision is Decision.ALLOW
    signer = AuthorizationSigner.generate("simulation-test-key")
    signed = signer.issue(intent, decision, now=NOW)
    verifier = AuthorizationVerifier(
        {signer.key_id: signer.public_key_bytes()}, InMemoryNonceStore()
    )
    audit = AuditChain()
    adapter = NovaCarterSimulationAdapter(
        BODY_ID, verifier, transport, audit, clock=lambda: NOW
    )
    return adapter, signed, audit


def fresh_odometry() -> OdometrySample:
    return OdometrySample(1.0, 2.0, 0.0, NOW - timedelta(milliseconds=20))


def test_authorized_brain_intent_moves_simulation_and_stops() -> None:
    intent = make_intent()
    transport = FakeVelocityTransport(fresh_odometry())
    adapter, signed, audit = make_adapter(intent, transport)
    prepared = adapter.prepare(signed, intent=intent, now=NOW)
    result = adapter.execute(prepared)
    assert result.displacement_m == pytest.approx(0.2)
    assert transport.commands == [TimedDriveCommand(0.2, 0.1, 1000)]
    assert transport.stops == ["bounded_action_complete"]
    assert [event.event_type for event in audit.events] == [
        "simulation.prepared",
        "simulation.completed",
    ]
    assert audit.verify()


def test_stale_odometry_fails_before_consuming_authorization() -> None:
    intent = make_intent()
    transport = FakeVelocityTransport(
        OdometrySample(0.0, 0.0, 0.0, NOW - timedelta(seconds=2))
    )
    adapter, signed, _ = make_adapter(intent, transport)
    with pytest.raises(RuntimeError, match="stale"):
        adapter.prepare(signed, intent=intent, now=NOW)
    transport.odometry = fresh_odometry()
    assert adapter.prepare(signed, intent=intent, now=NOW).startswith("prepared:")


def test_small_callback_clock_skew_is_tolerated_but_large_drift_is_denied() -> None:
    intent = make_intent()
    transport = FakeVelocityTransport(
        OdometrySample(0.0, 0.0, 0.0, NOW + timedelta(milliseconds=50))
    )
    adapter, signed, _ = make_adapter(intent, transport)
    assert adapter.prepare(signed, intent=intent, now=NOW).startswith("prepared:")

    drifted = FakeVelocityTransport(
        OdometrySample(0.0, 0.0, 0.0, NOW + timedelta(milliseconds=500))
    )
    adapter, signed, _ = make_adapter(intent, drifted)
    with pytest.raises(RuntimeError, match="age_ms=-500.0"):
        adapter.prepare(signed, intent=intent, now=NOW)


def test_wrong_payload_and_out_of_bounds_commands_are_denied() -> None:
    malformed = make_intent(extra="not-allowed")
    adapter, signed, _ = make_adapter(malformed, FakeVelocityTransport(fresh_odometry()))
    with pytest.raises(ValueError, match="invalid payload"):
        adapter.prepare(signed, intent=malformed, now=NOW)

    excessive = make_intent(linear_m_s=0.29)
    adapter, signed, _ = make_adapter(excessive, FakeVelocityTransport(fresh_odometry()))
    with pytest.raises(PermissionError, match="signed authorization"):
        adapter.prepare(signed, intent=excessive, now=NOW)


def test_authorization_is_one_shot_at_simulation_edge() -> None:
    intent = make_intent()
    adapter, signed, _ = make_adapter(intent, FakeVelocityTransport(fresh_odometry()))
    adapter.prepare(signed, intent=intent, now=NOW)
    with pytest.raises(PermissionError, match="replay_detected"):
        adapter.prepare(signed, intent=intent, now=NOW)


def test_heartbeat_fault_forces_zero_velocity_stop() -> None:
    intent = make_intent()
    transport = FakeVelocityTransport(fresh_odometry(), fail_motion=True)
    adapter, signed, audit = make_adapter(intent, transport)
    prepared = adapter.prepare(signed, intent=intent, now=NOW)
    with pytest.raises(RuntimeError, match="heartbeat lost"):
        adapter.execute(prepared)
    assert transport.stops == ["execution_fault"]
    assert audit.events[-1].event_type == "simulation.safe_stop"


def test_cancel_preempts_prepared_motion() -> None:
    intent = make_intent()
    transport = FakeVelocityTransport(fresh_odometry())
    adapter, signed, _ = make_adapter(intent, transport)
    prepared = adapter.prepare(signed, intent=intent, now=NOW)
    adapter.cancel(prepared, "operator_stop")
    with pytest.raises(RuntimeError, match="cancelled"):
        adapter.execute(prepared)
    assert transport.stops == ["operator_stop"]


def test_edge_rejects_capability_or_issuer_mismatch_before_motion() -> None:
    from dataclasses import replace

    intent = make_intent()
    transport = FakeVelocityTransport(fresh_odometry())
    adapter, signed, _ = make_adapter(intent, transport)
    wrong_capability = replace(
        signed,
        authorization=replace(
            signed.authorization, allowed_capability="mobility.navigate"
        ),
    )
    with pytest.raises(PermissionError, match="capability does not match"):
        adapter.prepare(wrong_capability, intent=intent, now=NOW)
    wrong_issuer = replace(
        signed,
        authorization=replace(signed.authorization, issuer="soul:agent:attacker"),
    )
    with pytest.raises(PermissionError, match="issuer does not match"):
        adapter.prepare(wrong_issuer, intent=intent, now=NOW)
    assert transport.commands == []


def test_edge_rejects_authorization_lease_over_five_seconds() -> None:
    from dataclasses import replace

    intent = replace(make_intent(), expires_at=NOW + timedelta(seconds=20))
    decision = PolicyEngine().evaluate(intent, make_body(), make_context())
    signer = AuthorizationSigner.generate("simulation-test-key")
    signed = signer.issue(intent, decision, now=NOW, ttl=timedelta(seconds=6))
    verifier = AuthorizationVerifier(
        {signer.key_id: signer.public_key_bytes()}, InMemoryNonceStore()
    )
    adapter = NovaCarterSimulationAdapter(
        BODY_ID, verifier, FakeVelocityTransport(fresh_odometry()), AuditChain()
    )
    with pytest.raises(PermissionError, match="lease exceeds"):
        adapter.prepare(signed, intent=intent, now=NOW)


def test_authorization_must_cover_the_full_timed_drive() -> None:
    intent = make_intent(duration_ms=1800)
    decision = PolicyEngine().evaluate(intent, make_body(), make_context())
    signer = AuthorizationSigner.generate("simulation-test-key")
    signed = signer.issue(intent, decision, now=NOW, ttl=timedelta(seconds=2))
    verifier = AuthorizationVerifier(
        {signer.key_id: signer.public_key_bytes()}, InMemoryNonceStore()
    )
    adapter = NovaCarterSimulationAdapter(
        BODY_ID,
        verifier,
        FakeVelocityTransport(fresh_odometry()),
        AuditChain(),
        clock=lambda: NOW,
    )
    with pytest.raises(PermissionError, match="cannot cover timed drive"):
        adapter.prepare(signed, intent=intent, now=NOW)


def test_prepared_drive_expires_and_odometry_is_revalidated_before_execute() -> None:
    current = [NOW]
    intent = make_intent()
    transport = FakeVelocityTransport(fresh_odometry())
    adapter, signed, audit = make_adapter(intent, transport)
    adapter.clock = lambda: current[0]
    prepared = adapter.prepare(signed, intent=intent, now=NOW)
    current[0] = NOW + timedelta(seconds=4)
    with pytest.raises(PermissionError, match="expired before execution"):
        adapter.execute(prepared)
    assert transport.commands == []
    assert audit.events[-1].event_type == "simulation.execution_denied"

    intent = make_intent()
    transport = FakeVelocityTransport(fresh_odometry())
    adapter, signed, _ = make_adapter(intent, transport)
    prepared = adapter.prepare(signed, intent=intent, now=NOW)
    transport.odometry = OdometrySample(0.0, 0.0, 0.0, NOW - timedelta(seconds=2))
    with pytest.raises(RuntimeError, match="stale"):
        adapter.execute(prepared)
    assert transport.commands == []


def test_nonfinite_odometry_fails_before_consuming_authorization() -> None:
    intent = make_intent()
    transport = FakeVelocityTransport(
        OdometrySample(float("nan"), 0.0, 0.0, NOW)
    )
    adapter, signed, _ = make_adapter(intent, transport)
    with pytest.raises(RuntimeError, match="non-finite"):
        adapter.prepare(signed, intent=intent, now=NOW)
    transport.odometry = fresh_odometry()
    assert adapter.prepare(signed, intent=intent, now=NOW).startswith("prepared:")


def test_brain_edge_wire_roundtrip_and_strict_schema() -> None:
    intent = make_intent()
    decision = PolicyEngine().evaluate(intent, make_body(), make_context())
    signer = AuthorizationSigner.generate("ada-sim-key")
    signed = signer.issue(intent, decision, now=NOW)
    bundle = plan_bundle(
        identity_digest="sha256:" + "a" * 64,
        session_id="session-1",
        issued_at=NOW,
        steps=[(intent, signed)],
    )
    parsed = parse_plan_bundle(bundle)
    parsed_intent, parsed_signed = parsed["steps"][0]
    assert parsed_intent.digest() == intent.digest()
    verifier = AuthorizationVerifier(
        {signer.key_id: signer.public_key_bytes()}, InMemoryNonceStore()
    )
    assert verifier.verify_and_consume(
        parsed_signed,
        expected_body_id=BODY_ID,
        expected_intent_hash=parsed_intent.digest(),
        now=NOW,
    ) == (True, "authorized")

    bundle["unexpected"] = True
    with pytest.raises(ValueError, match="bundle fields"):
        parse_plan_bundle(bundle)


def test_wire_rejects_spoofed_agent_identity() -> None:
    intent = make_intent()
    decision = PolicyEngine().evaluate(intent, make_body(), make_context())
    signer = AuthorizationSigner.generate("ada-sim-key")
    bundle = plan_bundle(
        identity_digest="sha256:" + "b" * 64,
        session_id="session-1",
        issued_at=NOW,
        steps=[(intent, signer.issue(intent, decision, now=NOW))],
    )
    bundle["agent"] = "ATTACKER"
    with pytest.raises(PermissionError, match="not issued by ADA"):
        parse_plan_bundle(bundle)
