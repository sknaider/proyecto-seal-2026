#!/usr/bin/env python3
"""
CapyTown lane_controller — Semana 11 (RC-2)  [CORREGIDO por NEXUS]

PID sobre /lane_error que publica /cmd_vel. Cuenta vueltas por odometría
(/odom) y frena al completar num_vueltas.

CAMBIOS vs la versión de globals:
  1. [FIX CRÍTICO] Signo del PID. Convención del PDF:
        error > 0  → centro a la DERECHA → girar derecha → omega < 0 (REP-103).
     La versión anterior hacía  w = +(P+I+D)  → con error>0 daba omega>0
     (giro a la IZQUIERDA) y el robot se salía. Ahora:  w = -(P+I+D).
     >> Verifica en hardware: carril a la derecha ⇒ las ruedas giran a la derecha. <<
  2. num_vueltas por defecto = 3 (Objetivo 4 / rúbrica), configurable por YAML.
  3. Suscripción a /odom (el bag del PDF graba /odom). Si tu robot publica otro
     nombre, ajústalo con  ros2 topic list | grep -i odom  y cambia el parámetro.
  4. Todas las ganancias se cargan desde config/pid_params.yaml (requisito YAML).
"""

import math

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry


class LaneController(Node):
    def __init__(self):
        super().__init__('lane_controller')

        # --- Parámetros (cargados desde config/pid_params.yaml vía launch) ---
        self.declare_parameters('', [
            ('kp', 2.5),
            ('ki', 0.0),
            ('kd', 0.3),
            ('linear_speed', 0.22),     # ≥0.2 con margen
            ('max_angular', 2.0),
            ('integral_limit', 0.5),
            ('error_timeout', 0.5),
            ('control_rate', 30.0),
            ('num_vueltas', 3),         # RC-2 pide 3
            ('metros_por_vuelta', 6.4), # perímetro Escenario A (2.0x2.0 ≈ 6.4 m)
            ('odom_topic', '/odom'),    # cambia si tu robot usa otro nombre
        ])
        gp = self.get_parameter
        self.kp      = gp('kp').value
        self.ki      = gp('ki').value
        self.kd      = gp('kd').value
        self.v       = gp('linear_speed').value
        self.max_w   = gp('max_angular').value
        self.i_limit = gp('integral_limit').value
        self.timeout = gp('error_timeout').value
        rate         = gp('control_rate').value
        self.num_vueltas       = gp('num_vueltas').value
        self.metros_por_vuelta = gp('metros_por_vuelta').value
        odom_topic             = gp('odom_topic').value

        # Estado del PID
        self.error      = None
        self.last_error = 0.0
        self.integral   = 0.0
        self.last_stamp = self.get_clock().now()
        self.last_rx    = self.get_clock().now()

        # Estado del contador de vueltas
        self.laps_done       = 0
        self.total_dist      = 0.0
        self.last_odom_x     = None
        self.last_odom_y     = None
        self.mision_completa = False

        self.sub_err  = self.create_subscription(Float32,  '/lane_error', self.on_error, 10)
        self.sub_odom = self.create_subscription(Odometry, odom_topic,    self.on_odom,  10)
        self.pub      = self.create_publisher(Twist, '/cmd_vel', 10)
        self.timer    = self.create_timer(1.0 / rate, self.control_loop)

        self.get_logger().info('lane_controller listo (params desde YAML).')
        self.get_logger().info(
            f'Misión: {self.num_vueltas} vueltas x {self.metros_por_vuelta} m = '
            f'{self.num_vueltas * self.metros_por_vuelta:.1f} m. odom={odom_topic}')

    def on_error(self, msg):
        if not math.isnan(msg.data):
            self.error   = msg.data
            self.last_rx = self.get_clock().now()

    def on_odom(self, msg):
        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y
        if self.last_odom_x is not None:
            self.total_dist += math.hypot(x - self.last_odom_x, y - self.last_odom_y)
            vueltas = int(self.total_dist / self.metros_por_vuelta)
            if vueltas > self.laps_done:
                self.laps_done = vueltas
                self.get_logger().info(
                    f'Vuelta {self.laps_done}/{self.num_vueltas} '
                    f'({self.total_dist:.1f} m)')
                if self.laps_done >= self.num_vueltas:
                    self.mision_completa = True
                    self.get_logger().info('¡Misión completa! Frenando.')
        self.last_odom_x = x
        self.last_odom_y = y

    def control_loop(self):
        now = self.get_clock().now()
        dt  = (now - self.last_stamp).nanoseconds * 1e-9
        self.last_stamp = now
        if dt <= 0.0:
            return

        if self.mision_completa:
            self.pub.publish(Twist())
            return

        age = (now - self.last_rx).nanoseconds * 1e-9
        if self.error is None or age > self.timeout:
            self.pub.publish(Twist())
            self.integral = 0.0
            return

        error = self.error

        # PID (TODO2, resuelto)
        p_term = self.kp * error
        self.integral += error * dt
        self.integral  = max(-self.i_limit, min(self.i_limit, self.integral))  # anti-windup
        i_term = self.ki * self.integral
        d_term = self.kd * (error - self.last_error) / dt
        self.last_error = error

        # [FIX SIGNO] error>0 (centro a la derecha) ⇒ omega<0 (gira derecha).
        w = -(p_term + i_term + d_term)
        w = max(-self.max_w, min(self.max_w, w))   # saturación

        cmd = Twist()
        cmd.linear.x  = self.v
        cmd.angular.z = w
        self.pub.publish(cmd)


def main(args=None):
    rclpy.init(args=args)
    node = LaneController()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.pub.publish(Twist())  # frena al salir
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
