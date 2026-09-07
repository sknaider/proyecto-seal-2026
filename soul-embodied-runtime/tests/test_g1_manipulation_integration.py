from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from soul_embodied_runtime.authorization import AuthorizationSigner
from soul_embodied_runtime.brain import (
    AdaIdentitySnapshot,
    AdaManipulationPlanner,
    DualMemorySnapshot,
)
from soul_embodied_runtime.manipulation import (
    G1_MANIPULATION_BODY,
    G1_PICK_PLACE,
    ManipulationPhase,
    ObjectRef,
    Pose3D,
)
from soul_embodied_runtime.ros2_g1_manipulation import (
    Ros2G1ManipulationTransport,
)


NOW = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)


def live_ref() -> ObjectRef:
    return ObjectRef(
        scene_id="seal-g1-manipulation-lab-v1",
        scene_revision=1,
        scene_digest="sha256:" + "1" * 64,
        object_instance_id="seal_cube_01",
        track_id="seal_cube_01_track_1",
        track_epoch=1,
        geometry_digest="sha256:" + "2" * 64,
        pose=Pose3D("world", (0.31, -0.12, 0.89), (0.0, 0.0, 0.0, 1.0)),
        confidence=1.0,
        observed_at=NOW,
        perception_sequence=42,
        source_sensor="g1_head_rgbd+bbox3d+isaac_sim_ground_truth",
    )


def planner() -> AdaManipulationPlanner:
    identity = AdaIdentitySnapshot(
        name="ADA",
        role="Ingeniera",
        active=True,
        ocean={"o": 0.81, "c": 1.0, "e": 1.0, "a": 0.48, "n": 0.21},
        persona_axes={},
        system_prompt_digest="sha256:" + "3" * 64,
        updated_at=NOW,
    )
    memories = DualMemorySnapshot((), (), 0, 0)
    return AdaManipulationPlanner(
        identity, memories, AuthorizationSigner.generate("ada-manip-test")
    )


def test_planner_defines_complete_fixed_phase_order() -> None:
    phases = [step.phase for step in planner().scenario("pick_place_right")]
    assert phases == list(ManipulationPhase)


def test_planner_signs_json_safe_perception_bound_intent() -> None:
    plan = planner()
    step = plan.scenario("pick_place_right")[0]
    intent, signed = plan.authorize_manipulation(
        step,
        live_ref(),
        now=NOW,
        session_id="test-session",
        subject_id="william",
        user_present=True,
        edge_challenge_ref="edge-challenge:test",
    )
    assert intent.body_id == G1_MANIPULATION_BODY
    assert intent.capability == G1_PICK_PLACE
    assert isinstance(intent.goal["object_ref"]["observed_at"], str)
    assert f"object-ref:{live_ref().digest()}" in intent.context_refs
    assert signed.authorization.max_uses == 1
    assert signed.authorization.effective_constraints.max_force_n == 35.0


def test_semantic_controller_never_maps_to_leg_joints() -> None:
    for phase in ManipulationPhase:
        targets = Ros2G1ManipulationTransport._targets(phase)
        assert targets
        assert not any(
            token in name
            for name in targets
            for token in ("hip", "knee", "ankle")
        )


def test_impulse_force_conversion_is_measured() -> None:
    force = Ros2G1ManipulationTransport._raw_contact_force(
        {"dt": 0.02, "impulse": [0.3, 0.4, 0.0]}
    )
    assert force == pytest.approx(25.0)


def test_edge_phase_state_rejects_skip_and_replay(tmp_path, monkeypatch) -> None:
    path = Path(__file__).parents[2] / "deploy/isaac-sim-spark70" \
        / "execute_ada_g1_authorized_manipulation.py"
    spec = importlib.util.spec_from_file_location("g1_manipulation_edge_test", path)
    assert spec and spec.loader
    edge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(edge)
    monkeypatch.setattr(edge, "PHASE_DB", tmp_path / "phases.sqlite3")

    with pytest.raises(PermissionError, match="start with approach"):
        edge.verify_phase_order("session-1", "grasp", ())
    edge.verify_phase_order("session-1", "approach", ())
    previous = edge.advance_phase("session-1", "approach", {"measured": True})
    with pytest.raises(PermissionError, match="out of order"):
        edge.verify_phase_order("session-1", "approach", (previous,))
    with pytest.raises(PermissionError, match="previous measured effect"):
        edge.verify_phase_order("session-1", "pregrasp", ())
    edge.verify_phase_order("session-1", "pregrasp", (previous,))
