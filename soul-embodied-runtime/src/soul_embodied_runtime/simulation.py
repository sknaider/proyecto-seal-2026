"""Simulation-only Nova Carter adapter behind the SOUL authorization boundary.

This module deliberately has no ROS dependency.  A ROS transport can be plugged in,
but only an already-authorized, bounded high-level intent reaches it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from .audit import AuditChain
from .authorization import AuthorizationVerifier, SignedAuthorization
from .contracts import ActionIntent


NOVA_TIMED_DRIVE = "mobility.drive_timed"


@dataclass(frozen=True)
class OdometrySample:
    x: float
    y: float
    yaw: float
    observed_at: datetime


@dataclass(frozen=True)
class TimedDriveCommand:
    linear_m_s: float
    angular_rad_s: float
    duration_ms: int


@dataclass(frozen=True)
class SimulationMotionResult:
    action_handle: str
    initial: OdometrySample
    final: OdometrySample
    command: TimedDriveCommand

    @property
    def displacement_m(self) -> float:
        return math.hypot(self.final.x - self.initial.x, self.final.y - self.initial.y)


class VelocityTransport(Protocol):
    def latest_odometry(self) -> OdometrySample | None: ...

    def run_velocity(
        self,
        command: TimedDriveCommand,
        *,
        heartbeat_timeout_ms: int,
    ) -> OdometrySample: ...

    def stop(self, reason: str) -> None: ...


@dataclass
class NovaCarterSimulationAdapter:
    """Authorized adapter for the Nova Carter *simulation*, never hardware."""

    body_id: str
    verifier: AuthorizationVerifier
    transport: VelocityTransport
    audit: AuditChain
    max_angular_rad_s: float = 0.8
    max_odometry_age_ms: int = 500
    max_clock_skew_ms: int = 100
    heartbeat_timeout_ms: int = 500
    safe: bool = True
    clock: Callable[[], datetime] = field(
        default_factory=lambda: lambda: datetime.now(timezone.utc), repr=False
    )

    def __post_init__(self) -> None:
        if not self.body_id.endswith("-sim-01"):
            raise ValueError("simulation adapter requires a simulation-only body_id")
        if self.max_angular_rad_s <= 0:
            raise ValueError("max_angular_rad_s must be positive")
        self._prepared: dict[
            str, tuple[TimedDriveCommand, OdometrySample, datetime]
        ] = {}
        self._executed: set[str] = set()
        self._cancelled: set[str] = set()

    def discover(self) -> dict[str, object]:
        return {
            "body_id": self.body_id,
            "kind": "nova_carter_isaac_sim",
            "capabilities": [NOVA_TIMED_DRIVE],
            "safe": self.safe,
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
            raise PermissionError("intent body does not match simulation body")
        if intent.capability != NOVA_TIMED_DRIVE:
            raise PermissionError("capability is not supported by Nova Carter simulation")
        bounded = authorization.authorization
        if bounded.issuer != intent.issuer:
            raise PermissionError("authorization issuer does not match intent issuer")
        if bounded.allowed_capability != intent.capability:
            raise PermissionError("authorization capability does not match intent capability")
        ttl_ms = (bounded.expires_at - bounded.not_before).total_seconds() * 1000
        if ttl_ms > 5_000:
            raise PermissionError("authorization lease exceeds simulation maximum")

        command = self._parse_command(intent)
        limits = bounded.effective_constraints
        if abs(command.linear_m_s) > limits.max_speed_m_s:
            raise PermissionError("linear speed exceeds signed authorization")
        if abs(command.angular_rad_s) > self.max_angular_rad_s:
            raise PermissionError("angular speed exceeds simulation body ceiling")
        if command.duration_ms > limits.max_duration_ms:
            raise PermissionError("duration exceeds signed authorization")
        remaining_ms = (bounded.expires_at - now).total_seconds() * 1000
        if remaining_ms < command.duration_ms + 250:
            raise PermissionError("authorization lease cannot cover timed drive")

        initial = self.transport.latest_odometry()
        if initial is None:
            raise RuntimeError("odometry is stale or unavailable: no sample")
        self._validate_odometry(initial, now)

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
        self._prepared[handle] = (command, initial, execute_deadline)
        self.audit.append(
            "simulation.prepared",
            intent.intent_id,
            {
                "body_id": self.body_id,
                "capability": intent.capability,
                "intent_hash": intent.digest(),
                "authorization_id": authorization.authorization.authorization_id,
                "linear_m_s": command.linear_m_s,
                "angular_rad_s": command.angular_rad_s,
                "duration_ms": command.duration_ms,
            },
        )
        return handle

    def execute(self, prepared_action: str) -> SimulationMotionResult:
        if not self.safe:
            raise RuntimeError("simulation body is in safe state")
        if prepared_action not in self._prepared:
            raise ValueError("unknown prepared action")
        if prepared_action in self._cancelled:
            raise RuntimeError("prepared action was cancelled")
        if prepared_action in self._executed:
            raise RuntimeError("prepared action already executed")

        command, initial, execute_deadline = self._prepared[prepared_action]
        now = self.clock()
        if now > execute_deadline:
            self.audit.append(
                "simulation.execution_denied",
                prepared_action,
                {"reason": "prepared_action_expired"},
            )
            raise PermissionError("prepared action expired before execution")
        current = self.transport.latest_odometry()
        if current is None:
            raise RuntimeError("odometry is stale or unavailable before execution")
        self._validate_odometry(current, now)
        initial = current
        self._executed.add(prepared_action)
        action_handle = prepared_action.replace("prepared:", "action:", 1)
        try:
            final = self.transport.run_velocity(
                command,
                heartbeat_timeout_ms=self.heartbeat_timeout_ms,
            )
            self._validate_odometry(final, self.clock())
        except Exception:
            self.transport.stop("execution_fault")
            self.audit.append(
                "simulation.safe_stop",
                action_handle,
                {"reason": "execution_fault"},
            )
            raise

        result = SimulationMotionResult(action_handle, initial, final, command)
        self.audit.append(
            "simulation.completed",
            action_handle,
            {
                "initial": {"x": initial.x, "y": initial.y, "yaw": initial.yaw},
                "final": {"x": final.x, "y": final.y, "yaw": final.yaw},
                "displacement_m": result.displacement_m,
                "stopped": True,
            },
        )
        return result

    def cancel(self, action_handle: str, reason: str) -> dict[str, str]:
        prepared = action_handle.replace("action:", "prepared:", 1)
        self._cancelled.add(prepared)
        self.transport.stop(reason)
        self.audit.append("simulation.cancelled", action_handle, {"reason": reason})
        return {"type": "cancelled", "handle": action_handle, "reason": reason}

    def safe_state(self, reason: str) -> dict[str, str]:
        self.safe = False
        self.transport.stop(reason)
        self.audit.append("simulation.safe_state", self.body_id, {"reason": reason})
        return {"type": "safe_state", "reason": reason}

    def _fresh(self, sample: OdometrySample, now: datetime) -> bool:
        age_ms = (now - sample.observed_at).total_seconds() * 1000
        return -self.max_clock_skew_ms <= age_ms <= self.max_odometry_age_ms

    def _validate_odometry(self, sample: OdometrySample, now: datetime) -> None:
        if sample.observed_at.tzinfo is None or now.tzinfo is None:
            raise RuntimeError("odometry and edge clock must be timezone-aware")
        if not all(math.isfinite(value) for value in (sample.x, sample.y, sample.yaw)):
            raise RuntimeError("odometry is non-finite")
        if not self._fresh(sample, now):
            age_ms = (now - sample.observed_at).total_seconds() * 1000
            raise RuntimeError(
                f"odometry is stale or unavailable: age_ms={age_ms:.1f} "
                f"limit_ms={self.max_odometry_age_ms}"
            )

    @staticmethod
    def _parse_command(intent: ActionIntent) -> TimedDriveCommand:
        expected = {"linear_m_s", "angular_rad_s", "duration_ms"}
        if set(intent.goal) != expected:
            raise ValueError("timed drive goal has an invalid payload")
        linear = intent.goal["linear_m_s"]
        angular = intent.goal["angular_rad_s"]
        duration = intent.goal["duration_ms"]
        if isinstance(linear, bool) or not isinstance(linear, (int, float)):
            raise ValueError("linear_m_s must be numeric")
        if isinstance(angular, bool) or not isinstance(angular, (int, float)):
            raise ValueError("angular_rad_s must be numeric")
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise ValueError("duration_ms must be an integer")
        if not math.isfinite(float(linear)) or not math.isfinite(float(angular)):
            raise ValueError("motion values must be finite")
        if duration <= 0:
            raise ValueError("duration_ms must be positive")
        return TimedDriveCommand(float(linear), float(angular), duration)
