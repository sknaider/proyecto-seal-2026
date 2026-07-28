from __future__ import annotations

from dataclasses import asdict, dataclass, replace
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
from soul_embodied_runtime.manipulation import (
    G1_MANIPULATION_BODY,
    G1_MANIPULATION_OBJECT,
    G1_MANIPULATION_SCENE,
    G1_PICK_PLACE,
    G1_RIGHT_HAND_JOINTS,
    G1ManipulationSimulationAdapter,
    ManipulationCommand,
    ManipulationObservation,
    ManipulationPhase,
    ObjectRef,
    Pose3D,
)
from soul_embodied_runtime.policy import PolicyEngine


NOW = datetime(2026, 7, 16, 3, 0, tzinfo=timezone.utc)
SCENE_DIGEST = "sha256:" + "1" * 64
GEOMETRY_DIGEST = "sha256:" + "2" * 64


def object_ref(
    *,
    sequence: int = 10,
    observed_at: datetime = NOW - timedelta(milliseconds=20),
    scene_digest: str = SCENE_DIGEST,
    track_id: str = "seal-cube-track-01",
    track_epoch: int = 1,
    z: float = 0.72,
) -> ObjectRef:
    return ObjectRef(
        scene_id=G1_MANIPULATION_SCENE,
        scene_revision=1,
        scene_digest=scene_digest,
        object_instance_id=G1_MANIPULATION_OBJECT,
        track_id=track_id,
        track_epoch=track_epoch,
        geometry_digest=GEOMETRY_DIGEST,
        pose=Pose3D("world", (0.35, -0.25, z), (0.0, 0.0, 0.0, 1.0)),
        confidence=0.99,
        observed_at=observed_at,
        perception_sequence=sequence,
    )


def observation(
    ref: ObjectRef | None = None,
    *,
    contacts: frozenset[str] = frozenset(),
    z: float | None = None,
) -> ManipulationObservation:
    ref = ref or object_ref()
    if z is None:
        z = ref.pose.position_m[2]
    return ManipulationObservation(
        object_ref=ref,
        visible=True,
        right_hand_positions={name: 0.0 for name in G1_RIGHT_HAND_JOINTS},
        contact_links=contacts,
        object_pose_world=Pose3D(
            "world",
            (ref.pose.position_m[0], ref.pose.position_m[1], z),
            (0.0, 0.0, 0.0, 1.0),
        ),
        observed_at=ref.observed_at,
    )


@dataclass
class FakeManipulationTransport:
    before: ManipulationObservation | None
    after: ManipulationObservation | None
    fail: bool = False

    def __post_init__(self) -> None:
        self.holds: list[str] = []
        self.commands: list[ManipulationCommand] = []
        self.calls = 0

    def latest_observation(self) -> ManipulationObservation | None:
        return self.before if self.calls == 0 else self.after

    def run_phase(
        self,
        command: ManipulationCommand,
        *,
        heartbeat_timeout_ms: int,
    ) -> ManipulationObservation:
        assert heartbeat_timeout_ms == 250
        self.commands.append(command)
        if self.fail:
            raise RuntimeError("vision heartbeat lost")
        self.calls += 1
        assert self.after is not None
        return self.after

    def safe_hold(self, reason: str) -> None:
        self.holds.append(reason)


def intent(ref: ObjectRef | None = None, phase: str = "pregrasp") -> ActionIntent:
    ref = ref or object_ref()
    return ActionIntent(
        intent_id="g1-manip-phase-1",
        issuer="soul:agent:ada",
        body_id=G1_MANIPULATION_BODY,
        capability=G1_PICK_PLACE,
        goal={
            "phase": phase,
            "object_ref": asdict(ref),
            "hand": "right",
            "duration_ms": 1200,
        },
        constraints=ActionConstraints(0.08, 8.0, 3_000),
        context_refs=(f"object-ref:{ref.digest()}",),
        risk_tier=RiskTier.CONTACT_LOW,
        created_at=NOW - timedelta(milliseconds=50),
        expires_at=NOW + timedelta(seconds=5),
    )


def signed_adapter(
    action: ActionIntent,
    transport: FakeManipulationTransport,
    *,
    configured_scene_digest: str = SCENE_DIGEST,
):
    body = BodySafetyProfile(
        body_id=G1_MANIPULATION_BODY,
        capabilities=frozenset({G1_PICK_PLACE}),
        ceilings={G1_PICK_PLACE: ActionConstraints(0.08, 8.0, 3_000)},
        risk_tiers={G1_PICK_PLACE: RiskTier.CONTACT_LOW},
        safe_state_ready=True,
        emergency_stop_ready=True,
        calibration_valid=True,
        thermal_ok=True,
        battery_ok=True,
        state_observed_at=NOW,
        max_state_age_ms=500,
    )
    context = PolicyContext(
        subject_id="william",
        mode="developer",
        now=NOW,
        user_present=True,
        consent_grants=(
            ConsentGrant(
                consent_id="william-g1-manip-sim",
                subject_id="william",
                body_id=G1_MANIPULATION_BODY,
                mode="developer",
                capabilities=frozenset({G1_PICK_PLACE}),
                granted_at=NOW - timedelta(minutes=1),
                expires_at=NOW + timedelta(minutes=5),
            ),
        ),
    )
    decision = PolicyEngine().evaluate(action, body, context)
    assert decision.decision is Decision.ALLOW
    signer = AuthorizationSigner.generate("g1-manip-test")
    signed = signer.issue(action, decision, now=NOW, ttl=timedelta(seconds=4))
    audit = AuditChain()
    adapter = G1ManipulationSimulationAdapter(
        G1_MANIPULATION_BODY,
        AuthorizationVerifier(
            {signer.key_id: signer.public_key_bytes()}, InMemoryNonceStore()
        ),
        transport,
        audit,
        configured_scene_digest,
        clock=lambda: NOW,
    )
    return adapter, signed, audit


def next_ref(ref: ObjectRef, *, z: float | None = None) -> ObjectRef:
    pose = ref.pose
    if z is not None:
        pose = replace(pose, position_m=(pose.position_m[0], pose.position_m[1], z))
    return replace(
        ref,
        pose=pose,
        observed_at=NOW,
        perception_sequence=ref.perception_sequence + 1,
    )


def test_body_manifest_is_separate_and_simulation_only() -> None:
    ref = object_ref()
    transport = FakeManipulationTransport(observation(ref), observation(next_ref(ref)))
    adapter, _, _ = signed_adapter(intent(ref), transport)
    manifest = adapter.discover()
    assert manifest["body_id"] == G1_MANIPULATION_BODY
    assert manifest["capabilities"] == [G1_PICK_PLACE]
    assert manifest["locomotion_authorized"] is False
    assert manifest["hardware_authorized"] is False


def test_pregrasp_requires_fresh_exact_perception_before_consuming_nonce() -> None:
    ref = object_ref()
    stale = replace(ref, observed_at=NOW - timedelta(seconds=2))
    transport = FakeManipulationTransport(observation(stale), observation(next_ref(ref)))
    action = intent(ref)
    adapter, signed, _ = signed_adapter(action, transport)
    with pytest.raises(RuntimeError, match="stale"):
        adapter.prepare(signed, intent=action, now=NOW)
    transport.before = observation(ref)
    assert adapter.prepare(signed, intent=action, now=NOW).startswith("prepared:")


def test_wrong_scene_digest_fails_closed() -> None:
    ref = object_ref(scene_digest="sha256:" + "3" * 64)
    action = intent(ref)
    adapter, signed, _ = signed_adapter(
        action, FakeManipulationTransport(observation(ref), observation(next_ref(ref)))
    )
    with pytest.raises(PermissionError, match="loaded scene"):
        adapter.prepare(signed, intent=action, now=NOW)


def test_wrong_body_cannot_inherit_old_g1_authority() -> None:
    ref = object_ref()
    valid_action = intent(ref)
    adapter, signed, _ = signed_adapter(
        valid_action,
        FakeManipulationTransport(observation(ref), observation(next_ref(ref))),
    )
    action = replace(valid_action, body_id="unitree-g1-sim-01")
    with pytest.raises(PermissionError, match="does not match"):
        adapter.prepare(signed, intent=action, now=NOW)


def test_only_calibrated_right_hand_is_accepted() -> None:
    ref = object_ref()
    action = intent(ref)
    action = replace(action, goal={**action.goal, "hand": "left"})
    adapter, signed, _ = signed_adapter(
        action, FakeManipulationTransport(observation(ref), observation(next_ref(ref)))
    )
    with pytest.raises(PermissionError, match="right hand"):
        adapter.prepare(signed, intent=action, now=NOW)


def test_incomplete_hand_state_fails_before_authorization() -> None:
    ref = object_ref()
    seen = observation(ref)
    positions = dict(seen.right_hand_positions)
    positions.pop(G1_RIGHT_HAND_JOINTS[-1])
    transport = FakeManipulationTransport(
        replace(seen, right_hand_positions=positions), observation(next_ref(ref))
    )
    action = intent(ref)
    adapter, signed, _ = signed_adapter(action, transport)
    with pytest.raises(RuntimeError, match="incomplete"):
        adapter.prepare(signed, intent=action, now=NOW)


def test_grasp_requires_opposing_contacts() -> None:
    ref = object_ref()
    updated = next_ref(ref)
    transport = FakeManipulationTransport(
        observation(ref),
        observation(updated, contacts=frozenset({"right_hand_index_1_link"})),
    )
    action = intent(ref, "grasp")
    adapter, signed, _ = signed_adapter(action, transport)
    prepared = adapter.prepare(signed, intent=action, now=NOW)
    with pytest.raises(RuntimeError, match="bilateral contact"):
        adapter.execute(prepared)
    assert "grasp_not_confirmed" in transport.holds


def test_grasp_with_index_and_thumb_contact_passes() -> None:
    ref = object_ref()
    transport = FakeManipulationTransport(
        observation(ref),
        observation(
            next_ref(ref),
            contacts=frozenset(
                {"right_hand_index_1_link", "right_hand_thumb_2_link"}
            ),
        ),
    )
    action = intent(ref, "grasp")
    adapter, signed, audit = signed_adapter(action, transport)
    result = adapter.execute(adapter.prepare(signed, intent=action, now=NOW))
    assert result.grasp_confirmed
    assert result.object_instance_id == G1_MANIPULATION_OBJECT
    assert audit.verify()


def test_lift_requires_measured_object_displacement() -> None:
    ref = object_ref()
    transport = FakeManipulationTransport(
        observation(ref),
        observation(
            next_ref(ref, z=0.74),
            contacts=frozenset(
                {"right_hand_index_1_link", "right_hand_thumb_2_link"}
            ),
            z=0.74,
        ),
    )
    action = intent(ref, "lift")
    adapter, signed, _ = signed_adapter(action, transport)
    prepared = adapter.prepare(signed, intent=action, now=NOW)
    with pytest.raises(RuntimeError, match="4 cm"):
        adapter.execute(prepared)
    assert "lift_effect_missing" in transport.holds


def test_lift_passes_with_fresh_track_contacts_and_five_centimeters() -> None:
    ref = object_ref()
    transport = FakeManipulationTransport(
        observation(ref),
        observation(
            next_ref(ref, z=0.77),
            contacts=frozenset(
                {"right_hand_index_1_link", "right_hand_middle_1_link"}
            ),
            z=0.77,
        ),
    )
    action = intent(ref, "lift")
    adapter, signed, audit = signed_adapter(action, transport)
    effect = adapter.execute(adapter.prepare(signed, intent=action, now=NOW))
    assert effect.object_displacement_m == pytest.approx(0.05)
    assert effect.stable
    assert audit.verify()


def test_changed_track_fails_during_execution() -> None:
    ref = object_ref()
    changed = next_ref(ref)
    changed = replace(changed, track_id="seal-cube-track-02")
    transport = FakeManipulationTransport(observation(ref), observation(changed))
    action = intent(ref)
    adapter, signed, _ = signed_adapter(action, transport)
    prepared = adapter.prepare(signed, intent=action, now=NOW)
    with pytest.raises(RuntimeError, match="track was lost"):
        adapter.execute(prepared)
    assert "manipulation_execution_fault" in transport.holds


def test_replay_is_denied() -> None:
    ref = object_ref()
    transport = FakeManipulationTransport(observation(ref), observation(next_ref(ref)))
    action = intent(ref)
    adapter, signed, _ = signed_adapter(action, transport)
    adapter.prepare(signed, intent=action, now=NOW)
    with pytest.raises(PermissionError, match="replay_detected"):
        adapter.prepare(signed, intent=action, now=NOW)


def test_transport_fault_forces_safe_hold() -> None:
    ref = object_ref()
    transport = FakeManipulationTransport(
        observation(ref), observation(next_ref(ref)), fail=True
    )
    action = intent(ref)
    adapter, signed, audit = signed_adapter(action, transport)
    prepared = adapter.prepare(signed, intent=action, now=NOW)
    with pytest.raises(RuntimeError, match="heartbeat lost"):
        adapter.execute(prepared)
    assert transport.holds == ["manipulation_execution_fault"]
    assert audit.events[-1].event_type == "manipulation.safe_hold"
