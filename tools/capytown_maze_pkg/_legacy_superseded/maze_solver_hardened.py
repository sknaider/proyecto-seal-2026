#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
maze_solver_hardened.py — CapyTown / Robótica de Móviles ESAN 2026-I
====================================================================
Base: maze_solver.py de ALICE (wall-following por la derecha, regla de la mano
derecha). Esta versión añade el ENDURECIMIENTO de la revisión de arquitectura
de JARVIS — el ALGORITMO de ALICE queda INTACTO; solo se hace robusto para el
robot físico. Cambios (marcados [JARVIS]):

  🔴 1. QoS de /scan = qos_profile_sensor_data (BEST_EFFORT). Sin esto, si el
        driver del MS200 publica BEST_EFFORT, un suscriptor RELIABLE NO recibe
        NADA → el robot no se mueve sin error visible. Causa #1 del "no hace nada".
  🔴 2. Chequeo de scan RANCIO: si /scan deja de llegar (>SCAN_TIMEOUT s), se
        FRENA en vez de actuar sobre data vieja (riesgo de chocar a ciegas).
  🔴 3. Watchdog de giro: si la odometría se congela, el giro nunca termina y el
        robot queda atascado girando. Se aborta el giro tras GIRO_TIMEOUT s.
  🟢 4. Paro de emergencia: si el frente cae por debajo de DIST_EMERG, stop duro.

Plataforma: Yahboom MicroROS-Pi5 Robot Car (RPi5-4GB) · LiDAR MS200 · ROS2.
Tópicos: sub /scan (LaserScan), sub /odom (Odometry), pub /cmd_vel (Twist).
Autores: ALICE (algoritmo) + JARVIS (endurecimiento) — Team SEAL para Henry — 2026-06-22
"""
import math
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data   # [JARVIS] QoS de sensor
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist


def yaw_from_quaternion(q) -> float:
    """Extrae el yaw (rotación en Z) de un quaternion geometry_msgs/Quaternion."""
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def ang_norm(a: float) -> float:
    """Normaliza un ángulo a (-pi, pi]."""
    return math.atan2(math.sin(a), math.cos(a))


class MazeSolver(Node):
    # ---- Parámetros sintonizables (justifícalos en la defensa) ----
    V_AVANCE      = 0.12    # m/s   velocidad lineal de crucero
    W_GIRO        = 0.8     # rad/s velocidad angular en giros
    DIST_PARED    = 0.30    # m     distancia deseada a la pared derecha
    DIST_FRONTAL  = 0.40    # m     umbral: si hay pared al frente más cerca, no avanzo
    DIST_HUECO    = 0.70    # m     si la derecha supera esto, hay apertura -> entrar
    DIST_EMERG    = 0.15    # m     [JARVIS] frente < esto => paro de emergencia
    KP            = 1.8     # ganancia del control proporcional lateral
    W_SECTOR      = math.radians(18)   # semiancho de cada sector (promedia varios rayos)
    SCAN_TIMEOUT  = 0.5     # s     [JARVIS] si no llega /scan en este tiempo => frenar
    GIRO_TIMEOUT  = 6.0     # s     [JARVIS] giro máx antes de abortar (odom congelada)

    def __init__(self):
        super().__init__('maze_solver')
        self.scan = None
        self.scan_stamp = None        # [JARVIS] tiempo (s) de la última lectura de /scan
        self.pose = (0.0, 0.0, 0.0)   # x, y, yaw
        self.estado = 'SEGUIR_PARED'
        self.giro_objetivo = None     # yaw objetivo cuando giramos un ángulo fijo
        self.giro_inicio = None       # [JARVIS] tiempo de inicio del giro (watchdog)

        # [JARVIS] /scan con QoS de sensor (BEST_EFFORT) — match con el driver del LiDAR.
        self.create_subscription(LaserScan, '/scan', self._cb_scan, qos_profile_sensor_data)
        # /odom suele ser RELIABLE (default) — se mantiene.
        self.create_subscription(Odometry, '/odom', self._cb_odom, 10)
        self.pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.create_timer(0.05, self._loop)   # 20 Hz
        self.get_logger().warn(
            'LEGACY superseded: usa capytown_maze_pkg/maze_navigator.py '
            '(ros2 launch capytown_maze_pkg maze.launch.py) para la prueba real.'
        )

    # ------------------------------------------------------------------ utilidad de reloj
    def _now(self) -> float:
        """[JARVIS] Tiempo monotónico del nodo en segundos (reloj ROS)."""
        return self.get_clock().now().nanoseconds * 1e-9

    # ------------------------------------------------------------------ callbacks
    def _cb_scan(self, msg: LaserScan):
        self.scan = msg
        self.scan_stamp = self._now()   # [JARVIS] marca de frescura

    def _cb_odom(self, msg: Odometry):
        p = msg.pose.pose
        self.pose = (p.position.x, p.position.y, yaw_from_quaternion(p.orientation))

    # ------------------------------------------------------------------ sensado
    def _dist_sector(self, centro: float) -> float:
        """
        Distancia representativa (mínimo robusto) en un sector angular alrededor
        de 'centro' (rad, marco del LiDAR). Descarta inf/nan y lecturas fuera de
        rango. Devuelve +inf si el sector está vacío (= sin obstáculo cercano).
        """
        s = self.scan
        if s is None:
            return float('inf')
        vals = []
        n = len(s.ranges)
        for i in range(n):
            theta = s.angle_min + i * s.angle_increment
            if abs(ang_norm(theta - centro)) <= self.W_SECTOR:
                r = s.ranges[i]
                if math.isfinite(r) and s.range_min <= r <= s.range_max:
                    vals.append(r)
        if not vals:
            return float('inf')
        # Mínimo robusto: media de los 3 menores -> resiste outliers sin sobre-reaccionar.
        vals.sort()
        k = min(3, len(vals))
        return sum(vals[:k]) / k

    # ------------------------------------------------------------------ giros por odometría
    def _iniciar_giro(self, delta_rad: float, nuevo_estado_al_terminar: str):
        _, _, yaw = self.pose
        self.giro_objetivo = (ang_norm(yaw + delta_rad), nuevo_estado_al_terminar)
        self.giro_inicio = self._now()   # [JARVIS] arranca el watchdog

    def _girando(self) -> bool:
        """Ejecuta el giro en curso; devuelve True mientras no se complete."""
        if self.giro_objetivo is None:
            return False
        objetivo, siguiente = self.giro_objetivo
        # [JARVIS] Watchdog: si la odom se congela el giro nunca llega -> abortar.
        if self.giro_inicio is not None and (self._now() - self.giro_inicio) > self.GIRO_TIMEOUT:
            self.get_logger().warn('Giro excedió timeout (¿odom congelada?) -> aborto y reevalúo.')
            self.giro_objetivo = None
            self.giro_inicio = None
            self.estado = siguiente
            self._mover(0.0, 0.0)
            return False
        _, _, yaw = self.pose
        err = ang_norm(objetivo - yaw)
        if abs(err) < math.radians(4):           # tolerancia de llegada
            self.giro_objetivo = None
            self.giro_inicio = None
            self.estado = siguiente
            self._mover(0.0, 0.0)
            return False
        self._mover(0.0, math.copysign(self.W_GIRO, err))
        return True

    # ------------------------------------------------------------------ actuación
    def _mover(self, v: float, w: float):
        t = Twist()
        t.linear.x = float(v)
        t.angular.z = float(w)
        self.pub.publish(t)

    # ------------------------------------------------------------------ bucle FSM
    def _loop(self):
        if self.scan is None:
            return

        # [JARVIS] Scan rancio: si /scan dejó de llegar, FRENA (no actúes con data vieja).
        if self.scan_stamp is not None and (self._now() - self.scan_stamp) > self.SCAN_TIMEOUT:
            self.get_logger().warn('Scan rancio (>%.1fs sin /scan) -> freno por seguridad.' % self.SCAN_TIMEOUT)
            self._mover(0.0, 0.0)
            return

        # Si hay un giro fijo en curso (esquina o callejón), termínalo primero.
        if self._girando():
            return

        frente   = self._dist_sector(0.0)
        derecha  = self._dist_sector(-math.pi / 2)
        izquierda = self._dist_sector(math.pi / 2)

        # [JARVIS] Paro de emergencia: obstáculo frontal demasiado cerca.
        if frente < self.DIST_EMERG:
            self._mover(0.0, 0.0)
            self.get_logger().warn('Paro de emergencia: frente a %.2fm.' % frente)
            # Resuelve girando (esquina interior o callejón) en el próximo tick.
            if izquierda < self.DIST_FRONTAL:
                self._iniciar_giro(math.pi, 'SEGUIR_PARED')
            else:
                self._iniciar_giro(math.pi / 2, 'SEGUIR_PARED')
            return

        pared_frente = frente < self.DIST_FRONTAL
        hueco_derecha = derecha > self.DIST_HUECO
        pared_izq = izquierda < self.DIST_FRONTAL

        # --- Lógica de la mano derecha (algoritmo de ALICE, intacto) ---
        # 1) Si se abre hueco a la derecha y el frente está libre: ENTRA (gira derecha).
        if hueco_derecha and not pared_frente:
            self.estado = 'GIRAR_DER'
            self._iniciar_giro(-math.pi / 2, 'SEGUIR_PARED')
            self.get_logger().info('Hueco a la derecha -> giro derecha.')
            return

        # 2) Pared al frente.
        if pared_frente:
            if pared_izq:
                # Callejón sin salida: frente + izquierda bloqueados -> 180°.
                self.estado = 'CALLEJON'
                self._iniciar_giro(math.pi, 'SEGUIR_PARED')
                self.get_logger().info('Callejón -> giro 180°.')
            else:
                # Esquina interior: gira a la izquierda y sigue la pared.
                self.estado = 'GIRAR_IZQ'
                self._iniciar_giro(math.pi / 2, 'SEGUIR_PARED')
                self.get_logger().info('Pared al frente -> giro izquierda.')
            return

        # 3) Camino libre: SEGUIR_PARED con control P sobre la distancia derecha.
        self.estado = 'SEGUIR_PARED'
        if math.isfinite(derecha):
            error = self.DIST_PARED - derecha     # >0: muy cerca -> aléjate (gira izq, w>0)
            w = self.KP * error
        else:
            # No veo pared a la derecha: deriva suave hacia la derecha para reengancharla.
            w = -0.4
        w = max(-self.W_GIRO, min(self.W_GIRO, w))
        self._mover(self.V_AVANCE, w)

    def destroy_node(self):
        self._mover(0.0, 0.0)   # detén el robot al cerrar
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    nodo = MazeSolver()
    try:
        rclpy.spin(nodo)
    except KeyboardInterrupt:
        pass
    finally:
        nodo.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
