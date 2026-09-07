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
    from std_msgs.msg import Int32, Bool
    from geometry_msgs.msg import PoseArray, Pose   # IMP2: publica POSICIONES del censo
    _HAVE_ROS = True
except Exception:
    _HAVE_ROS = False

# NOTA: las vueltas las cuenta maze_navigator (loop_completion) y las anuncia por /capytown_lap;
# box_detector NO auto-detecta la vuelta (lo consume en _cb_lap). Sin import de LoopCompletion aquí.

# ── Parámetros (sintonizables) ──
BOX_SIZE = 0.17          # m, lado real de la caja RC-4 (17x17cm, confirmado por Henry)
BOX_TOL = 0.07           # m, tolerancia permisiva (alineado FABLE lead); el censo ya no depende del ancho estricto (usa extent), esto queda para la viz
RANGE_JUMP = 0.16        # m, salto de rango que separa segmentos; v29 subido 0.12→0.16 para NO fragmentar
                         #    las caras de la caja vista en ángulo (la "escalera" que señaló JARVIS)
BOX_MERGE_DIST = 0.40    # m, las cajas reales están separadas >=40cm; menor = misma caja
MIN_PROTRUSION = 0.05    # m, la caja debe sobresalir respecto a un vecino; v29 bajado 0.08→0.05 (permisivo, Henry)
MIN_BOX_POINTS = 3       # evita rayos sueltos/ruido como caja
MAX_BOX_POINTS = 55      # v29: subido 40→55 (stress-test: caja a <0.30m ocupa ~47 rayos, se rechazaba
                         #      como pared). El extent≤0.35 sigue rechazando paredes largas.
DETECT_MAX_DIST = 1.20   # solo censar objetos cercanos; paredes lejanas generan FP
DETECT_SECTOR_DEG = 120  # sector frontal util (+/-); ignora basura trasera del scan 360
MAX_BOXES_PER_LAP = 5    # RC-4 tiene 5 cajas; cap defensivo contra FP acumulados


def yaw_from_quat(x, y, z, w):
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def seg_dir(pts):
    """Dirección (rad) de un segmento vía sus extremos."""
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    return math.atan2(y1 - y0, x1 - x0)


def is_L_box(segA, segB, min_corner_deg=30.0, min_extent=0.045, max_extent=0.35, max_arm=0.28):
    """Spec de Henry: una L (2 caras en esquina ~perpendicular, CUALQUIER orientación) = CAJA.
    ang: 0=colineal (pared recta) .. 90=esquina (caja). is_L_box base verificado por JARVIS.

    Discriminador CAJA vs ESQUINA-DE-LABERINTO (Henry: «también detecta esquinas»):
    1) CONVEXIDAD (JARVIS, el discriminador fuerte): la CAJA es una L CONVEXA → el vértice apunta
       HACIA el robot = es el punto MÁS CERCANO. La esquina del maze es CÓNCAVA → el vértice es lo
       más LEJANO (dos paredes que se alejan). Convexo = caja; cóncavo = esquina.
    2) TAMAÑO (Henry: «libertad de min y max, no 80cm pero sí 35cm»): extent total ∈ [min,max]."""
    if len(segA) < 2 or len(segB) < 2:
        return False
    d = seg_dir(segA) - seg_dir(segB)
    ang = math.degrees(abs(math.atan2(math.sin(d), math.cos(d))))
    ang = min(ang, 180 - ang)
    def _ext(pp):
        return max(max(x for x, _y in pp) - min(x for x, _y in pp),
                   max(y for _x, y in pp) - min(y for _x, y in pp))
    def _d(p):
        return math.hypot(p[0], p[1])
    tot = _ext(list(segA) + list(segB))
    # convexidad: el vértice (unión de las 2 caras) está MÁS CERCA que los extremos exteriores
    vertex_d = min(_d(segA[-1]), _d(segB[0]))
    outer_d = min(_d(segA[0]), _d(segB[-1]))
    convex = vertex_d <= outer_d + 0.02          # +2cm de holgura al ruido
    return (ang >= min_corner_deg and min_extent <= tot <= max_extent
            and _ext(list(segA)) <= max_arm and _ext(list(segB)) <= max_arm and convex)


def _short_clusters(ranges, angle_min, angle_inc, range_min, range_max,
                    range_jump, min_points, max_points, max_dist, sector_deg):
    """Clústeres continuos, cortos, cercanos y dentro del sector frontal, con puntos XY."""
    import numpy as np
    r = np.asarray(ranges, dtype=float)
    valid = np.isfinite(r) & (r >= range_min) & (r <= range_max)
    n = len(r); clusters = []; i = 0
    while i < n:
        ang_i = angle_min + i * angle_inc
        if abs(math.degrees(math.atan2(math.sin(ang_i), math.cos(ang_i)))) > sector_deg or not valid[i]:
            i += 1; continue
        j = i
        while j + 1 < n and valid[j + 1] and abs(r[j + 1] - r[j]) < range_jump:
            j += 1
        seg = r[i:j + 1]
        if min_points <= len(seg) <= max_points:
            dist = float(np.median(seg))
            if dist <= max_dist:
                pts = [(r[k] * math.cos(angle_min + k * angle_inc),
                        r[k] * math.sin(angle_min + k * angle_inc)) for k in range(i, j + 1)]
                mid = (i + j) // 2
                nl = float(r[i - 1]) if i - 1 >= 0 and valid[i - 1] else float("inf")
                nr = float(r[j + 1]) if j + 1 < n and valid[j + 1] else float("inf")
                # CONTINUIDAD (Henry): ¿la PARED CONTINÚA más allá del extremo? Miramos hasta K rayos
                # a cada lado; si reaparece superficie a profundidad SIMILAR (~dist), es un pedazo de
                # una pared más larga, NO una caja (la caja TERMINA: detrás/al lado hay más lejos o vacío).
                K = 8
                ctol = 0.10
                cont_l = any(valid[kk] and abs(float(r[kk]) - dist) < ctol
                             for kk in range(i - 1, max(-1, i - 1 - K), -1))
                cont_r = any(valid[kk] and abs(float(r[kk]) - dist) < ctol
                             for kk in range(j + 1, min(n, j + 1 + K)))
                clusters.append({"pts": pts, "dist": dist, "ang": angle_min + mid * angle_inc,
                                 "nl": nl, "nr": nr, "cont_l": cont_l, "cont_r": cont_r})
        i = j + 1
    return clusters


def _is_single_face(c, min_protrusion, min_extent=0.045, max_extent=0.24):
    """Detección TEMPRANA/PERSISTENTE (Henry: «detectar antes de llegar y cuando se va»): una CAJA
    vista de FRENTE muestra 1 sola cara (no hay L aún) → un tramo CORTO de tamaño-caja que SOBRESALE
    (más cerca que sus DOS vecinos = bump CONVEXO hacia el robot). Una pared no sobresale; una esquina
    de laberinto no es un bump (se aleja). = misma convexidad que la L, pero de 1 cara."""
    pts = c["pts"]
    ex = max(max(x for x, _y in pts) - min(x for x, _y in pts),
             max(y for _x, y in pts) - min(y for _x, y in pts))
    if not (min_extent <= ex <= max_extent):
        return False
    return (c["nl"] - c["dist"]) > min_protrusion and (c["nr"] - c["dist"]) > min_protrusion


def detect_boxes_in_scan(ranges, angle_min, angle_inc, range_min, range_max,
                         *, box_size=BOX_SIZE, box_tol=BOX_TOL, range_jump=RANGE_JUMP,
                         min_protrusion=MIN_PROTRUSION, min_points=MIN_BOX_POINTS,
                         max_points=MAX_BOX_POINTS, max_dist=DETECT_MAX_DIST,
                         sector_deg=DETECT_SECTOR_DEG, max_box_extent=0.35, corner_dist=0.30):
    """CENSO por FORMA (spec de Henry): una L = dos caras en esquina ~perpendicular = CAJA, en
    CUALQUIER orientación. Pared recta = segmentos colineales (no forma L) → NO caja. Esto CORTA
    la oscilación FP↔FN (era forma, no tamaño). Devuelve (dist, ang) del vértice de cada L.
    Dos vías: (1) la L dentro de UN clúster que se dobla (esquina continua), (2) par de clústeres
    adyacentes que forman L. El merge del nodo (0.40m) deduplica."""
    clusters = _short_clusters(ranges, angle_min, angle_inc, range_min, range_max,
                               range_jump, min_points, max_points, max_dist, sector_deg)
    boxes = []
    taken = set()
    # (1) L dentro de un clúster: partir en dos mitades y ver si doblan en esquina.
    # NOTA: el gate de AISLAMIENTO (ambos flancos más lejos) se QUITÓ — mataba L reales de Henry
    # (caja pegada a pared o con un lado que continúa). El discriminador CAJA vs ISLA queda por
    # FORMA+TAMAÑO: convexidad + extent≤max_box_extent + brazos≤max_arm (la isla es más grande).
    for idx, c in enumerate(clusters):
        pts = c["pts"]
        if len(pts) >= 4:
            h = len(pts) // 2
            if is_L_box(pts[:h + 1], pts[h:], max_extent=max_box_extent):
                boxes.append((c["dist"], c["ang"]))
                taken.add(idx)
    # (2) L entre dos clústeres adyacentes (esquina partida por salto de rango)
    for a in range(len(clusters)):
        if a in taken:
            continue
        for b in range(a + 1, len(clusters)):
            if b in taken:
                continue
            ca, cb = clusters[a], clusters[b]
            gap = min(math.hypot(x1 - x2, y1 - y2)
                      for (x1, y1) in ca["pts"] for (x2, y2) in cb["pts"])
            if gap > corner_dist:
                continue
            if is_L_box(ca["pts"], cb["pts"], max_extent=max_box_extent):
                boxes.append((ca["dist"], ca["ang"]))
                taken.add(a); taken.add(b)
                break
    # (3) CARA ÚNICA (detección temprana/persistente, Henry): caja de frente = 1 cara corta que
    # sobresale (bump convexo). Solo clústeres que NO fueron parte de una L.
    for idx, c in enumerate(clusters):
        if idx in taken:
            continue
        if _is_single_face(c, min_protrusion):
            boxes.append((c["dist"], c["ang"]))
            taken.add(idx)
    return boxes


def debug_shapes(ranges, angle_min, angle_inc, range_min, range_max,
                 *, range_jump=RANGE_JUMP, min_points=MIN_BOX_POINTS, max_points=MAX_BOX_POINTS,
                 max_dist=DETECT_MAX_DIST, sector_deg=DETECT_SECTOR_DEG, **_ignore):
    """DIAGNÓSTICO (ground-truth): por cada clúster cercano, su tamaño (extent) y el ángulo de
    DOBLEZ interno (0=recto/pared .. 90=esquina/L=caja). Henry corre 1 vez → tuneamos con datos
    REALES en vez de adivinar. Devuelve [(dist, extent, bend_deg, n_pts), ...]."""
    cl = _short_clusters(ranges, angle_min, angle_inc, range_min, range_max,
                         range_jump, min_points, max_points, max_dist, sector_deg)
    out = []
    for c in cl:
        pts = c["pts"]
        ex = float(max(max(x for x, _y in pts) - min(x for x, _y in pts),
                       max(y for _x, y in pts) - min(y for _x, y in pts)))
        bend = 0.0
        if len(pts) >= 4:
            h = len(pts) // 2
            d = seg_dir(pts[:h + 1]) - seg_dir(pts[h:])
            bend = math.degrees(abs(math.atan2(math.sin(d), math.cos(d))))
            bend = min(bend, 180 - bend)
        out.append((round(c["dist"], 2), round(ex, 3), round(bend, 1), len(pts)))
    return out


def segment_scan(ranges, angle_min, angle_inc, range_min, range_max,
                 *, range_jump=RANGE_JUMP, box_size=0.17, box_tol=0.055,
                 min_protrusion=MIN_PROTRUSION, min_points=MIN_BOX_POINTS,
                 max_points=MAX_BOX_POINTS, max_dist=DETECT_MAX_DIST,
                 corner_dist=0.30, max_box_extent=0.30):
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
            # candidato = ÁREA del tamaño de la caja (17cm ESTRICTO, box_tol=0.04 → 13-21cm).
            # Áreas MÁS GRANDES = pared, NO caja (Henry). La 2ª cara adyacente confirma.
            # v29 (ALICE) FIX del robot real: además de ancho/extent, la caja DEBE SOBRESALIR de la
            # línea de pared (protrudes) con vecinos FINITOS — igual que el censo estricto (is_box).
            # Sin esto, una pared real fragmentada por ruido/range_jump en pedazos de ~17cm pasaba
            # como "candidato" (y la 2ª pasada los unía como "confirmada") → "detecta áreas grandes
            # como caja". La sim con pared limpia (1 segmento de 50cm) NO reproducía el fallo; el robot
            # real SÍ. Una caja física sobresale del muro (vecinos más lejos); un trozo de pared no.
            # v29 (ALICE): la VIZ usa el MISMO criterio que el censo (detect_boxes_in_scan) para que el
            # gráfico marque las cajas que el robot detecta (Henry: «que marque la caja como antes»).
            #   • L (clúster que dobla en esquina ~perpendicular, convexa) → CONFIRMADA (rojo).
            #   • cara ÚNICA de tamaño-caja que SOBRESALE (frente / montada en pared) → CANDIDATO (naranja).
            #   • resto → pared. Antes exigía arc-width≈0.17 + ambos vecinos finitos → perdía cajas de
            #     frente y montadas en pared (las que el censo sí ve).
            short = min_points <= len(seg) <= max_points and dist <= max_dist
            box_extent = 0.045 <= extent <= max_box_extent
            protrudes_face = (left - dist) > min_protrusion and (right - dist) > min_protrusion
            h_l = len(pts) // 2
            is_L = len(pts) >= 4 and is_L_box(pts[:h_l + 1], pts[h_l:], max_extent=max_box_extent)
            if is_L:
                status = "confirmada"
            elif short and box_extent and protrudes_face:
                status = "candidato"
            else:
                status = "pared"
            clusters.append({"points": pts, "status": status, "is_box": is_box,
                             "dist": dist, "ang": ang, "width": width,
                             "extent": extent})
        i = j + 1
    # 2ª pasada: un candidato con OTRA cara-candidata cercana (esquina) → CONFIRMADA
    cands = [c for c in clusters if c["status"] == "candidato"]
    for a_i in range(len(cands)):
        for b_i in range(a_i + 1, len(cands)):
            ca, cb = cands[a_i], cands[b_i]
            adjacent = any(math.hypot(x1 - x2, y1 - y2) <= corner_dist
                           for (x1, y1) in ca["points"] for (x2, y2) in cb["points"])
            # v29: confirmar SOLO si las 2 caras forman una L (~perpendicular) = caja (spec de Henry).
            # Dos tramos COLINEALES adyacentes (pared) NO forman L → no se confirman.
            if adjacent and is_L_box(ca["points"], cb["points"], max_extent=max_box_extent):
                ca["status"] = "confirmada"
                cb["status"] = "confirmada"
    # v29 (ALICE) 2ª guarda anti-pared: un grupo de "confirmadas" mutuamente adyacentes cuya
    # EXTENSIÓN TOTAL supera el tamaño físico de UNA caja (2-3 caras = esquina compacta) es una
    # PARED fragmentada (escalonada/curva), no una caja → se degrada a pared. Esto caza el caso
    # que 'protrudes' NO cubre: pared irregular donde cada tramo "sobresale" de sus vecinos.
    # Caja real vista como esquina: bounding box ≈ 0.17-0.20m (2 caras dentro de un cuadrado 17x17).
    # Umbral 0.28m (diagonal de caja 0.24 + margen para no perder cajas reales, Henry: «un poco más de
    # libertad») → pared curva/larga que se auto-confirma sigue cayendo, pero la caja real no se pierde.
    MAX_BOX_GROUP_EXTENT = 0.32
    confs = [c for c in clusters if c["status"] == "confirmada"]
    assigned = set()
    for ca in confs:
        if id(ca) in assigned:
            continue
        stack, grp = [ca], []
        assigned.add(id(ca))
        while stack:
            cur = stack.pop(); grp.append(cur)
            for cb in confs:
                if id(cb) in assigned:
                    continue
                if any(math.hypot(x1 - x2, y1 - y2) <= corner_dist
                       for (x1, y1) in cur["points"] for (x2, y2) in cb["points"]):
                    assigned.add(id(cb)); stack.append(cb)
        allpts = [p for c in grp for p in c["points"]]
        ex = max(x for x, _y in allpts) - min(x for x, _y in allpts)
        ey = max(y for _x, y in allpts) - min(y for _x, y in allpts)
        if max(ex, ey) > MAX_BOX_GROUP_EXTENT:
            for c in grp:
                c["status"] = "pared"
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
            # /box_ahead: True cuando hay una CAJA confirmada JUSTO AL FRENTE y cerca. El navegador lo
            # usa para decidir SOLO el STOP-3s (caja=para; isla/pared=rodea sin parar). El detector ya
            # distingue caja de isla (L+convexidad); el navegador rodea TODO como v18. (Henry)
            self.pub_box_ahead = self.create_publisher(Bool, "/box_ahead", 10)
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
            # DIAGNÓSTICO ground-truth (cada ~3s): qué formas ve el detector en el robot REAL.
            self._dbg = getattr(self, "_dbg", 0) + 1
            if self._dbg % 15 == 0:
                shapes = debug_shapes(list(s.ranges), s.angle_min, s.angle_increment,
                                      s.range_min, s.range_max,
                                      range_jump=self.det_kw.get("range_jump", RANGE_JUMP),
                                      min_points=self.det_kw.get("min_points", MIN_BOX_POINTS),
                                      max_points=self.det_kw.get("max_points", MAX_BOX_POINTS),
                                      max_dist=self.det_kw.get("max_dist", DETECT_MAX_DIST),
                                      sector_deg=self.det_kw.get("sector_deg", DETECT_SECTOR_DEG))
                self.get_logger().info(
                    f"🔎 FORMAS (dist, extent, doblez°, n_pts) [L=caja si doblez≳30° y extent≲0.30]: "
                    f"{shapes[:8]}  | detectadas={len(cand)}")
            # /box_ahead: ¿alguna caja detectada está JUSTO al frente (|ang|<35°) y cerca (<0.6m)?
            box_ahead = any(abs(math.degrees(math.atan2(math.sin(a), math.cos(a)))) < 35.0 and d < 0.60
                            for d, a in cand)
            self.pub_box_ahead.publish(Bool(data=bool(box_ahead)))
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
