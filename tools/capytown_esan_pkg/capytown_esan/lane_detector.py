#!/usr/bin/env python3
"""
CapyTown lane_detector — Semana 11 (RC-2)  [CORREGIDO por NEXUS]

Segmenta el borde blanco y el eje amarillo por HSV, aplica una vista de
pájaro (IPM) y publica el error lateral en metros sobre /lane_error.

CAMBIOS vs la versión de globals:
  - Parámetros HSV/geometría se DECLARAN y se cargan desde config/hsv_params.yaml
    (requisito del entregable: "configurables por YAML").
  - Conserva TODO1 (compute_lane_error con los 3 casos) ya resuelto.
  - Calibrador HSV interactivo:  python3 lane_detector.py --calibrar --source 0

Convención de signo de /lane_error (igual que el PDF):
  error > 0  →  el centro del carril está a la DERECHA del robot.
  (El controlador es quien convierte esto en giro; ver lane_controller.py.)
"""

import argparse
import sys
import time

import cv2
import numpy as np

try:
    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import Image
    from std_msgs.msg import Float32
    from cv_bridge import CvBridge
    ROS_AVAILABLE = True
except ImportError:
    ROS_AVAILABLE = False
    Node = object  # fallback para modo --calibrar / pruebas sin ROS2


# ── IPM: trapecio de perspectiva (fracciones del ancho/alto de la imagen) ──
# Se dejan como constantes de código (igual que el enunciado base). Si las
# líneas salen curvas en /lane/debug_image, ajusta estos 4 valores.
IPM_TOP_Y        = 0.50
IPM_TOP_LEFT_X   = 0.18
IPM_TOP_RIGHT_X  = 0.82
IPM_BOTTOM_Y     = 0.98


# ═════════════════════════════════════════════════════════════════════════
#  FUNCIONES COMPARTIDAS
# ═════════════════════════════════════════════════════════════════════════

def build_ipm(w, h):
    """Homografía 3x3 de vista frontal -> vista de pájaro (cv2.getPerspectiveTransform)."""
    src = np.float32([
        [IPM_TOP_LEFT_X  * w, IPM_TOP_Y    * h],
        [IPM_TOP_RIGHT_X * w, IPM_TOP_Y    * h],
        [1.00            * w, IPM_BOTTOM_Y * h],
        [0.00            * w, IPM_BOTTOM_Y * h],
    ])
    dst = np.float32([
        [0.30 * w, 0.0],
        [0.70 * w, 0.0],
        [0.70 * w, h  ],
        [0.30 * w, h  ],
    ])
    return cv2.getPerspectiveTransform(src, dst)


def centroid_x(mask, min_area):
    """X del centroide de una máscara binaria, o None si el área < min_area (ruido)."""
    m = cv2.moments(mask, binaryImage=True)
    if m['m00'] < max(min_area, 1e-3):
        return None
    return m['m10'] / m['m00']


def apply_hsv_masks(frame, white_lo, white_hi, yellow_lo, yellow_hi, M):
    """IPM + máscaras de color (blanco/amarillo) con limpieza morfológica."""
    h, w = frame.shape[:2]
    warp   = cv2.warpPerspective(frame, M, (w, h))
    hsv    = cv2.cvtColor(warp, cv2.COLOR_BGR2HSV)
    kernel = np.ones((3, 3), np.uint8)
    mask_w = cv2.morphologyEx(cv2.inRange(hsv, white_lo,  white_hi),  cv2.MORPH_OPEN, kernel)
    mask_y = cv2.morphologyEx(cv2.inRange(hsv, yellow_lo, yellow_hi), cv2.MORPH_OPEN, kernel)
    return warp, mask_w, mask_y


def compute_lane_error(mask_white, mask_yellow, w, h,
                       min_area, lane_width_m, px_per_meter, look_ahead_row):
    """
    Error lateral en metros respecto al centro del carril (TODO1, resuelto).

    Casos:
      ambas líneas → centro = promedio(blanca, amarilla)
      solo amarillo → centro = amarillo + media calzada (a la derecha)
      solo blanco   → centro = blanco  - media calzada (a la izquierda)
      ninguna       → NaN → el controlador frena por seguridad
    """
    row  = int(look_ahead_row * h)
    band = slice(max(0, row - 8), min(h, row + 8))

    x_white  = centroid_x(mask_white[band,  :], min_area)
    x_yellow = centroid_x(mask_yellow[band, :], min_area)

    half_px = (lane_width_m / 2.0) * px_per_meter

    if x_white is not None and x_yellow is not None:
        center_px = (x_white + x_yellow) / 2.0
    elif x_yellow is not None:
        center_px = x_yellow + half_px
    elif x_white is not None:
        center_px = x_white - half_px
    else:
        center_px = None

    if center_px is None:
        error_m = float('nan')
    else:
        error_m = (center_px - w / 2.0) / px_per_meter

    return error_m, x_white, x_yellow, center_px, row


# ═════════════════════════════════════════════════════════════════════════
#  NODO ROS2
# ═════════════════════════════════════════════════════════════════════════

class LaneDetector(Node):
    def __init__(self):
        super().__init__('lane_detector')
        self.bridge = CvBridge()

        # --- Parámetros (cargados desde config/hsv_params.yaml vía launch) ---
        self.declare_parameters('', [
            ('white_h_min', 0),   ('white_h_max', 180),
            ('white_s_min', 0),   ('white_s_max', 30),
            ('white_v_min', 180), ('white_v_max', 255),
            ('yellow_h_min', 20), ('yellow_h_max', 35),
            ('yellow_s_min', 100), ('yellow_s_max', 255),
            ('yellow_v_min', 100), ('yellow_v_max', 255),
            ('min_area', 150),
            ('lane_width_m', 0.21),
            ('px_per_meter', 600.0),
            ('look_ahead_row', 0.6),
            ('publish_debug', True),
        ])
        gp = self.get_parameter
        self.white_lo  = np.array([gp('white_h_min').value,  gp('white_s_min').value,  gp('white_v_min').value])
        self.white_hi  = np.array([gp('white_h_max').value,  gp('white_s_max').value,  gp('white_v_max').value])
        self.yellow_lo = np.array([gp('yellow_h_min').value, gp('yellow_s_min').value, gp('yellow_v_min').value])
        self.yellow_hi = np.array([gp('yellow_h_max').value, gp('yellow_s_max').value, gp('yellow_v_max').value])
        self.min_area       = gp('min_area').value
        self.lane_width_m   = gp('lane_width_m').value
        self.px_per_meter   = gp('px_per_meter').value
        self.look_ahead_row = gp('look_ahead_row').value
        self.publish_debug  = gp('publish_debug').value

        self.M = None  # homografía IPM (se calcula con el primer frame)

        self.sub     = self.create_subscription(Image, '/camera/image_raw', self.on_image, 10)
        self.pub_err = self.create_publisher(Float32, '/lane_error', 10)
        self.pub_dbg = self.create_publisher(Image,   '/lane/debug_image', 10)
        self.get_logger().info('lane_detector listo (params desde YAML).')

    def on_image(self, msg):
        frame = self.bridge.imgmsg_to_cv2(msg, 'bgr8')
        h, w  = frame.shape[:2]
        if self.M is None:
            self.M = build_ipm(w, h)

        warp, mask_w, mask_y = apply_hsv_masks(
            frame, self.white_lo, self.white_hi, self.yellow_lo, self.yellow_hi, self.M)

        error_m, x_white, x_yellow, center_px, row = compute_lane_error(
            mask_w, mask_y, w, h,
            self.min_area, self.lane_width_m, self.px_per_meter, self.look_ahead_row)

        out = Float32()
        out.data = float(error_m)
        self.pub_err.publish(out)

        if self.publish_debug:
            self._publish_debug(warp, row, x_white, x_yellow, center_px, msg)

    def _publish_debug(self, warp, row, xw, xy, xc, header_msg):
        dbg = warp.copy()
        cv2.line(dbg, (0, row), (dbg.shape[1], row), (0, 255, 0), 1)
        for x, color in [(xw, (255, 255, 255)), (xy, (0, 255, 255)), (xc, (0, 0, 255))]:
            if x is not None:
                cv2.circle(dbg, (int(x), row), 5, color, -1)
        out = self.bridge.cv2_to_imgmsg(dbg, 'bgr8')
        out.header = header_msg.header
        self.pub_dbg.publish(out)


# ═════════════════════════════════════════════════════════════════════════
#  CALIBRADOR HSV INTERACTIVO  (python3 lane_detector.py --calibrar --source 0)
# ═════════════════════════════════════════════════════════════════════════

def _nothing(_):
    pass


def _make_trackbars(window, vals):
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    for i, name in enumerate(('H min', 'H max', 'S min', 'S max', 'V min', 'V max')):
        top = 180 if 'H' in name else 255
        cv2.createTrackbar(name, window, vals[i], top, _nothing)


def _read_trackbars(window):
    return tuple(cv2.getTrackbarPos(k, window)
                 for k in ('H min', 'H max', 'S min', 'S max', 'V min', 'V max'))


def _yaml_block(white, yellow):
    return f"""lane_detector:
  ros__parameters:
    white_h_min: {white[0]}
    white_h_max: {white[1]}
    white_s_min: {white[2]}
    white_s_max: {white[3]}
    white_v_min: {white[4]}
    white_v_max: {white[5]}
    yellow_h_min: {yellow[0]}
    yellow_h_max: {yellow[1]}
    yellow_s_min: {yellow[2]}
    yellow_s_max: {yellow[3]}
    yellow_v_min: {yellow[4]}
    yellow_v_max: {yellow[5]}
    min_area: 150
    lane_width_m: 0.21
    px_per_meter: 600.0
    look_ahead_row: 0.6
    publish_debug: true
"""


def run_calibrator(source):
    cap = None
    static_img = None
    if source.isdigit():
        cap = cv2.VideoCapture(int(source))
    else:
        img = cv2.imread(source)
        if img is not None:
            static_img = img
        else:
            cap = cv2.VideoCapture(source)
    if static_img is None and (cap is None or not cap.isOpened()):
        raise SystemExit(f'No se pudo abrir la fuente: {source}')

    _make_trackbars('Blanco (borde)',  (0, 180, 0, 30, 180, 255))
    _make_trackbars('Amarillo (eje)',  (20, 35, 100, 255, 100, 255))

    M = None
    last_print = 0.0
    white = yellow = None
    print('\n[CALIBRADOR] Ajusta los sliders. "s" guarda YAML, "q" sale.\n')

    while True:
        frame = static_img.copy() if static_img is not None else None
        if frame is None:
            ok, frame = cap.read()
            if not ok:
                break
        frame = cv2.resize(frame, (640, 480))
        h, w = frame.shape[:2]
        if M is None:
            M = build_ipm(w, h)

        white  = _read_trackbars('Blanco (borde)')
        yellow = _read_trackbars('Amarillo (eje)')
        warp, mask_w, mask_y = apply_hsv_masks(
            frame,
            np.array([white[0], white[2], white[4]]), np.array([white[1], white[3], white[5]]),
            np.array([yellow[0], yellow[2], yellow[4]]), np.array([yellow[1], yellow[3], yellow[5]]),
            M)

        row  = int(0.6 * h)
        band = slice(max(0, row - 8), min(h, row + 8))
        xw = centroid_x(mask_w[band, :], 150)
        xy = centroid_x(mask_y[band, :], 150)
        view = warp.copy()
        cv2.line(view, (0, row), (w, row), (0, 255, 0), 1)
        for x, color, label in [(xw, (255, 255, 255), 'W'), (xy, (0, 255, 255), 'Y')]:
            if x is not None:
                cv2.circle(view, (int(x), row), 6, color, -1)
                cv2.putText(view, label, (int(x) - 5, row - 12),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        cv2.imshow('IPM + centroides', view)
        cv2.imshow('Mascara blanco',   mask_w)
        cv2.imshow('Mascara amarillo', mask_y)

        if time.time() - last_print > 1.0:
            print(_yaml_block(white, yellow))
            last_print = time.time()
        key = cv2.waitKey(30) & 0xFF
        if key in (ord('q'), 27):
            break
        if key == ord('s'):
            with open('hsv_params_calibrado.yaml', 'w') as f:
                f.write(_yaml_block(white, yellow))
            print('[CALIBRADOR] Guardado hsv_params_calibrado.yaml')

    if cap is not None:
        cap.release()
    cv2.destroyAllWindows()
    if white and yellow:
        print('\n=== YAML FINAL (copiar a config/hsv_params.yaml) ===')
        print(_yaml_block(white, yellow))


def main(args=None):
    parser = argparse.ArgumentParser(description='CapyTown lane_detector RC-2')
    parser.add_argument('--calibrar', action='store_true', help='Calibrador HSV (sin ROS2)')
    parser.add_argument('--source', default='0', help='Cámara (0,1,..) o ruta a video/imagen')
    parsed, _ = parser.parse_known_args()

    if parsed.calibrar:
        run_calibrator(parsed.source)
        return
    if not ROS_AVAILABLE:
        print('ERROR: ROS2 no disponible. Usa --calibrar para calibrar HSV.')
        sys.exit(1)

    rclpy.init(args=args)
    node = LaneDetector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
