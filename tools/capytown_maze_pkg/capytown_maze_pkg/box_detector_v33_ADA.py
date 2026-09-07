#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
box_detector.py — Censo de cajas para el reto "El Censo y el Guardián de las Cajas" (JARVIS).
==============================================================================================
Pieza confirmada por la arquitectura oficial: LiDAR→/scan→[box_detector]→/cajas_avistadas→[fsm].
Detecta las CAJAS (20x20cm) montadas en las paredes del circuito y las CENSA (cuenta las 5: C1-C5)
sin duplicar, usando /odom para deduplicar por posición en el mundo.

Enchufa al paquete SIN tocar maze_navigator.py de NEXUS (nodo separado).

Algoritmo (solo /scan + /odom, numpy, apto edge):
  1. Segmenta el /scan en tramos continuos (saltos de rango = bordes).
  2. Una CAJA = un tramo cuya ANCHURA física ≈ 0.20m (no una pared larga) y que SALE de la línea
     de pared (protuberancia discreta). Se calcula la anchura = distancia * (Δángulo del tramo).
  3. Proyecta el centro de la caja a coordenadas del MUNDO (con la pose /odom) y deduplica:
     una caja nueva solo cuenta si está a > BOX_MERGE_DIST de las ya vistas.
  4. Publica /cajas_avistadas (std_msgs/Int32 = total) — el FSM lo consume.
"""
from __future__ import annotations
import math
import os
import csv

try:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import LaserScan
    from nav_msgs.msg import Odometry
    from std_msgs.msg import Int32
    from geometry_msgs.msg import PoseArray, Pose   # IMP2: publica POSICIONES del censo
    _HAVE_ROS = True
except Exception:
    _HAVE_ROS = False

# NOTA: las vueltas las cuenta maze_navigator (loop_completion) y las anuncia por /capytown_lap;
# box_detector NO auto-detecta la vuelta (lo consume en _cb_lap). Sin import de LoopCompletion aquí.

# ── Parámetros (sintonizables) ──
BOX_SIZE = 0.17          # m, lado real de la caja RC-4 (17x17cm, confirmado por Henry)
BOX_TOL = 0.04           # m, tolerancia ESTRICTA (caja 13-21cm); áreas mayores = pared, no caja (Henry)
RANGE_JUMP = 0.12        # m, salto de rango que separa segmentos (borde)
BOX_MERGE_DIST = 0.40    # m, las cajas reales están separadas >=40cm; menor = misma caja
MIN_PROTRUSION = 0.08    # m, la caja debe sobresalir claramente respecto a sus vecinos
MIN_BOX_POINTS = 3       # evita rayos sueltos/ruido como caja
MAX_BOX_POINTS = 40      # evita tramos largos de pared fragmentada
DETECT_MAX_DIST = 1.20   # solo censar objetos cercanos; paredes lejanas generan FP
DETECT_SECTOR_DEG = 120  # sector frontal util (+/-); ignora basura trasera del scan 360
MAX_BOXES_PER_LAP = 5    # RC-4 tiene 5 cajas; cap defensivo contra FP acumulados


def yaw_from_quat(x, y, z, w):
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def detect_boxes_in_scan(ranges, angle_min, angle_inc, range_min, range_max,
                         *, box_size=BOX_SIZE, box_tol=BOX_TOL, range_jump=RANGE_JUMP,
                         min_protrusion=MIN_PROTRUSION, min_points=MIN_BOX_POINTS,
                         max_points=MAX_BOX_POINTS, max_dist=DETECT_MAX_DIST,
                         sector_deg=DETECT_SECTOR_DEG):
    """Pure/testable. Devuelve lista de (dist, ang) de cajas candidatas en el frame del LiDAR.
    Los umbrales son parámetros (default = constantes del módulo) para que el nodo los exponga
    como ROS params y Henry pueda afinar FP=0 en vivo (--ros-args -p box_tol:=0.05 ...)."""
    import numpy as np
    r = np.asarray(ranges, dtype=float)
    valid = np.isfinite(r) & (r >= range_min) & (r <= range_max)
    boxes = []
    n = len(r)
    i = 0
    while i < n:
        ang_i = angle_min + i * angle_inc
        if abs(math.degrees(math.atan2(math.sin(ang_i), math.cos(ang_i)))) > sector_deg:
            i += 1; continue
        if not valid[i]:
            i += 1; continue
        # crece un segmento continuo (sin saltos de rango)
        j = i
        while j + 1 < n and valid[j + 1] and abs(r[j + 1] - r[j]) < range_jump:
            j += 1
        seg = r[i:j + 1]
        if min_points <= len(seg) <= max_points:
            mid = (i + j) // 2
            ang = angle_min + mid * angle_inc
            if abs(math.degrees(math.atan2(math.sin(ang), math.cos(ang)))) > sector_deg:
                i = j + 1
                continue
            dist = float(np.median(seg))
            if dist > max_dist:
                i = j + 1
                continue
            width = dist * (len(seg) * angle_inc)              # anchura física del tramo
            # vecinos a los lados del segmento (fondo de pared)
            left = r[i - 1] if i - 1 >= 0 and valid[i - 1] else np.inf
            right = r[j + 1] if j + 1 < n and valid[j + 1] else np.inf
            # No aceptar segmentos pegados a huecos/invalidos: eso suele ser borde de scan,
            # sombra de pared o ruido, y fue la causa de "20+ cajas".
            if not (math.isfinite(left) and math.isfinite(right)):
                i = j + 1
                continue
            protrudes = (left - dist) > min_protrusion and (right - dist) > min_protrusion
            is_box = (abs(width - box_size) <= box_tol) and protrudes
            if is_box:
                boxes.append((dist, ang))
        i = j + 1
    return boxes


def segment_scan(ranges, angle_min, angle_inc, range_min, range_max,
                 *, range_jump=RANGE_JUMP, box_size=0.17, box_tol=0.08,
                 min_protrusion=MIN_PROTRUSION, min_points=MIN_BOX_POINTS,
                 max_points=MAX_BOX_POINTS, max_dist=DETECT_MAX_DIST,
                 corner_dist=0.42, max_box_extent=0.34, max_box_group_extent=0.48):
    """Pure/testable. Segmenta TODO el /scan en clústeres continuos (clustering 1D) y les asigna
    un ESTADO de 3 niveles para la visualización (modelo de Henry):
      • 'pared'      : segmento LARGO o de ancho no-caja (lo normal) → color de pared.
      • 'candidato'  : segmento CORTO del tamaño de una cara de caja, SOLO → CAJA NO CONFIRMADA (naranja).
      • 'confirmada' : un candidato con OTRA cara-candidata adyacente (esquina) → CAJA CONFIRMADA (rojo).
    NO cambia el censo (detect_boxes_in_scan es aparte). Devuelve dicts con: points, label, status,
    is_box (criterio estricto del censo, por compat), dist, ang, width."""
    import numpy as np
    r = np.asarray(ranges, dtype=float)
    valid = np.isfinite(r) & (r >= range_min) & (r <= range_max)
    clusters = []
    n = len(r)
    i = 0
    while i < n:
        if not valid[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and valid[j + 1] and abs(r[j + 1] - r[j]) < range_jump:
            j += 1
        seg = r[i:j + 1]
        if len(seg) >= 2:
            pts = []
            for k in range(i, j + 1):
                a = angle_min + k * angle_inc
                pts.append((r[k] * math.cos(a), r[k] * math.sin(a)))
            if len(pts) >= 2:
                orientation = math.atan2(pts[-1][1] - pts[0][1],
                                         pts[-1][0] - pts[0][0])
            else:
                orientation = 0.0
            mid = (i + j) // 2
            ang = angle_min + mid * angle_inc
            dist = float(np.median(seg))
            width = dist * (len(seg) * angle_inc)
            extent_x = max(x for x, _y in pts) - min(x for x, _y in pts)
            extent_y = max(y for _x, y in pts) - min(y for _x, y in pts)
            extent = max(width, extent_x, extent_y)
            left = r[i - 1] if i - 1 >= 0 and valid[i - 1] else np.inf
            right = r[j + 1] if j + 1 < n and valid[j + 1] else np.inf
            protrudes = (left - dist) > min_protrusion and (right - dist) > min_protrusion
            # censo estricto (compat): corto + ancho caja + sobresale por ambos lados + cerca
            is_box = (min_points <= len(seg) <= max_points and dist <= max_dist
                      and abs(width - box_size) <= box_tol
                      and math.isfinite(left) and math.isfinite(right) and protrudes)
            # candidato visual = área chica/mediana compatible con una caja vista de lado,
            # de frente o en escalón/L. Es más permisivo que el censo para la demo visual.
            short = min_points <= len(seg) <= (max_points * 2) and dist <= max_dist
            box_width = (0.06 <= width <= 0.36) or abs(width - box_size) <= box_tol
            box_extent = extent <= max_box_extent
            status = "candidato" if (short and box_width and box_extent) else "pared"
            clusters.append({"points": pts, "status": status, "is_box": is_box,
                             "dist": dist, "ang": ang, "width": width,
                             "extent": extent, "orientation": orientation})
        i = j + 1
    # 2ª pasada: candidatos cercanos forman un bloque de caja si el grupo total
    # sigue siendo compacto. Esto acepta cajas escalonadas/fragmentadas del robot
    # real, pero rechaza paredes largas fragmentadas.
    cands = [c for c in clusters if c["status"] == "candidato"]
    seen = set()
    for root in cands:
        if id(root) in seen:
            continue
        stack = [root]
        group = []
        seen.add(id(root))
        while stack:
            cur = stack.pop()
            group.append(cur)
            for other in cands:
                if id(other) in seen:
                    continue
                adjacent = any(math.hypot(x1 - x2, y1 - y2) <= corner_dist
                               for (x1, y1) in cur["points"]
                               for (x2, y2) in other["points"])
                if adjacent:
                    seen.add(id(other))
                    stack.append(other)
        all_pts = [pt for item in group for pt in item["points"]]
        gx = max(x for x, _y in all_pts) - min(x for x, _y in all_pts)
        gy = max(y for _x, y in all_pts) - min(y for _x, y in all_pts)
        gextent = max(gx, gy)
        # Forma L/escalonada: la nube ocupa dos ejes, no una línea recta. Esto evita depender
        # de la orientación de la L en el mapa del LiDAR.
        l_shape = False
        if len(all_pts) >= 5:
            arr = np.asarray(all_pts, dtype=float)
            arr = arr - arr.mean(axis=0)
            cov = np.cov(arr.T)
            vals = sorted(np.linalg.eigvalsh(cov), reverse=True)
            if vals[0] > 1e-9:
                spread_ratio = vals[1] / vals[0]
                l_shape = spread_ratio >= 0.10 and gextent <= max_box_group_extent
        if len(group) >= 2 and (gextent <= max_box_group_extent or l_shape):
            for item in group:
                item["status"] = "confirmada"
        elif len(group) == 1 and l_shape:
            for item in group:
                item["status"] = "confirmada"
        else:
            for item in group:
                item["status"] = "pared"
    # etiquetas legibles
    n_conf = n_cand = n_wall = 0
    for c in clusters:
        if c["status"] == "confirmada":
            n_conf += 1
            c["label"] = f"Caja {n_conf}"
        elif c["status"] == "candidato":
            n_cand += 1
            c["label"] = f"Caja? {n_cand}"
        else:
            n_wall += 1
            c["label"] = f"Pared {n_wall}"
    return clusters


def census_metrics(detected, ground_truth, match_dist=0.30):
    """Pure/testable. Métricas de la rúbrica (IMP2/DEF3) del censo.
    detected: [(x,y), ...] cajas censadas (mundo).  ground_truth: [(x,y), ...] cajas reales (cinta).
    Matching greedy 1-a-1 por cercanía (≤ match_dist = 30cm de la rúbrica).
    Devuelve dict: VP (matcheadas), FP (detectadas sin GT), FN (GT no detectadas),
    tasa_deteccion = VP/(VP+FN), error_pos_prom (m) de los VP.
    """
    gt = list(ground_truth)
    used = [False] * len(gt)
    vp, errs = 0, []
    for (dx, dy) in detected:
        best, best_d = -1, match_dist + 1e-9
        for k, (gx, gy) in enumerate(gt):
            if used[k]:
                continue
            d = math.hypot(dx - gx, dy - gy)
            if d < best_d:
                best, best_d = k, d
        if best >= 0:
            used[best] = True
            vp += 1
            errs.append(best_d)
    fp = len(detected) - vp
    fn = len(gt) - vp
    rate = vp / (vp + fn) if (vp + fn) > 0 else 0.0
    err = (sum(errs) / len(errs)) if errs else 0.0
    return {"VP": vp, "FP": fp, "FN": fn, "tasa_deteccion": rate, "error_pos_prom": err}


if _HAVE_ROS:
    class BoxDetector(Node):
        def __init__(self):
            super().__init__("box_detector")
            self.scan = None
            self.pose = (0.0, 0.0, 0.0)
            self.seen_world = []          # cajas únicas censadas (x,y mundo)
            # Topics parametrizables: en este robot la odom es /odom_raw (no /odom). Override:
            #   ros2 run capytown_maze_pkg box_detector --ros-args -p odom_topic:=/odom_raw
            self.declare_parameter("scan_topic", "/scan")
            self.declare_parameter("odom_topic", "/odom")
            self.declare_parameter("lap_topic", "/capytown_lap")
            scan_t = self.get_parameter("scan_topic").value
            odom_t = self.get_parameter("odom_topic").value
            lap_t = self.get_parameter("lap_topic").value
            qos = qos_profile_sensor_data
            self.create_subscription(LaserScan, scan_t, self._cb_scan, qos)
            self.create_subscription(Odometry, odom_t, self._cb_odom, qos)
            self.create_subscription(Int32, lap_t, self._cb_lap, 10)
            self.get_logger().info(f"box_detector: scan={scan_t} odom={odom_t} lap={lap_t}")
            self.pub = self.create_publisher(Int32, "/cajas_avistadas", 10)          # conteo (FSM lo consume)
            self.pub_pos = self.create_publisher(PoseArray, "/cajas_avistadas_pos", 10)  # IMP2: POSICIONES
            # --- métricas de la rúbrica (IMP2/IMP4/DEF3) ---
            #   ground_truth = lista PLANA [x1,y1, x2,y2, ...] de las cajas reales (medidas con cinta, en marco odom)
            #   run_id = número de corrida (1-10) ; metrics_csv = archivo acumulado
            self.declare_parameter("ground_truth", [])          # p.ej. -p ground_truth:="[1.0,0.5, 2.0,-0.5]"
            self.declare_parameter("run_id", 0)
            self.declare_parameter("metrics_csv", "/tmp/metricas_lidar.csv")
            self.declare_parameter("detections_csv", "/tmp/capytown_box_detections.csv")
            gt_flat = list(self.get_parameter("ground_truth").value or [])
            self.ground_truth = [(gt_flat[i], gt_flat[i + 1]) for i in range(0, len(gt_flat) - 1, 2)]
            self.run_id = int(self.get_parameter("run_id").value)
            self.metrics_csv = str(self.get_parameter("metrics_csv").value)
            self.detections_csv = str(self.get_parameter("detections_csv").value)
            # --- umbrales de detección TUNABLES en vivo (afinar FP=0 sin recompilar) ---
            #   ros2 run capytown_maze_pkg box_detector --ros-args -p box_tol:=0.05 -p min_protrusion:=0.10
            self.declare_parameter("box_size", BOX_SIZE)
            self.declare_parameter("box_tol", BOX_TOL)
            self.declare_parameter("range_jump", RANGE_JUMP)
            self.declare_parameter("min_protrusion", MIN_PROTRUSION)
            self.declare_parameter("min_points", MIN_BOX_POINTS)
            self.declare_parameter("max_points", MAX_BOX_POINTS)
            self.declare_parameter("max_dist", DETECT_MAX_DIST)
            self.declare_parameter("sector_deg", DETECT_SECTOR_DEG)
            self.declare_parameter("box_merge_dist", BOX_MERGE_DIST)
            self.declare_parameter("max_boxes", MAX_BOXES_PER_LAP)
            self.det_kw = dict(
                box_size=float(self.get_parameter("box_size").value),
                box_tol=float(self.get_parameter("box_tol").value),
                range_jump=float(self.get_parameter("range_jump").value),
                min_protrusion=float(self.get_parameter("min_protrusion").value),
                min_points=int(self.get_parameter("min_points").value),
                max_points=int(self.get_parameter("max_points").value),
                max_dist=float(self.get_parameter("max_dist").value),
                sector_deg=float(self.get_parameter("sector_deg").value),
            )
            self.box_merge_dist = float(self.get_parameter("box_merge_dist").value)
            self.max_boxes = int(self.get_parameter("max_boxes").value)
            self.get_logger().info(f"box_detector umbrales: {self.det_kw} merge={self.box_merge_dist} cap={self.max_boxes}")
            self._written_runs = set()
            self._ensure_csv(self.detections_csv, [
                "time", "run_id", "box_id", "x", "y", "dist_lidar", "angle_rad",
            ])
            self.create_timer(0.2, self._tick)   # 5 Hz
            self.get_logger().info(
                f"box_detector listo — censando cajas en /scan. GT={len(self.ground_truth)} cajas, "
                f"run={self.run_id}, csv={self.metrics_csv}, detecciones={self.detections_csv}")

        def _cb_scan(self, m): self.scan = m
        def _cb_odom(self, m):
            p = m.pose.pose
            self.pose = (p.position.x, p.position.y,
                         yaw_from_quat(p.orientation.x, p.orientation.y, p.orientation.z, p.orientation.w))

        def _cb_lap(self, m):
            completed_lap = int(m.data)
            self.get_logger().info(
                f"🏁 VUELTA {completed_lap} COMPLETA — {len(self.seen_world)} cajas censadas; "
                f"escribo la fila de métricas y reseteo el censo para la siguiente.")
            self.run_id = completed_lap
            self.write_metrics(force=True)
            self.seen_world = []
            self.run_id = completed_lap + 1

        def _tick(self):
            if self.scan is None: return
            s = self.scan
            cand = detect_boxes_in_scan(list(s.ranges), s.angle_min, s.angle_increment,
                                        s.range_min, s.range_max, **self.det_kw)
            x0, y0, yaw = self.pose
            for dist, ang in cand:
                wx = x0 + dist * math.cos(yaw + ang)
                wy = y0 + dist * math.sin(yaw + ang)
                if len(self.seen_world) >= self.max_boxes:
                    continue
                if all(math.hypot(wx - sx, wy - sy) > self.box_merge_dist for sx, sy in self.seen_world):
                    self.seen_world.append((wx, wy))
                    box_id = len(self.seen_world)
                    self.get_logger().info(f"📦 Caja #{box_id} censada en ({wx:.2f},{wy:.2f})")
                    self._append_detection(box_id, wx, wy, dist, ang)
            self.pub.publish(Int32(data=len(self.seen_world)))
            # IMP2: publica las POSICIONES censadas (marco odom) como PoseArray
            pa = PoseArray()
            pa.header.frame_id = "odom"
            pa.header.stamp = self.get_clock().now().to_msg()
            for (sx, sy) in self.seen_world:
                pose = Pose()
                pose.position.x, pose.position.y = float(sx), float(sy)
                pose.orientation.w = 1.0
                pa.poses.append(pose)
            self.pub_pos.publish(pa)

        def _ensure_csv(self, path, header):
            if not path:
                return
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                with open(path, "w", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow(header)

        def _append_detection(self, box_id, x, y, dist, ang):
            if not self.detections_csv:
                return
            self._ensure_csv(self.detections_csv, [
                "time", "run_id", "box_id", "x", "y", "dist_lidar", "angle_rad",
            ])
            with open(self.detections_csv, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([
                    f"{self.get_clock().now().nanoseconds / 1e9:.3f}",
                    self.run_id,
                    box_id,
                    f"{x:.3f}",
                    f"{y:.3f}",
                    f"{dist:.3f}",
                    f"{ang:.4f}",
                ])

        def write_metrics(self, force=False):
            """Al terminar la corrida: compara el censo vs ground_truth y agrega una fila a metricas_lidar.csv (IMP2/DEF3)."""
            if self.run_id in self._written_runs:
                return
            if not force and not self.seen_world:
                return
            header = ["run_id", "cajas_reales", "cajas_censadas", "VP", "FP", "FN",
                      "tasa_deteccion", "error_pos_prom_m", "posiciones_censadas", "observaciones"]
            m = census_metrics(self.seen_world, self.ground_truth)
            try:
                os.makedirs(os.path.dirname(self.metrics_csv) or ".", exist_ok=True)
                new = not os.path.exists(self.metrics_csv) or os.path.getsize(self.metrics_csv) == 0
                with open(self.metrics_csv, "a", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    if new:
                        w.writerow(header)
                    if self.ground_truth:
                        obs = "auto_con_ground_truth"
                        cajas_reales = len(self.ground_truth)
                        vp, fp, fn = m["VP"], m["FP"], m["FN"]
                        tasa = f"{m['tasa_deteccion']:.3f}"
                        err = f"{m['error_pos_prom']:.3f}"
                    else:
                        obs = "sin_ground_truth; completar VP/FP/FN/error con cinta o video"
                        cajas_reales = ""
                        vp = fp = fn = tasa = err = ""
                    w.writerow([self.run_id, cajas_reales, len(self.seen_world), vp, fp, fn,
                                tasa, err,
                                ";".join(f"({x:.2f},{y:.2f})" for x, y in self.seen_world),
                                obs])
                self._written_runs.add(self.run_id)
                self.get_logger().info(
                    f"📊 Métricas corrida {self.run_id}: cajas={len(self.seen_world)} → {self.metrics_csv}")
            except Exception as exc:
                self.get_logger().error(f"No pude escribir {self.metrics_csv}: {exc}")

    def main(args=None):
        rclpy.init(args=args)
        node = BoxDetector()
        try: rclpy.spin(node)
        except KeyboardInterrupt: pass
        finally:
            node.write_metrics()   # al cerrar la corrida (Ctrl+C) → escribe VP/FP/FN a metricas_lidar.csv
            node.destroy_node(); rclpy.shutdown()
else:
    def main(args=None):
        raise SystemExit("ROS2 (rclpy) no disponible aquí.")


if __name__ == "__main__":
    main()
