"""Governed, simulation-only object manipulation for ADA's Unitree G1.

This module deliberately exposes semantic manipulation phases rather than raw
joint targets.  A perception-bound object reference is validated before a
one-shot authorization is consumed; the edge adapter remains responsible for
mapping the phase to a versioned controller and measuring the physical effect.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Protocol

from .audit import AuditChain
from .authorization import AuthorizationVerifier, SignedAuthorization
from .contracts import ActionIntent, canonical_json


G1_MANIPULATION_BODY = "unitree-g1-sim-hands-01"
G1_PICK_PLACE = "manipulation.pick_place"
G1_MANIPULATION_SCENE = "seal-g1-manipulation-lab-v1"
G1_MANIPULATION_OBJECT = "seal_cube_01"
G1_RIGHT_HAND_JOINTS = (
    "right_hand_index_0_joint",
    "right_hand_index_1_joint",
    "right_hand_middle_0_joint",
    "right_hand_middle_1_joint",
    "right_hand_thumb_0_joint",
    "right_hand_thumb_1_joint",
    "right_hand_thumb_2_joint",
)
G1_RIGHT_WRIST_JOINTS = (
    "right_wrist_roll_joint",
    "right_wrist_pitch_joint",
    "right_wrist_yaw_joint",
)
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")


class ManipulationPhase(str, Enum):
    APPROACH = "approach"
    PREGRASP = "pregrasp"
    GRASP = "grasp"
    LIFT = "lift"
    TRANSFER = "transfer"
    PLACE = "place"
    RELEASE = "release"
    RETREAT = "retreat"


@dataclass(frozen=True)
class Pose3D:
    frame_id: str
    position_m: tuple[float, float, float]
    orientation_xyzw: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        if self.frame_id not in {"world", "g1_manipulation_camera"}:
            raise ValueError("pose frame is not authorized")
        values = (*self.position_m, *self.orientation_xyzw)
        if len(self.position_m) != 3 or len(self.orientation_xyzw) != 4:
            raise ValueError("pose dimensions are invalid")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("pose contains non-finite values")
        norm = math.sqrt(sum(value * value for value in self.orientation_xyzw))
        if not 0.99 <= norm <= 1.01:
            raise ValueError("pose quaternion is not normalized")


@dataclass(frozen=True)
class ObjectRef:
    scene_id: str
    scene_revision: int
    scene_digest: str
    object_instance_id: str
    track_id: str
    track_epoch: int
    geometry_digest: str
    pose: Pose3D
    confidence: float
    observed_at: datetime
    perception_sequence: int
    source_sensor: str = "g1_head_rgbd"
    schema: str = "ser.object-ref/1"

    def __post_init__(self) -> None:
        if self.schema != "ser.object-ref/1":
            raise ValueError("unsupported object reference schema")
        if self.scene_id != G1_MANIPULATION_SCENE:
            raise ValueError("object reference belongs to another scene")
        if self.object_instance_id != G1_MANIPULATION_OBJECT:
            raise ValueError("object reference is not the authorized object")
        if not _IDENTIFIER_RE.fullmatch(self.track_id):
            raise ValueError("track_id is malformed")
        if self.scene_revision < 1 or self.track_epoch < 1 or self.perception_sequence < 1:
            raise ValueError("object reference counters must be positive")
        if not _DIGEST_RE.fullmatch(self.scene_digest):
            raise ValueError("scene_digest is malformed")
        if not _DIGEST_RE.fullmatch(self.geometry_digest):
            raise ValueError("geometry_digest is malformed")
        if not 0.0 <= self.confidence <= 1.0 or not math.isfinite(self.confidence):
            raise ValueError("object confidence is invalid")
        if self.confidence < 0.90:
            raise ValueError("object confidence is below the manipulation floor")
        if self.observed_at.tzinfo is None:
            raise ValueError("object observation must be timezone-aware")
        if self.source_sensor not in {
            "g1_head_rgbd",
            "g1_head_rgbd+bbox3d+isaac_sim_ground_truth",
        }:
            raise ValueError("object reference source sensor is not authorized")

    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(canonical_json(self)).hexdigest()


@dataclass(frozen=True)
class ManipulationObservation:
    object_ref: ObjectRef
    visible: bool
    right_hand_positions: dict[str, float]
    contact_links: frozenset[str]
    object_pose_world: Pose3D
    observed_at: datetime


@dataclass(frozen=True)
class ManipulationCommand:
    phase: ManipulationPhase
    object_ref: ObjectRef
    hand: str
    duration_ms: int


@dataclass(frozen=True)
class ManipulationEffect:
    action_handle: str
    phase: ManipulationPhase
    object_instance_id: str
    before: ManipulationObservation
    after: ManipulationObservation
    object_displacement_m: float
    grasp_confirmed: bool
    stable: bool


class G1ManipulationTransport(Protocol):
    def latest_observation(self) -> ManipulationObservation | None: ...

    def run_phase(
        self,
        command: ManipulationCommand,
        *,
        heartbeat_timeout_ms: int,
    ) -> ManipulationObservation: ...

    def safe_hold(self, reason: str) -> None: ...


@dataclass
class G1ManipulationSimulationAdapter:
    """Fail-closed adapter for one perception-bound manipulation phase."""

    body_id: str
    verifier: AuthorizationVerifier
    transport: G1ManipulationTransport
    audit: AuditChain
    scene_digest: str
    max_observation_age_ms: int = 350
    max_clock_skew_ms: int = 100
    heartbeat_timeout_ms: int = 250
    safe: bool = True
    clock: Callable[[], datetime] = field(
        default_factory=lambda: lambda: datetime.now(timezone.utc), repr=False
    )

    def __post_init__(self) -> None:
        if self.body_id != G1_MANIPULATION_BODY:
            raise ValueError("manipulation adapter requires the hands-specific body_id")
        if not _DIGEST_RE.fullmatch(self.scene_digest):
            raise ValueError("configured scene digest is malformed")
        self._prepared: dict[str, tuple[ManipulationCommand, datetime]] = {}
        self._executed: set[str] = set()

    def discover(self) -> dict[str, object]:
        return {
            "body_id": self.body_id,
            "kind": "unitree_g1_29dof_dex3_isaac_sim_fixed_base",
            "capabilities": [G1_PICK_PLACE],
            "hands": ["right"],
            "object_allowlist": [G1_MANIPULATION_OBJECT],
            "phases": [phase.value for phase in ManipulationPhase],
            "locomotion_authorized": False,
            "hardware_authorized": False,
        }

    def prepare(
        self,
        authorization: SignedAuthorization,
        *,
        intent: ActionIntent,
        now: datetime,
    ) -> str:
        if not self.safe:
            raise RuntimeError("manipulation body is in safe state")
        if intent.body_id != self.body_id:
            raise PermissionError("intent body does not match manipulation body")
        if intent.capability != G1_PICK_PLACE:
            raise PermissionError("capability is not supported by manipulation body")
        bounded = authorization.authorization
        if bounded.issuer != intent.issuer:
            raise PermissionError("authorization issuer does not match intent issuer")
        if bounded.allowed_capability != intent.capability:
            raise PermissionError("authorization capability does not match intent capability")
        ttl_ms = (bounded.expires_at - bounded.not_before).total_seconds() * 1000
        if ttl_ms > 5_000:
            raise PermissionError("authorization lease exceeds manipulation maximum")

        command = self._parse_command(intent)
        if command.object_ref.scene_digest != self.scene_digest:
            raise PermissionError("object reference scene digest does not match loaded scene")
        if command.duration_ms > bounded.effective_constraints.max_duration_ms:
            raise PermissionError("phase duration exceeds signed authorization")
        remaining_ms = (bounded.expires_at - now).total_seconds() * 1000
        if remaining_ms < command.duration_ms + 250:
            raise PermissionError("authorization lease cannot cover manipulation phase")

        observation = self.transport.latest_observation()
        self._validate_observation(observation, command, now)
        assert observation is not None
        if observation.object_ref.digest() != command.object_ref.digest():
            raise PermissionError("signed object reference is not the current perception")

        allowed, reason = self.verifier.verify_and_consume(
            authorization,
            expected_body_id=self.body_id,
            expected_intent_hash=intent.digest(),
            now=now,
        )
        if not allowed:
            raise PermissionError(f"authorization rejected: {reason}")

        handle = "prepared:" + bounded.authorization_id
        self._prepared[handle] = (
            command,
            bounded.expires_at - timedelta(milliseconds=command.duration_ms + 250),
        )
        self.audit.append(
            "manipulation.phase_prepared",
            intent.intent_id,
            {
                "body_id": self.body_id,
                "phase": command.phase.value,
                "object_instance_id": command.object_ref.object_instance_id,
                "object_ref_digest": command.object_ref.digest(),
                "scene_digest": self.scene_digest,
                "hand": command.hand,
            },
        )
        return handle

    def execute(self, prepared_action: str) -> ManipulationEffect:
        if not self.safe:
            raise RuntimeError("manipulation body is in safe state")
        if prepared_action not in self._prepared:
            raise ValueError("unknown prepared manipulation phase")
        if prepared_action in self._executed:
            raise RuntimeError("prepared manipulation phase already executed")
        command, execute_deadline = self._prepared[prepared_action]
        now = self.clock()
        if now > execute_deadline:
            raise PermissionError("prepared manipulation phase expired before execution")
        before = self.transport.latest_observation()
        self._validate_observation(before, command, now)
        assert before is not None
        if before.object_ref.digest() != command.object_ref.digest():
            raise PermissionError("object track changed before execution")
        self._executed.add(prepared_action)
        action_handle = prepared_action.replace("prepared:", "action:", 1)
        try:
            after = self.transport.run_phase(
                command, heartbeat_timeout_ms=self.heartbeat_timeout_ms
            )
            self._validate_after(after, command)
        except Exception:
            self.transport.safe_hold("manipulation_execution_fault")
            self.audit.append(
                "manipulation.safe_hold",
                action_handle,
                {"phase": command.phase.value, "reason": "execution_fault"},
            )
            raise

        displacement = math.dist(
            before.object_pose_world.position_m, after.object_pose_world.position_m
        )
        grasp_confirmed = self._grasp_confirmed(after)
        if command.phase in {ManipulationPhase.GRASP, ManipulationPhase.LIFT, ManipulationPhase.TRANSFER}:
            if not grasp_confirmed:
                self.transport.safe_hold("grasp_not_confirmed")
                raise RuntimeError("grasp was not confirmed by bilateral contact")
        if command.phase is ManipulationPhase.LIFT:
            vertical_lift = (
                after.object_pose_world.position_m[2]
                - before.object_pose_world.position_m[2]
            )
            if displacement < 0.04 or vertical_lift < 0.03:
                self.transport.safe_hold("lift_effect_missing")
                raise RuntimeError(
                    "object did not move at least 4 cm with 3 cm measured upward lift"
                )
        stable = after.visible and self._fresh(after.observed_at, self.clock())
        effect = ManipulationEffect(
            action_handle,
            command.phase,
            command.object_ref.object_instance_id,
            before,
            after,
            displacement,
            grasp_confirmed,
            stable,
        )
        self.audit.append(
            "manipulation.phase_completed",
            action_handle,
            {
                "phase": command.phase.value,
                "object_instance_id": effect.object_instance_id,
                "object_displacement_m": displacement,
                "grasp_confirmed": grasp_confirmed,
                "stable": stable,
            },
        )
        return effect

    def safe_state(self, reason: str) -> dict[str, str]:
        self.safe = False
        self.transport.safe_hold(reason)
        self.audit.append("manipulation.safe_state", self.body_id, {"reason": reason})
        return {"type": "safe_state", "reason": reason}

    def _fresh(self, observed_at: datetime, now: datetime) -> bool:
        age_ms = (now - observed_at).total_seconds() * 1000
        return -self.max_clock_skew_ms <= age_ms <= self.max_observation_age_ms

    def _validate_observation(
        self,
        observation: ManipulationObservation | None,
        command: ManipulationCommand,
        now: datetime,
    ) -> None:
        if observation is None:
            raise RuntimeError("manipulation perception is unavailable")
        if not self._fresh(observation.observed_at, now):
            raise RuntimeError("manipulation perception is stale")
        if not observation.visible:
            raise RuntimeError("authorized object is not visible")
        if observation.object_ref.object_instance_id != command.object_ref.object_instance_id:
            raise PermissionError("perceived object does not match signed object")
        if observation.object_ref.scene_digest != self.scene_digest:
            raise PermissionError("perceived scene does not match configured scene")
        if observation.object_ref.track_epoch != command.object_ref.track_epoch:
            raise PermissionError("object track epoch changed")
        if set(observation.right_hand_positions) != set(G1_RIGHT_HAND_JOINTS):
            raise RuntimeError("right-hand joint observation is incomplete")
        if not all(math.isfinite(value) for value in observation.right_hand_positions.values()):
            raise RuntimeError("right-hand joint observation is non-finite")

    def _validate_after(
        self,
        observation: ManipulationObservation,
        command: ManipulationCommand,
    ) -> None:
        self._validate_observation(observation, command, self.clock())
        if observation.object_ref.track_id != command.object_ref.track_id:
            raise RuntimeError("authorized object track was lost")
        if observation.object_ref.perception_sequence <= command.object_ref.perception_sequence:
            raise RuntimeError("manipulation effect lacks a fresh perception sample")

    @staticmethod
    def _grasp_confirmed(observation: ManipulationObservation) -> bool:
        index_contact = any("index" in link for link in observation.contact_links)
        opposing_contact = any(
            token in link for link in observation.contact_links for token in ("thumb", "middle")
        )
        return index_contact and opposing_contact

    @staticmethod
    def _parse_command(intent: ActionIntent) -> ManipulationCommand:
        if set(intent.goal) != {"phase", "object_ref", "hand", "duration_ms"}:
            raise ValueError("manipulation goal has an invalid payload")
        try:
            phase = ManipulationPhase(intent.goal["phase"])
        except (TypeError, ValueError) as exc:
            raise ValueError("manipulation phase is invalid") from exc
        if intent.goal["hand"] != "right":
            raise PermissionError("only the calibrated right hand is authorized")
        duration = intent.goal["duration_ms"]
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise ValueError("duration_ms must be an integer")
        if not 250 <= duration <= 3_000:
            raise PermissionError("phase duration is outside the safe envelope")
        raw_ref = intent.goal["object_ref"]
        if not isinstance(raw_ref, dict):
            raise ValueError("object_ref must be an object")
        raw_pose = raw_ref.get("pose")
        if not isinstance(raw_pose, dict):
            raise ValueError("object_ref pose must be an object")
        pose = Pose3D(
            frame_id=raw_pose["frame_id"],
            position_m=tuple(raw_pose["position_m"]),
            orientation_xyzw=tuple(raw_pose["orientation_xyzw"]),
        )
        observed_at = raw_ref["observed_at"]
        if isinstance(observed_at, str):
            observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        object_ref = ObjectRef(**{**raw_ref, "pose": pose, "observed_at": observed_at})
        return ManipulationCommand(phase, object_ref, "right", duration)
