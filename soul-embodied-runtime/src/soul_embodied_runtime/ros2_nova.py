"""ROS 2 transport for the simulation-only Nova Carter adapter.

Imports ROS lazily so the safety core remains testable without ROS installed.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from .simulation import OdometrySample, TimedDriveCommand


class Ros2NovaVelocityTransport:
    def __init__(self, *, node_name: str = "soul_nova_sim_adapter") -> None:
        import rclpy
        from geometry_msgs.msg import Twist
        from nav_msgs.msg import Odometry

        self._rclpy = rclpy
        self._twist_type = Twist
        rclpy.init()
        self._node = rclpy.create_node(node_name)
        self._publisher = self._node.create_publisher(Twist, "/cmd_vel", 10)
        self._latest: OdometrySample | None = None

        def on_odom(msg: Odometry) -> None:
            p = msg.pose.pose.position
            q = msg.pose.pose.orientation
            siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
            cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
            self._latest = OdometrySample(
                x=float(p.x),
                y=float(p.y),
                yaw=math.atan2(siny_cosp, cosy_cosp),
                observed_at=datetime.now(timezone.utc),
            )

        self._node.create_subscription(Odometry, "/chassis/odom", on_odom, 10)

    def wait_for_odometry(self, timeout_s: float = 5.0) -> OdometrySample:
        deadline = time.monotonic() + timeout_s
        while self._latest is None and time.monotonic() < deadline:
            self._rclpy.spin_once(self._node, timeout_sec=0.1)
        if self._latest is None:
            raise RuntimeError("no /chassis/odom received before timeout")
        return self._latest

    def latest_odometry(self) -> OdometrySample | None:
        self._rclpy.spin_once(self._node, timeout_sec=0.05)
        return self._latest

    def topic_names(self, discovery_s: float = 2.0) -> tuple[str, ...]:
        deadline = time.monotonic() + discovery_s
        while time.monotonic() < deadline:
            self._rclpy.spin_once(self._node, timeout_sec=0.1)
        return tuple(sorted(name for name, _ in self._node.get_topic_names_and_types()))

    def run_velocity(
        self,
        command: TimedDriveCommand,
        *,
        heartbeat_timeout_ms: int,
    ) -> OdometrySample:
        msg = self._twist_type()
        msg.linear.x = command.linear_m_s
        msg.angular.z = command.angular_rad_s
        deadline = time.monotonic() + command.duration_ms / 1000.0
        try:
            while time.monotonic() < deadline:
                self._rclpy.spin_once(self._node, timeout_sec=0.025)
                latest = self._latest
                if latest is None:
                    raise RuntimeError("odometry heartbeat unavailable")
                age_ms = (
                    datetime.now(timezone.utc) - latest.observed_at
                ).total_seconds() * 1000
                if age_ms > heartbeat_timeout_ms:
                    raise RuntimeError("odometry heartbeat lost")
                self._publisher.publish(msg)
                time.sleep(0.025)
        finally:
            self.stop("bounded_action_complete")
        latest = self.latest_odometry()
        if latest is None:
            raise RuntimeError("final odometry unavailable")
        return latest

    def stop(self, reason: str) -> None:
        del reason
        zero = self._twist_type()
        deadline = time.monotonic() + 0.75
        while time.monotonic() < deadline:
            self._publisher.publish(zero)
            self._rclpy.spin_once(self._node, timeout_sec=0.025)
            time.sleep(0.025)

    def close(self) -> None:
        self.stop("transport_close")
        self._node.destroy_node()
        self._rclpy.shutdown()

    def __enter__(self) -> "Ros2NovaVelocityTransport":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
