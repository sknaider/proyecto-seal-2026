#!/usr/bin/env python3
"""Visualizador de detecciones LiDAR para defensa RC-4.

Nodo aparte: no publica /cmd_vel ni toca la navegacion. Solo escucha /scan,
grafica la nube 2D del LiDAR y marca las cajas que detecta box_detector.
"""
from __future__ import annotations

import math
import os
import time

try:
    import matplotlib
    if not os.environ.get("DISPLAY"):
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception as exc:  # pragma: no cover - depende del robot
    plt = None
    _PLOT_IMPORT_ERROR = exc
else:
    _PLOT_IMPORT_ERROR = None

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String

from capytown_maze_pkg.box_detector import segment_scan


class LidarVisualizer(Node):
    def __init__(self):
        super().__init__("lidar_visualizer")
        self.declare_parameter("scan_topic", "/scan")
        self.declare_parameter("state_topic", "/capytown_state")
        self.declare_parameter("save_path", "/tmp/lidar_detection.png")
        self.declare_parameter("save_period", 1.0)
        self.declare_parameter("show_window", bool(os.environ.get("DISPLAY")))
        self.declare_parameter("plot_range", 2.0)
        self.declare_parameter("box_memory_t", 2.5)

        self.scan_topic = self.get_parameter("scan_topic").value
        self.state_topic = self.get_parameter("state_topic").value
        self.save_path = str(self.get_parameter("save_path").value)
        self.save_period = float(self.get_parameter("save_period").value)
        self.show_window = bool(self.get_parameter("show_window").value)
        self.plot_range = float(self.get_parameter("plot_range").value)
        self.box_memory_t = float(self.get_parameter("box_memory_t").value)
        self.scan = None
        self.robot_state = "SIN ESTADO"
        self.last_save = 0.0
        self.box_memory = []

        if plt is None:
            self.get_logger().error(
                "No se pudo importar matplotlib. Instalar: sudo apt install python3-matplotlib. "
                f"Error: {_PLOT_IMPORT_ERROR}")
        else:
            self.fig, self.ax = plt.subplots(figsize=(6, 6))
            if self.show_window:
                plt.ion()
                plt.show(block=False)

        self.create_subscription(LaserScan, self.scan_topic, self._cb_scan, qos_profile_sensor_data)
        self.create_subscription(String, self.state_topic, self._cb_state, 10)
        self.create_timer(0.2, self._tick)
        self.get_logger().info(
            f"lidar_visualizer listo: scan={self.scan_topic}, ventana={self.show_window}, "
            f"png={self.save_path}")

    def _cb_scan(self, msg):
        self.scan = msg

    def _cb_state(self, msg):
        self.robot_state = str(msg.data)

    def _tick(self):
        if plt is None or self.scan is None:
            return
        s = self.scan
        # Clustering 1D: cada objeto (pared/caja) = un clúster continuo. Un color por clúster.
        clusters = segment_scan(
            list(s.ranges), s.angle_min, s.angle_increment, s.range_min, s.range_max)

        self.ax.clear()
        now = time.time()
        n_box = sum(1 for cl in clusters if cl.get("status") == "confirmada")
        self._update_box_memory(clusters, now)
        box_labeled = False
        tentative_labeled = False
        non_box_i = 0
        wall_cmap = plt.get_cmap("Blues")
        for cl in clusters:
            pts = [(x, y) for (x, y) in cl["points"] if math.hypot(x, y) <= self.plot_range]
            if not pts:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            status = cl.get("status", "pared")
            if status == "confirmada":
                self.ax.scatter(xs, ys, s=60, color="red", marker="o",
                                edgecolors="black", linewidths=0.5,
                                label=("Caja confirmada" if not box_labeled else None))
                box_labeled = True
            elif status == "candidato":
                self.ax.scatter(xs, ys, s=42, color="#ff9d00", marker="o",
                                edgecolors="black", linewidths=0.35,
                                label=("Caja no confirmada" if not tentative_labeled else None))
                tentative_labeled = True
            else:
                non_box_i += 1
                shade = 0.35 + 0.55 * (((non_box_i - 1) % 10) / 9.0)
                self.ax.scatter(xs, ys, s=14, color=wall_cmap(shade),
                                marker="o", label=f"No caja {non_box_i}")
        memory_labeled = box_labeled
        for item in self.box_memory:
            pts = [(x, y) for (x, y) in item["points"] if math.hypot(x, y) <= self.plot_range]
            if not pts:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            self.ax.scatter(xs, ys, s=34, color="#ff3333", marker="o",
                            alpha=0.45, edgecolors="none",
                            label=("Caja reciente" if not memory_labeled else None))
            memory_labeled = True
        # Robot / LiDAR en el origen
        self.ax.scatter([0], [0], s=110, c="white", edgecolors="black", linewidths=1.2,
                        marker="^", zorder=5, label="Robot/LiDAR")
        self.ax.set_title(
            f"ESTADO: {self.robot_state} | rojo=caja, naranja=no confirmada, azul=pared (cajas={n_box})",
            fontsize=11)
        self.ax.set_xlabel("x LiDAR (m)")
        self.ax.set_ylabel("y LiDAR (m)")
        self.ax.set_xlim(-self.plot_range, self.plot_range)
        self.ax.set_ylim(-self.plot_range, self.plot_range)
        self.ax.set_aspect("equal", adjustable="box")
        self.ax.grid(True, alpha=0.3)
        self.ax.legend(loc="upper right", fontsize=8)

        if self.show_window:
            self.fig.canvas.draw_idle()
            self.fig.canvas.flush_events()
        if self.save_path and now - self.last_save >= self.save_period:
            os.makedirs(os.path.dirname(self.save_path) or ".", exist_ok=True)
            self.fig.savefig(self.save_path, dpi=130)
            self.last_save = now

    def _update_box_memory(self, clusters, now):
        self.box_memory = [item for item in self.box_memory if item["expires"] > now]
        for cl in clusters:
            if cl.get("status") != "confirmada":
                continue
            pts = list(cl.get("points", []))
            if not pts:
                continue
            cx = sum(x for x, _y in pts) / len(pts)
            cy = sum(y for _x, y in pts) / len(pts)
            merged = False
            for item in self.box_memory:
                ix, iy = item["center"]
                if math.hypot(cx - ix, cy - iy) <= 0.20:
                    item["points"] = pts
                    item["center"] = (cx, cy)
                    item["expires"] = now + self.box_memory_t
                    merged = True
                    break
            if not merged:
                self.box_memory.append({
                    "points": pts,
                    "center": (cx, cy),
                    "expires": now + self.box_memory_t,
                })


def main(args=None):
    rclpy.init(args=args)
    node = LidarVisualizer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
