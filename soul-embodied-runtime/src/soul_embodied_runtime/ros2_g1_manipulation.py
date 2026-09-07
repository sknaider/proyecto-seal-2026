"""Measured ROS 2 transport for the calibrated G1 right-hand pick/place cell."""
from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path

from .humanoid import HumanoidPoseCommand
from .manipulation import (
    G1_RIGHT_HAND_JOINTS,
    ManipulationCommand,
    ManipulationObservation,
    ManipulationPhase,
    ObjectRef,
    Pose3D,
)
from .ros2_g1 import Ros2G1JointTransport


OBJECT_BODY_PATH = "/World/ManipulationObjects/seal_cube_01"
STATE_PATH = Path("/var/lib/seal/ser-sim/g1-manipulation-state.json")
OPEN_HAND = {name: 0.0 for name in G1_RIGHT_HAND_JOINTS}
CALIBRATED_GRASP = {
    "right_hand_index_0_joint": 0.65,
    "right_hand_index_1_joint": 0.90,
    "right_hand_middle_0_joint": 0.55,
    "right_hand_middle_1_joint": 0.75,
    "right_hand_thumb_0_joint": -0.30,
    "right_hand_thumb_1_joint": -0.65,
    "right_hand_thumb_2_joint": -0.55,
}


class Ros2G1ManipulationTransport:
    """Map semantic phases to one versioned, simulation-only calibration."""

    controller_version = "seal-g1-dex3-pick-place/1"

    def __init__(
        self,
        joint_transport: Ros2G1JointTransport,
        *,
        ready_state: dict,
        state_path: Path = STATE_PATH,
        max_contact_force_n: float = 35.0,
    ) -> None:
        self._joint = joint_transport
        self._state_path = state_path
        self._ready_state = ready_state
        self._max_contact_force_n = max_contact_force_n
        self._ready_reads_remaining = 2
        sample = self._joint.latest_joint_state()
        if sample is None:
            raise RuntimeError("joint state unavailable for manipulation transport")
        self._ready_joint_positions = dict(sample.positions)

    @staticmethod
    def _raw_contact_force(raw: dict) -> float:
        dt = float(raw.get("dt") or 0.0)
        impulse = raw.get("impulse") or []
        if dt <= 0.0 or len(impulse) != 3:
            return 0.0
        return math.sqrt(sum(float(value) ** 2 for value in impulse)) / dt

    @classmethod
    def _contact_links(cls, state: dict) -> frozenset[str]:
        links = set()
        for link, payload in state["contacts"].items():
            expected = f"/World/G1/right_hand/{link}"
            for raw in payload.get("raw_contacts", []):
                if {raw.get("body0"), raw.get("body1")} != {
                    expected,
                    OBJECT_BODY_PATH,
                }:
                    continue
                if cls._raw_contact_force(raw) >= 0.05:
                    links.add(link)
                    break
        return frozenset(links)

    @staticmethod
    def _track_id(state: dict) -> str:
        # Legacy live state used colons; normalize only that exact known form.
        raw = str(state["track_id"])
        if raw == "seal_cube_01:isaac-sim-track:1":
            return "seal_cube_01_track_1"
        return raw

    @classmethod
    def object_ref_from_state(cls, state: dict) -> ObjectRef:
        pose = state["object_pose_world"]
        return ObjectRef(
            scene_id=state["scene_id"],
            scene_revision=int(state["scene_revision"]),
            scene_digest=state["scene_digest"],
            object_instance_id=state["object_instance_id"],
            track_id=cls._track_id(state),
            track_epoch=int(state["track_epoch"]),
            geometry_digest=state["object_geometry_digest"],
            pose=Pose3D(
                "world",
                tuple(float(value) for value in pose["position_m"]),
                tuple(float(value) for value in pose["orientation_xyzw"]),
            ),
            confidence=float(state["perception_confidence"]),
            observed_at=datetime.fromisoformat(state["observed_at_utc"]),
            perception_sequence=int(state["perception_sequence"]),
            source_sensor=state["sensor_source"],
        )

    @classmethod
    def observation_from_state(
        cls, state: dict, joint_positions: dict[str, float]
    ) -> ManipulationObservation:
        ref = cls.object_ref_from_state(state)
        return ManipulationObservation(
            object_ref=ref,
            visible=float(state["perception_confidence"]) >= 0.90,
            right_hand_positions={
                name: float(joint_positions[name]) for name in G1_RIGHT_HAND_JOINTS
            },
            contact_links=cls._contact_links(state),
            object_pose_world=ref.pose,
            observed_at=ref.observed_at,
        )

    def _read_state(self, *, after_sequence: int | None = None) -> dict:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
            if after_sequence is None or int(state["perception_sequence"]) > after_sequence:
                return state
            time.sleep(0.03)
        raise TimeoutError("fresh manipulation perception unavailable")

    def latest_observation(self) -> ManipulationObservation | None:
        if self._ready_reads_remaining:
            self._ready_reads_remaining -= 1
            return self.observation_from_state(
                self._ready_state, self._ready_joint_positions
            )
        state = self._read_state()
        sample = self._joint.latest_joint_state()
        if sample is None:
            return None
        return self.observation_from_state(state, sample.positions)

    @staticmethod
    def _targets(phase: ManipulationPhase) -> dict[str, float]:
        if phase in {ManipulationPhase.APPROACH, ManipulationPhase.PREGRASP}:
            return {**OPEN_HAND, "right_shoulder_pitch_joint": 0.0,
                    "right_shoulder_roll_joint": 0.0}
        if phase is ManipulationPhase.GRASP:
            return dict(CALIBRATED_GRASP)
        if phase is ManipulationPhase.LIFT:
            return {**CALIBRATED_GRASP, "right_shoulder_pitch_joint": -0.24}
        if phase is ManipulationPhase.TRANSFER:
            return {
                **CALIBRATED_GRASP,
                "right_shoulder_pitch_joint": -0.24,
                # A four-centiradian lateral carry remains visible/measurable
                # while keeping the grasped block inside the placement basin.
                "right_shoulder_roll_joint": -0.04,
            }
        if phase is ManipulationPhase.PLACE:
            return {
                **CALIBRATED_GRASP,
                "right_shoulder_pitch_joint": 0.0,
                "right_shoulder_roll_joint": 0.0,
            }
        if phase is ManipulationPhase.RELEASE:
            return dict(OPEN_HAND)
        if phase is ManipulationPhase.RETREAT:
            return {
                **OPEN_HAND,
                "right_shoulder_pitch_joint": 0.0,
                "right_shoulder_roll_joint": 0.0,
                "right_shoulder_yaw_joint": 0.0,
                "right_elbow_joint": 0.0,
            }
        raise ValueError(f"unsupported manipulation phase: {phase}")

    def run_phase(
        self,
        command: ManipulationCommand,
        *,
        heartbeat_timeout_ms: int,
    ) -> ManipulationObservation:
        before = self._read_state()
        self._joint.run_pose(
            HumanoidPoseCommand(self._targets(command.phase), command.duration_ms),
            heartbeat_timeout_ms=heartbeat_timeout_ms,
        )
        state = self._read_state(after_sequence=int(before["perception_sequence"]))
        measured_forces = [
            self._raw_contact_force(raw)
            for payload in state["contacts"].values()
            for raw in payload.get("raw_contacts", [])
            if OBJECT_BODY_PATH in {raw.get("body0"), raw.get("body1")}
        ]
        maximum_force = max(measured_forces, default=0.0)
        if maximum_force > self._max_contact_force_n:
            self.safe_hold("contact_force_ceiling_exceeded")
            raise RuntimeError(
                "measured contact force exceeded signed ceiling: "
                f"force_n={maximum_force:.3f}, ceiling_n={self._max_contact_force_n:.3f}"
            )
        sample = self._joint.latest_joint_state()
        if sample is None:
            raise RuntimeError("joint state unavailable after manipulation phase")
        return self.observation_from_state(state, sample.positions)

    def safe_hold(self, reason: str) -> None:
        self._joint.hold(reason)
