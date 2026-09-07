#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ronda2_manager.py — Detección de META por CÁMARA (verde) + arranque de la
                    RONDA 2 con RUTA OPTIMIZADA (BFS). (ALICE, 2026-07-15)

FLUJO (lo que hace, de verdad, no un facade):
  1. Escucha la cámara en /image_raw.
  2. Detecta la META por COLOR VERDE (HSV + contornos, igual técnica que el
     pare_detector usa para el rojo, espejada a verde).
  3. Cuando confirma el verde (debounce de N frames, anti-falso-positivo),
     publica /meta_verde=True y ARRANCA UN TEMPORIZADOR de 10 segundos.
  4. A los 10 s: pasa a RONDA 2 → carga el mapa aprendido en la Ronda 1
     (maze_planner.load_map), calcula la RUTA MÁS CORTA con BFS
     (shortest_path) y publica los waypoints de esa ruta + /ronda2_start=True.

HONESTO (para la defensa): este nodo IMPLEMENTA el diseño (detección verde,
temporizador, cálculo BFS de la ruta). NO fue probado en el robot físico (el
hardware falló). Presentalo como "implementado, listo para integrar", no como
"probado en cancha". La ejecución de los waypoints por el robot (que MANEJE la
ruta) es el paso de integración con maze_solver que queda pendiente; acá se
CALCULA y se PUBLICA la ruta óptima.

Uso (sin colcon):
    PYTHONPATH=. python3 -m capytown_granprix_pkg.ronda2_manager
Parámetros ROS: image_topic (/image_raw), meta_delay_s (10.0),
                map_path (/tmp/granprix_map.json), debounce_frames (5).
"""
import os
import sys
import collections

try:
    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import Image
    from std_msgs.msg import Bool, String
    _HAVE_ROS = True
except Exception:
    _HAVE_ROS = False

try:
    import cv2
    import numpy as np
    _HAVE_CV = True
except Exception:
    _HAVE_CV = False

# Importa el planner BFS (mismo paquete o ruta local)
try:
    from capytown_granprix_pkg.maze_planner import MazePlanner
except Exception:
    try:
        from maze_planner import MazePlanner
    except Exception:
        MazePlanner = None

# ── Rango HSV del VERDE (OpenCV H en [0,179]; el verde NO envuelve el 0) ──
# Ajustable en cancha con --ros-args si la luz cambia.
HSV_GREEN_LO = (35, 80, 60)
HSV_GREEN_HI = (85, 255, 255)


def detect_green_meta(bgr_image, min_area=400):
    """Devuelve (detectado: bool, area: float, contorno). Técnica clásica HSV
    + contornos — barata, corre bien en un Pi 5. Espeja detect_red_sign del
    pare_detector, pero para verde (un solo rango, el verde no envuelve H=0)."""
    if not _HAVE_CV or bgr_image is None:
        return False, 0.0, None
    hsv = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(HSV_GREEN_LO), np.array(HSV_GREEN_HI))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best, best_area = None, 0.0
    for c in cnts:
        a = cv2.contourArea(c)
        if a >= min_area and a > best_area:
            best, best_area = c, a
    return (best is not None), best_area, best


if _HAVE_ROS:
    class Ronda2Manager(Node):
        def __init__(self):
            super().__init__("ronda2_manager")
            self.image_topic = self.declare_parameter("image_topic", "/image_raw").value
            self.meta_delay_s = float(self.declare_parameter("meta_delay_s", 10.0).value)
            self.map_path = self.declare_parameter("map_path", "/tmp/granprix_map.json").value
            self.debounce = int(self.declare_parameter("debounce_frames", 5).value)

            self._recent = collections.deque(maxlen=max(1, self.debounce))
            self.meta_confirmada = False
            self.ronda2_iniciada = False
            self._t_meta = None

            # cv_bridge opcional (si no está, decodifica manual)
            try:
                from cv_bridge import CvBridge
                self._bridge = CvBridge()
            except Exception:
                self._bridge = None

            self.pub_meta = self.create_publisher(Bool, "/meta_verde", 10)
            self.pub_r2 = self.create_publisher(Bool, "/ronda2_start", 10)
            self.pub_route = self.create_publisher(String, "/ronda2_ruta", 10)
            self.sub_img = self.create_subscription(Image, self.image_topic, self.on_image, 10)
            self.timer = self.create_timer(0.5, self.tick)
            self.get_logger().info(
                f"Ronda2Manager listo: cámara={self.image_topic}, "
                f"delay={self.meta_delay_s}s, mapa={self.map_path}")

        def _to_bgr(self, msg):
            if self._bridge is not None:
                try:
                    return self._bridge.imgmsg_to_cv2(msg, "bgr8")
                except Exception:
                    pass
            if not _HAVE_CV:
                return None
            try:
                arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, -1)
                if msg.encoding in ("rgb8", "rgb"):
                    arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
                return arr
            except Exception:
                return None

        def on_image(self, msg):
            if self.meta_confirmada:
                return
            bgr = self._to_bgr(msg)
            found, area, _ = detect_green_meta(bgr)
            self._recent.append(1 if found else 0)
            # DEBOUNCE: la MAYORÍA de los últimos N frames deben ver verde
            if len(self._recent) == self._recent.maxlen and sum(self._recent) > self._recent.maxlen // 2:
                self.confirmar_meta()

        def confirmar_meta(self):
            self.meta_confirmada = True
            self._t_meta = self.now_s()
            self.pub_meta.publish(Bool(data=True))
            self.get_logger().info(
                f"✅ META VERDE detectada por cámara. Ronda 2 arranca en {self.meta_delay_s:.0f}s…")

        def now_s(self):
            return self.get_clock().now().nanoseconds * 1e-9

        def tick(self):
            # cuenta regresiva de 10 s tras la meta -> arranca Ronda 2
            if self.meta_confirmada and not self.ronda2_iniciada:
                if (self.now_s() - self._t_meta) >= self.meta_delay_s:
                    self.iniciar_ronda2()

        def iniciar_ronda2(self):
            self.ronda2_iniciada = True
            self.get_logger().info("🏁 RONDA 2 — calculando ruta óptima (BFS)…")
            ruta = self.calcular_ruta_optima()
            self.pub_r2.publish(Bool(data=True))
            if ruta:
                self.pub_route.publish(String(data=str(ruta)))
                self.get_logger().info(f"Ruta óptima (celdas): {ruta}  ·  {len(ruta)-1} pasos")
            else:
                self.get_logger().warn(
                    "No se pudo calcular ruta (¿mapa de Ronda 1 no guardado en %s?)." % self.map_path)

        def calcular_ruta_optima(self):
            if MazePlanner is None:
                self.get_logger().warn("maze_planner no disponible.")
                return None
            planner = MazePlanner()
            if not planner.load_map(self.map_path):
                return None
            return planner.shortest_path()   # BFS -> lista de celdas de INICIO a META

        def main(args=None):
            rclpy.init(args=args)
            node = Ronda2Manager()
            try:
                rclpy.spin(node)
            except KeyboardInterrupt:
                pass
            finally:
                node.destroy_node()
                rclpy.shutdown()
else:
    def main(args=None):
        raise SystemExit("ROS2 (rclpy) no disponible en este entorno.")


if __name__ == "__main__":
    main()
