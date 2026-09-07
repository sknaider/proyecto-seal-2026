"""Simulation-only Unitree G1 adapter behind the SOUL authorization boundary."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from .audit import AuditChain
from .authorization import AuthorizationVerifier, SignedAuthorization
from .contracts import ActionIntent


G1_BOUNDED_POSE = "humanoid.pose_bounded"
G1_SIM_BODY = "unitree-g1-sim-01"
G1_ARM_JOINT_LIMITS_RAD = {
    "left_shoulder_pitch_joint": (-0.8, 0.8),
    "right_shoulder_pitch_joint": (-0.8, 0.8),
    "left_shoulder_roll_joint": (-0.7, 0.7),
    "right_shoulder_roll_joint": (-0.7, 0.7),
    "left_shoulder_yaw_joint": (-0.6, 0.6),
    "right_shoulder_yaw_joint": (-0.6, 0.6),
    "left_elbow_joint": (-0.2, 1.1),
    "right_elbow_joint": (-0.2, 1.1),
}


@dataclass(frozen=True)
class JointStateSample:
    positions: dict[str, float]
    observed_at: datetime


@dataclass(frozen=True)
class HumanoidPoseCommand:
    joint_positions: dict[str, float]
    duration_ms: int


@dataclass(frozen=True)
class HumanoidPoseResult:
    action_handle: str
    initial: JointStateSample
    final: JointStateSample
    command: HumanoidPoseCommand
    hold_error_max_rad: float

    @property
    def max_joint_delta_rad(self) -> float:
        return max(
            abs(self.final.positions[name] - self.initial.positions[name])
            for name in self.command.joint_positions
        )

    @property
    def target_error_max_rad(self) -> float:
        return max(
            abs(self.final.positions[name] - target)
            for name, target in self.command.joint_positions.items()
        )

    @property
    def hold_verified(self) -> bool:
        return math.isfinite(self.hold_error_max_rad) and self.hold_error_max_rad <= 0.12


class JointPoseTransport(Protocol):
    def latest_joint_state(self) -> JointStateSample | None: ...

    def run_pose(
        self,
        command: HumanoidPoseCommand,
        *,
        heartbeat_timeout_ms: int,
    ) -> JointStateSample: ...

    def hold(self, reason: str) -> None: ...

    def hold_error_max_rad(self) -> float | None: ...


@dataclass
class G1HumanoidSimulationAdapter:
    """Fixed-base, upper-body-only G1 adapter; it never authorizes hardware."""

    body_id: str
    verifier: AuthorizationVerifier
    transport: JointPoseTransport
    audit: AuditChain
    max_joint_speed_rad_s: float = 1.0
    max_joint_delta_rad: float = 1.0
    max_state_age_ms: int = 500
    max_clock_skew_ms: int = 100
    heartbeat_timeout_ms: int = 500
    safe: bool = True
    clock: Callable[[], datetime] = field(
        default_factory=lambda: lambda: datetime.now(timezone.utc), repr=False
    )

    def __post_init__(self) -> None:
        if self.body_id != G1_SIM_BODY:
            raise ValueError("G1 adapter requires the canonical simulation-only body_id")
        self._prepared: dict[str, tuple[HumanoidPoseCommand, datetime]] = {}
        self._executed: set[str] = set()
        self._cancelled: set[str] = set()

    def discover(self) -> dict[str, object]:
        return {
            "body_id": self.body_id,
            "kind": "unitree_g1_29dof_isaac_sim_fixed_base",
            "capabilities": [G1_BOUNDED_POSE],
            "allowed_joints": sorted(G1_ARM_JOINT_LIMITS_RAD),
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
            raise RuntimeError("simulation body is in safe state")
        if intent.body_id != self.body_id:
            raise PermissionError("intent body does not match G1 simulation body")
        if intent.capability != G1_BOUNDED_POSE:
            raise PermissionError("capability is not supported by the G1 simulation")
        bounded = authorization.authorization
        if bounded.issuer != intent.issuer:
            raise PermissionError("authorization issuer does not match intent issuer")
        if bounded.allowed_capability != intent.capability:
            raise PermissionError("authorization capability does not match intent capability")
        ttl_ms = (bounded.expires_at - bounded.not_before).total_seconds() * 1000
        if ttl_ms > 5_000:
            raise PermissionError("authorization lease exceeds simulation maximum")

        command = self._parse_command(intent)
        if command.duration_ms > bounded.effective_constraints.max_duration_ms:
            raise PermissionError("duration exceeds signed authorization")
        remaining_ms = (bounded.expires_at - now).total_seconds() * 1000
        if remaining_ms < command.duration_ms + 250:
            raise PermissionError("authorization lease cannot cover bounded pose")
        initial = self.transport.latest_joint_state()
        if initial is None:
            raise RuntimeError("joint state is stale or unavailable: no sample")
        self._validate_sample_and_motion(initial, command, now)

        allowed, reason = self.verifier.verify_and_consume(
            authorization,
            expected_body_id=self.body_id,
            expected_intent_hash=intent.digest(),
            now=now,
        )
        if not allowed:
            raise PermissionError(f"authorization rejected: {reason}")

        handle = "prepared:" + authorization.authorization.authorization_id
        execute_deadline = bounded.expires_at - timedelta(
            milliseconds=command.duration_ms + 250
        )
        self._prepared[handle] = (command, execute_deadline)
        self.audit.append(
            "humanoid.pose_prepared",
            intent.intent_id,
            {
                "body_id": self.body_id,
                "capability": intent.capability,
                "intent_hash": intent.digest(),
                "authorization_id": authorization.authorization.authorization_id,
                "joint_positions": command.joint_positions,
                "duration_ms": command.duration_ms,
                "fixed_base": True,
            },
        )
        return handle

    def execute(self, prepared_action: str) -> HumanoidPoseResult:
        if not self.safe:
            raise RuntimeError("simulation body is in safe state")
        if prepared_action not in self._prepared:
            raise ValueError("unknown prepared action")
        if prepared_action in self._cancelled:
            raise RuntimeError("prepared action was cancelled")
        if prepared_action in self._executed:
            raise RuntimeError("prepared action already executed")
        command, execute_deadline = self._prepared[prepared_action]
        now = self.clock()
        if now > execute_deadline:
            self.audit.append(
                "humanoid.execution_denied",
                prepared_action,
                {"reason": "prepared_action_expired"},
            )
            raise PermissionError("prepared action expired before execution")
        initial = self.transport.latest_joint_state()
        if initial is None:
            raise RuntimeError("joint state is stale or unavailable before execution")
        self._validate_sample_and_motion(initial, command, now)
        self._executed.add(prepared_action)
        action_handle = prepared_action.replace("prepared:", "action:", 1)
        try:
            final = self.transport.run_pose(
                command, heartbeat_timeout_ms=self.heartbeat_timeout_ms
            )
            hold_error = self.transport.hold_error_max_rad()
            if hold_error is None or not math.isfinite(hold_error):
                raise RuntimeError("post-pose hold was not measured")
            for name in command.joint_positions:
                if name not in final.positions or not math.isfinite(final.positions[name]):
                    raise RuntimeError(f"final joint state is non-finite: {name}")
        except Exception:
            self.transport.hold("execution_fault")
            self.audit.append(
                "humanoid.safe_hold", action_handle, {"reason": "execution_fault"}
            )
            raise
        result = HumanoidPoseResult(action_handle, initial, final, command, hold_error)
        if not result.hold_verified:
            self.transport.hold("hold_unstable")
            self.audit.append(
                "humanoid.safe_hold",
                action_handle,
                {"reason": "hold_unstable", "hold_error_max_rad": hold_error},
            )
            raise RuntimeError(
                f"post-pose hold unstable: error_rad={hold_error:.4f}"
            )
        self.audit.append(
            "humanoid.pose_completed",
            action_handle,
            {
                "max_joint_delta_rad": result.max_joint_delta_rad,
                "target_error_max_rad": result.target_error_max_rad,
                "hold_error_max_rad": result.hold_error_max_rad,
                "held": result.hold_verified,
            },
        )
        return result

    def cancel(self, action_handle: str, reason: str) -> dict[str, str]:
        prepared = action_handle.replace("action:", "prepared:", 1)
        self._cancelled.add(prepared)
        self.transport.hold(reason)
        self.audit.append("humanoid.cancelled", action_handle, {"reason": reason})
        return {"type": "cancelled", "handle": action_handle, "reason": reason}

    def safe_state(self, reason: str) -> dict[str, str]:
        self.safe = False
        self.transport.hold(reason)
        self.audit.append("humanoid.safe_state", self.body_id, {"reason": reason})
        return {"type": "safe_state", "reason": reason}

    def _fresh(self, sample: JointStateSample, now: datetime) -> bool:
        age_ms = (now - sample.observed_at).total_seconds() * 1000
        return -self.max_clock_skew_ms <= age_ms <= self.max_state_age_ms

    def _validate_sample_and_motion(
        self,
        sample: JointStateSample,
        command: HumanoidPoseCommand,
        now: datetime,
    ) -> None:
        if not self._fresh(sample, now):
            age_ms = (now - sample.observed_at).total_seconds() * 1000
            raise RuntimeError(
                f"joint state is stale or unavailable: age_ms={age_ms:.1f} "
                f"limit_ms={self.max_state_age_ms}"
            )
        for name, target in command.joint_positions.items():
            if name not in sample.positions:
                raise RuntimeError(f"required joint state is unavailable: {name}")
            observed = sample.positions[name]
            if not math.isfinite(observed):
                raise RuntimeError(f"joint state is non-finite: {name}")
            low, high = G1_ARM_JOINT_LIMITS_RAD[name]
            if not low <= target <= high:
                raise PermissionError(f"joint target exceeds safe envelope: {name}")
            delta = abs(target - observed)
            if delta > self.max_joint_delta_rad:
                raise PermissionError(f"joint delta exceeds safe envelope: {name}")
            speed = delta / (command.duration_ms / 1000.0)
            if speed > self.max_joint_speed_rad_s:
                raise PermissionError(f"joint speed exceeds safe envelope: {name}")

    @staticmethod
    def _parse_command(intent: ActionIntent) -> HumanoidPoseCommand:
        if set(intent.goal) != {"joint_positions", "duration_ms"}:
            raise ValueError("humanoid pose goal has an invalid payload")
        raw_positions = intent.goal["joint_positions"]
        duration = intent.goal["duration_ms"]
        if not isinstance(raw_positions, dict) or not raw_positions:
            raise ValueError("joint_positions must be a non-empty object")
        if len(raw_positions) > 4:
            raise PermissionError("a bounded pose may command at most four joints")
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise ValueError("duration_ms must be an integer")
        if not 500 <= duration <= 4_000:
            raise PermissionError("duration_ms is outside the safe envelope")
        positions: dict[str, float] = {}
        for name, value in raw_positions.items():
            if name not in G1_ARM_JOINT_LIMITS_RAD:
                raise PermissionError(f"joint is not in the upper-body whitelist: {name}")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"joint target must be numeric: {name}")
            if not math.isfinite(float(value)):
                raise ValueError(f"joint target must be finite: {name}")
            positions[name] = float(value)
        return HumanoidPoseCommand(positions, duration)
