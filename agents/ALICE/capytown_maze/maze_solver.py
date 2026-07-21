#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
maze_solver.py — CapyTown / Robótica de Móviles ESAN 2026-I
============================================================
Atraviesa un laberinto usando SOLO LiDAR 2D (/scan) + odometría (/odom).
SIN cámara. Plataforma: Yahboom MicroROS-Pi5 Robot Car (RPi5-4GB) · LiDAR MS200 · ROS2.

Estrategia: seguimiento de pared (wall-following) por la DERECHA con la
"regla de la mano derecha". En un laberinto simplemente-conexo (todas las
paredes conectadas al borde) mantener contacto continuo con UNA pared
GARANTIZA encontrar la salida. Es el método clásico, robusto y fácil de
justificar en la defensa.

Tópicos
-------
  sub  /scan   sensor_msgs/LaserScan   — nube 2D del MS200
  sub  /odom   nav_msgs/Odometry       — pose del robot (x, y, yaw)
  pub  /cmd_vel geometry_msgs/Twist     — comando de velocidad

Máquina de estados (FSM)
------------------------
  SEGUIR_PARED : control P sobre la distancia lateral derecha (avanza pegado).
  GIRAR_IZQ    : pared al frente -> gira a la izquierda (la mano sigue la pared).
  GIRAR_DER    : se abrió hueco a la derecha -> gira a la derecha y entra.
  CALLEJON     : pared al frente, izq y der -> giro de 180°.

Autor: ALICE (Team SEAL) para Henry — 2026-06-22
"""
import math
import rclpy
from rclpy.node import Node
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
    KP            = 1.8     # ganancia del control proporcional lateral
    W_SECTOR      = math.radians(18)   # semiancho de cada sector (promedia varios rayos)

    def __init__(self):
        super().__init__('maze_solver')
        self.scan = None
        self.pose = (0.0, 0.0, 0.0)   # x, y, yaw
        self.estado = 'SEGUIR_PARED'
        self.giro_objetivo = None     # yaw objetivo cuando giramos un ángulo fijo

        self.create_subscription(LaserScan, '/scan', self._cb_scan, 10)
        self.create_subscription(Odometry, '/odom', self._cb_odom, 10)
        self.pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.create_timer(0.05, self._loop)   # 20 Hz
        self.get_logger().info('maze_solver listo — wall-following por la derecha.')

    # ------------------------------------------------------------------ callbacks
    def _cb_scan(self, msg: LaserScan):
        self.scan = msg

    def _cb_odom(self, msg: Odometry):
        p = msg.pose.pose
        self.pose = (p.position.x, p.position.y, yaw_from_quaternion(p.orientation))

    # ------------------------------------------------------------------ sensado
    def _dist_sector(self, centro: float) -> float:
        """
        Distancia representativa (mínimo robusto) en un sector angular alrededor
        de 'centro' (rad, marco del LiDAR). Descarta inf/nan y lecturas fuera de
        rango. Devuelve +inf si el sector está vacío (= sin obstáculo cercano).
        El índice i corresponde al ángulo theta = angle_min + i*angle_increment.
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

    def _girando(self) -> bool:
        """Ejecuta el giro en curso; devuelve True mientras no se complete."""
        if self.giro_objetivo is None:
            return False
        objetivo, siguiente = self.giro_objetivo
        _, _, yaw = self.pose
        err = ang_norm(objetivo - yaw)
        if abs(err) < math.radians(4):           # tolerancia de llegada
            self.giro_objetivo = None
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

        # Si hay un giro fijo en curso (esquina o callejón), termínalo primero.
        if self._girando():
            return

        frente   = self._dist_sector(0.0)
        derecha  = self._dist_sector(-math.pi / 2)
        izquierda = self._dist_sector(math.pi / 2)

        pared_frente = frente < self.DIST_FRONTAL
        hueco_derecha = derecha > self.DIST_HUECO
        pared_izq = izquierda < self.DIST_FRONTAL

        # --- Lógica de la mano derecha ---
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
            error = self.DIST_PARED - derecha     # >0: muy lejos -> acércate (gira der, w<0)
            w = -self.KP * error
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
