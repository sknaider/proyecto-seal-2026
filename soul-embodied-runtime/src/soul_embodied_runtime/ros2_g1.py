"""ROS 2 transport for the fixed-base Unitree G1 simulation adapter."""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from .humanoid import HumanoidPoseCommand, JointStateSample


class Ros2G1JointTransport:
    def __init__(self, *, node_name: str = "soul_g1_sim_adapter") -> None:
        import rclpy
        from sensor_msgs.msg import JointState

        self._rclpy = rclpy
        self._joint_state_type = JointState
        rclpy.init()
        self._node = rclpy.create_node(node_name)
        self._publisher = self._node.create_publisher(
            JointState, "/g1/joint_commands", 10
        )
        self._latest: JointStateSample | None = None
        self._state_sequence = 0
        self._last_targets: dict[str, float] = {}
        self._hold_error_max_rad: float | None = None
        self._active_heartbeat_timeout_ms = 500

        def on_state(message: JointState) -> None:
            if len(message.name) != len(message.position):
                return
            if not all(math.isfinite(float(value)) for value in message.position):
                return
            self._latest = JointStateSample(
                positions={
                    name: float(position)
                    for name, position in zip(
                        message.name, message.position, strict=True
                    )
                },
                observed_at=datetime.now(timezone.utc),
            )
            self._state_sequence += 1

        self._node.create_subscription(
            JointState, "/g1/joint_states", on_state, 10
        )

    def wait_for_joint_state(self, timeout_s: float = 5.0) -> JointStateSample:
        deadline = time.monotonic() + timeout_s
        while self._latest is None and time.monotonic() < deadline:
            self._rclpy.spin_once(self._node, timeout_sec=0.1)
        if self._latest is None:
            raise RuntimeError("no /g1/joint_states received before timeout")
        return self._latest

    def latest_joint_state(self) -> JointStateSample | None:
        self._rclpy.spin_once(self._node, timeout_sec=0.05)
        return self._latest

    def topic_names(self, discovery_s: float = 1.0) -> tuple[str, ...]:
        deadline = time.monotonic() + discovery_s
        while time.monotonic() < deadline:
            self._rclpy.spin_once(self._node, timeout_sec=0.1)
        return tuple(sorted(name for name, _ in self._node.get_topic_names_and_types()))

    def _publish(self, positions: dict[str, float]) -> None:
        message = self._joint_state_type()
        message.name = list(positions)
        message.position = [positions[name] for name in message.name]
        self._publisher.publish(message)

    def run_pose(
        self,
        command: HumanoidPoseCommand,
        *,
        heartbeat_timeout_ms: int,
    ) -> JointStateSample:
        initial = self.latest_joint_state()
        if initial is None:
            raise RuntimeError("joint-state heartbeat unavailable")
        starts = {name: initial.positions[name] for name in command.joint_positions}
        self._last_targets = dict(command.joint_positions)
        self._active_heartbeat_timeout_ms = heartbeat_timeout_ms
        started = time.monotonic()
        duration_s = command.duration_ms / 1000.0
        deadline = started + duration_s
        while time.monotonic() < deadline:
            self._rclpy.spin_once(self._node, timeout_sec=0.01)
            latest = self._latest
            if latest is None:
                raise RuntimeError("joint-state heartbeat unavailable")
            age_ms = (
                datetime.now(timezone.utc) - latest.observed_at
            ).total_seconds() * 1000
            if age_ms > heartbeat_timeout_ms:
                raise RuntimeError("joint-state heartbeat lost")
            alpha = min(1.0, (time.monotonic() - started) / duration_s)
            interpolated = {
                name: starts[name] + (target - starts[name]) * alpha
                for name, target in command.joint_positions.items()
            }
            self._publish(interpolated)
            time.sleep(1.0 / 60.0)
        self.hold("bounded_pose_complete")
        final = self.latest_joint_state()
        if final is None:
            raise RuntimeError("final joint state unavailable")
        return final

    def hold(self, reason: str) -> None:
        del reason
        if not self._last_targets:
            return
        self._hold_error_max_rad = None
        started = time.monotonic()
        hold_started_at = datetime.now(timezone.utc)
        start_sequence = self._state_sequence
        last_measured_sequence = start_sequence
        deadline = started + 0.5
        errors: list[float] = []
        while time.monotonic() < deadline:
            self._publish(self._last_targets)
            self._rclpy.spin_once(self._node, timeout_sec=0.01)
            latest = self._latest
            if (
                latest is not None
                and time.monotonic() - started >= 0.25
                and self._state_sequence > last_measured_sequence
                and self._state_sequence > start_sequence
                and latest.observed_at >= hold_started_at
                and (
                    datetime.now(timezone.utc) - latest.observed_at
                ).total_seconds() * 1000 <= self._active_heartbeat_timeout_ms
            ):
                values = [
                    abs(latest.positions[name] - target)
                    for name, target in self._last_targets.items()
                    if name in latest.positions
                    and math.isfinite(latest.positions[name])
                ]
                if len(values) == len(self._last_targets):
                    errors.append(max(values))
                    last_measured_sequence = self._state_sequence
            time.sleep(1.0 / 60.0)
        if errors:
            self._hold_error_max_rad = max(errors)

    def hold_error_max_rad(self) -> float | None:
        return self._hold_error_max_rad

    def close(self) -> None:
        self.hold("transport_close")
        self._node.destroy_node()
        self._rclpy.shutdown()

    def __enter__(self) -> "Ros2G1JointTransport":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
