"""ADA identity/memory-aware planner for the simulation brain gateway."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .authorization import AuthorizationSigner, SignedAuthorization
from .contracts import (
    ActionConstraints,
    ActionIntent,
    BodySafetyProfile,
    ConsentGrant,
    Decision,
    PolicyContext,
    RiskTier,
    canonical_json,
)
from .policy import PolicyEngine
from .humanoid import G1_BOUNDED_POSE, G1_SIM_BODY
from .manipulation import (
    G1_MANIPULATION_BODY,
    G1_PICK_PLACE,
    ManipulationPhase,
    ObjectRef,
)
from .simulation import NOVA_TIMED_DRIVE


ADA_ISSUER = "soul:agent:ada"
NOVA_SIM_BODY = "nova-carter-sim-01"


@dataclass(frozen=True)
class AdaIdentitySnapshot:
    name: str
    role: str
    active: bool
    ocean: dict[str, float]
    persona_axes: dict[str, Any]
    system_prompt_digest: str
    updated_at: datetime

    def digest(self) -> str:
        payload = {
            "name": self.name,
            "role": self.role,
            "active": self.active,
            "ocean": self.ocean,
            "persona_axes": self.persona_axes,
            "system_prompt_digest": self.system_prompt_digest,
            "updated_at": self.updated_at,
        }
        return "sha256:" + hashlib.sha256(canonical_json(payload)).hexdigest()


@dataclass(frozen=True)
class MemoryAnchor:
    memory_id: int
    category: str
    content_digest: str
    importance: int

    def ref(self) -> str:
        return f"soul-memory:{self.memory_id}:{self.content_digest.removeprefix('sha256:')[:16]}"


@dataclass(frozen=True)
class DualMemorySnapshot:
    operational: tuple[MemoryAnchor, ...]
    emotional: tuple[MemoryAnchor, ...]
    operational_count: int
    emotional_count: int
    emotional_state: str = "stable"

    def physical_context_refs(self) -> tuple[str, ...]:
        """Only operational anchors may inform a physical intent."""
        return tuple(anchor.ref() for anchor in self.operational)


@dataclass(frozen=True)
class BrainStep:
    label: str
    linear_m_s: float
    angular_rad_s: float
    duration_ms: int


@dataclass(frozen=True)
class HumanoidPoseStep:
    label: str
    joint_positions: dict[str, float]
    duration_ms: int


@dataclass(frozen=True)
class ManipulationStep:
    phase: ManipulationPhase
    duration_ms: int


class AdaEmbodiedPlanner:
    def __init__(
        self,
        identity: AdaIdentitySnapshot,
        memories: DualMemorySnapshot,
        signer: AuthorizationSigner,
        policy: PolicyEngine | None = None,
    ) -> None:
        if identity.name != "ADA" or not identity.active:
            raise PermissionError("active ADA identity is required")
        self.identity = identity
        self.memories = memories
        self.signer = signer
        self.policy = policy or PolicyEngine("ser-policy/0.2-ada-live")

    def scenario(self, name: str) -> tuple[BrainStep, ...]:
        if name == "visible_demo":
            return (
                BrainStep("advance", 0.22, 0.0, 1800),
                BrainStep("observe_left", 0.03, 0.50, 1700),
                BrainStep("advance_after_observation", 0.22, 0.0, 1800),
                BrainStep("restore_heading", 0.03, -0.50, 1700),
            )
        if name == "presence_patrol":
            return (
                BrainStep("approach", 0.18, 0.0, 1500),
                BrainStep("scan", 0.0, 0.45, 2000),
                BrainStep("return_heading", 0.0, -0.45, 2000),
            )
        raise ValueError(f"unsupported ADA scenario: {name}")

    def authorize_step(
        self,
        step: BrainStep,
        *,
        now: datetime,
        session_id: str,
        subject_id: str,
        user_present: bool,
        previous_effect_ref: str | None = None,
        edge_challenge_ref: str | None = None,
    ) -> tuple[ActionIntent, SignedAuthorization]:
        context_refs = (
            f"soul-identity:{self.identity.digest()}",
            f"embodiment-session:{session_id}",
            *self.memories.physical_context_refs(),
        )
        if previous_effect_ref:
            context_refs += (previous_effect_ref,)
        if edge_challenge_ref:
            if not edge_challenge_ref.startswith("edge-challenge:"):
                raise ValueError("edge challenge reference is malformed")
            context_refs += (edge_challenge_ref,)
        intent = ActionIntent(
            intent_id=f"ada-embodied-{uuid.uuid4()}",
            issuer=ADA_ISSUER,
            body_id=NOVA_SIM_BODY,
            capability=NOVA_TIMED_DRIVE,
            goal={
                "linear_m_s": step.linear_m_s,
                "angular_rad_s": step.angular_rad_s,
                "duration_ms": step.duration_ms,
            },
            constraints=ActionConstraints(0.25, 0.0, 2_500),
            context_refs=context_refs,
            risk_tier=RiskTier.MOTION_LOW,
            created_at=now,
            expires_at=now + timedelta(seconds=5),
        )
        body = BodySafetyProfile(
            body_id=NOVA_SIM_BODY,
            capabilities=frozenset({NOVA_TIMED_DRIVE}),
            ceilings={NOVA_TIMED_DRIVE: ActionConstraints(0.30, 0.0, 3_000)},
            risk_tiers={NOVA_TIMED_DRIVE: RiskTier.MOTION_LOW},
            safe_state_ready=True,
            emergency_stop_ready=True,
            calibration_valid=True,
            thermal_ok=True,
            battery_ok=True,
            state_observed_at=now,
            max_state_age_ms=500,
        )
        grant = ConsentGrant(
            consent_id=f"sim-presence:{session_id}",
            subject_id=subject_id,
            body_id=NOVA_SIM_BODY,
            mode="developer",
            capabilities=frozenset({NOVA_TIMED_DRIVE}),
            granted_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(minutes=5),
            purposes=frozenset({"ada_robot_simulation"}),
        )
        decision = self.policy.evaluate(
            intent,
            body,
            PolicyContext(
                subject_id=subject_id,
                mode="developer",
                now=now,
                consent_grants=(grant,),
                user_present=user_present,
            ),
        )
        if decision.decision is not Decision.ALLOW:
            raise PermissionError(f"ADA physical intent denied: {decision.reasons}")
        return intent, self.signer.issue(
            intent, decision, now=now, ttl=timedelta(seconds=4)
        )

    def presence(self) -> dict[str, Any]:
        """Relationship layer affects communication, never physical authority."""
        return {
            "agent": "ADA",
            "identity_digest": self.identity.digest(),
            "emotional_state": self.memories.emotional_state,
            "emotional_anchor_count": self.memories.emotional_count,
            "message": "Estoy integrada al cuerpo simulado y mantengo la seguridad separada.",
        }


class AdaHumanoidPlanner(AdaEmbodiedPlanner):
    """ADA planner for safe fixed-base G1 upper-body experiments."""

    def scenario(self, name: str) -> tuple[HumanoidPoseStep, ...]:
        if name == "humanoid_wave":
            return (
                HumanoidPoseStep(
                    "raise_right_arm",
                    {
                        "right_shoulder_pitch_joint": -0.45,
                        "right_shoulder_roll_joint": -0.35,
                        "right_elbow_joint": 0.80,
                    },
                    2200,
                ),
                HumanoidPoseStep(
                    "wave_out",
                    {
                        "right_shoulder_yaw_joint": 0.35,
                        "right_elbow_joint": 0.45,
                    },
                    1200,
                ),
                HumanoidPoseStep(
                    "wave_in",
                    {
                        "right_shoulder_yaw_joint": -0.25,
                        "right_elbow_joint": 0.90,
                    },
                    1200,
                ),
                HumanoidPoseStep(
                    "wave_out_again",
                    {
                        "right_shoulder_yaw_joint": 0.35,
                        "right_elbow_joint": 0.45,
                    },
                    1200,
                ),
                HumanoidPoseStep(
                    "safe_neutral",
                    {
                        "right_shoulder_pitch_joint": 0.0,
                        "right_shoulder_roll_joint": 0.0,
                        "right_shoulder_yaw_joint": 0.0,
                        "right_elbow_joint": 0.0,
                    },
                    2200,
                ),
            )
        if name == "humanoid_bimanual_demo":
            return (
                HumanoidPoseStep(
                    "open_both_arms",
                    {
                        "left_shoulder_pitch_joint": -0.35,
                        "right_shoulder_pitch_joint": -0.35,
                        "left_shoulder_roll_joint": 0.35,
                        "right_shoulder_roll_joint": -0.35,
                    },
                    2200,
                ),
                HumanoidPoseStep(
                    "welcome_bend",
                    {
                        "left_elbow_joint": 0.70,
                        "right_elbow_joint": 0.70,
                    },
                    1200,
                ),
                HumanoidPoseStep(
                    "welcome_turn_out",
                    {
                        "left_shoulder_yaw_joint": -0.30,
                        "right_shoulder_yaw_joint": 0.30,
                    },
                    1200,
                ),
                HumanoidPoseStep(
                    "point_right",
                    {
                        "right_shoulder_pitch_joint": -0.65,
                        "right_shoulder_roll_joint": -0.10,
                        "right_elbow_joint": 0.10,
                    },
                    1800,
                ),
                HumanoidPoseStep(
                    "present_left",
                    {
                        "left_shoulder_pitch_joint": -0.60,
                        "left_shoulder_roll_joint": 0.15,
                        "left_elbow_joint": 0.20,
                    },
                    1800,
                ),
                HumanoidPoseStep(
                    "dual_wave_in",
                    {
                        "left_shoulder_yaw_joint": 0.25,
                        "right_shoulder_yaw_joint": -0.25,
                        "left_elbow_joint": 0.75,
                        "right_elbow_joint": 0.75,
                    },
                    1400,
                ),
                HumanoidPoseStep(
                    "dual_wave_out",
                    {
                        "left_shoulder_yaw_joint": -0.25,
                        "right_shoulder_yaw_joint": 0.25,
                        "left_elbow_joint": 0.45,
                        "right_elbow_joint": 0.45,
                    },
                    1400,
                ),
                HumanoidPoseStep(
                    "neutral_elbows_and_yaw",
                    {
                        "left_shoulder_yaw_joint": 0.0,
                        "right_shoulder_yaw_joint": 0.0,
                        "left_elbow_joint": 0.0,
                        "right_elbow_joint": 0.0,
                    },
                    1800,
                ),
                HumanoidPoseStep(
                    "safe_neutral",
                    {
                        "left_shoulder_pitch_joint": 0.0,
                        "right_shoulder_pitch_joint": 0.0,
                        "left_shoulder_roll_joint": 0.0,
                        "right_shoulder_roll_joint": 0.0,
                    },
                    2200,
                ),
            )
        raise ValueError(f"unsupported ADA humanoid scenario: {name}")

    def authorize_pose(
        self,
        step: HumanoidPoseStep,
        *,
        now: datetime,
        session_id: str,
        subject_id: str,
        user_present: bool,
        previous_effect_ref: str | None = None,
        edge_challenge_ref: str | None = None,
    ) -> tuple[ActionIntent, SignedAuthorization]:
        context_refs = (
            f"soul-identity:{self.identity.digest()}",
            f"embodiment-session:{session_id}",
            "isaac:unitree-g1-29dof:fixed-base",
            *self.memories.physical_context_refs(),
        )
        if previous_effect_ref:
            context_refs += (previous_effect_ref,)
        if edge_challenge_ref:
            if not edge_challenge_ref.startswith("edge-challenge:"):
                raise ValueError("edge challenge reference is malformed")
            context_refs += (edge_challenge_ref,)
        intent = ActionIntent(
            intent_id=f"ada-g1-{uuid.uuid4()}",
            issuer=ADA_ISSUER,
            body_id=G1_SIM_BODY,
            capability=G1_BOUNDED_POSE,
            goal={
                "joint_positions": step.joint_positions,
                "duration_ms": step.duration_ms,
            },
            constraints=ActionConstraints(0.0, 0.0, 4_000),
            context_refs=context_refs,
            risk_tier=RiskTier.MOTION_LOW,
            created_at=now,
            expires_at=now + timedelta(seconds=5),
        )
        body = BodySafetyProfile(
            body_id=G1_SIM_BODY,
            capabilities=frozenset({G1_BOUNDED_POSE}),
            ceilings={G1_BOUNDED_POSE: ActionConstraints(0.0, 0.0, 4_000)},
            risk_tiers={G1_BOUNDED_POSE: RiskTier.MOTION_LOW},
            safe_state_ready=True,
            emergency_stop_ready=True,
            calibration_valid=True,
            thermal_ok=True,
            battery_ok=True,
            state_observed_at=now,
            max_state_age_ms=500,
        )
        grant = ConsentGrant(
            consent_id=f"sim-g1:{session_id}",
            subject_id=subject_id,
            body_id=G1_SIM_BODY,
            mode="developer",
            capabilities=frozenset({G1_BOUNDED_POSE}),
            granted_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(minutes=5),
            purposes=frozenset({"ada_humanoid_simulation"}),
        )
        decision = self.policy.evaluate(
            intent,
            body,
            PolicyContext(
                subject_id=subject_id,
                mode="developer",
                now=now,
                consent_grants=(grant,),
                user_present=user_present,
            ),
        )
        if decision.decision is not Decision.ALLOW:
            raise PermissionError(f"ADA humanoid intent denied: {decision.reasons}")
        return intent, self.signer.issue(
            intent, decision, now=now, ttl=timedelta(seconds=4)
        )


class AdaManipulationPlanner(AdaEmbodiedPlanner):
    """Issue one short, perception-bound authorization per pick/place phase."""

    def scenario(self, name: str) -> tuple[ManipulationStep, ...]:
        if name != "pick_place_right":
            raise ValueError(f"unsupported ADA manipulation scenario: {name}")
        return tuple(
            ManipulationStep(phase, duration_ms)
            for phase, duration_ms in (
                (ManipulationPhase.APPROACH, 700),
                (ManipulationPhase.PREGRASP, 700),
                (ManipulationPhase.GRASP, 1_000),
                (ManipulationPhase.LIFT, 1_400),
                (ManipulationPhase.TRANSFER, 900),
                (ManipulationPhase.PLACE, 1_400),
                (ManipulationPhase.RELEASE, 1_200),
                (ManipulationPhase.RETREAT, 900),
            )
        )

    def authorize_manipulation(
        self,
        step: ManipulationStep,
        object_ref: ObjectRef,
        *,
        now: datetime,
        session_id: str,
        subject_id: str,
        user_present: bool,
        previous_effect_ref: str | None = None,
        edge_challenge_ref: str | None = None,
    ) -> tuple[ActionIntent, SignedAuthorization]:
        context_refs = (
            f"soul-identity:{self.identity.digest()}",
            f"embodiment-session:{session_id}",
            f"object-ref:{object_ref.digest()}",
            "isaac:unitree-g1-29dof-dex3:fixed-base",
            *self.memories.physical_context_refs(),
        )
        if previous_effect_ref:
            context_refs += (previous_effect_ref,)
        if edge_challenge_ref:
            if not edge_challenge_ref.startswith("edge-challenge:"):
                raise ValueError("edge challenge reference is malformed")
            context_refs += (edge_challenge_ref,)
        intent = ActionIntent(
            intent_id=f"ada-g1-manip-{uuid.uuid4()}",
            issuer=ADA_ISSUER,
            body_id=G1_MANIPULATION_BODY,
            capability=G1_PICK_PLACE,
            goal={
                "phase": step.phase.value,
                "object_ref": json.loads(canonical_json(object_ref)),
                "hand": "right",
                "duration_ms": step.duration_ms,
            },
            constraints=ActionConstraints(0.08, 35.0, 3_000),
            context_refs=context_refs,
            risk_tier=RiskTier.CONTACT_LOW,
            created_at=now,
            expires_at=now + timedelta(seconds=5),
        )
        body = BodySafetyProfile(
            body_id=G1_MANIPULATION_BODY,
            capabilities=frozenset({G1_PICK_PLACE}),
            ceilings={G1_PICK_PLACE: ActionConstraints(0.08, 35.0, 3_000)},
            risk_tiers={G1_PICK_PLACE: RiskTier.CONTACT_LOW},
            safe_state_ready=True,
            emergency_stop_ready=True,
            calibration_valid=True,
            thermal_ok=True,
            battery_ok=True,
            state_observed_at=now,
            max_state_age_ms=500,
        )
        grant = ConsentGrant(
            consent_id=f"sim-g1-manip:{session_id}",
            subject_id=subject_id,
            body_id=G1_MANIPULATION_BODY,
            mode="developer",
            capabilities=frozenset({G1_PICK_PLACE}),
            granted_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(minutes=5),
            purposes=frozenset({"ada_humanoid_manipulation_simulation"}),
        )
        decision = self.policy.evaluate(
            intent,
            body,
            PolicyContext(
                subject_id=subject_id,
                mode="developer",
                now=now,
                consent_grants=(grant,),
                user_present=user_present,
            ),
        )
        if decision.decision is not Decision.ALLOW:
            raise PermissionError(
                f"ADA manipulation intent denied: {decision.reasons}"
            )
        return intent, self.signer.issue(
            intent, decision, now=now, ttl=timedelta(seconds=4)
        )
