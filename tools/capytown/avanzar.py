#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
avanzar.py — Test MÍNIMO de movimiento (ALICE, 2026-07-13).
Publica /cmd_vel para que el carro AVANCE, sin nada del código CapyTown.
Sirve para aislar: si con esto el carro se mueve, los motores/base están OK
y el problema es del código/config. Si NO se mueve, es el chasis/bringup.

Uso (dentro del contenedor, con ROS sourceado):
    python3 avanzar.py
    python3 avanzar.py 0.15        # velocidad en m/s (default 0.12)
Ctrl+C para parar (publica ceros al salir, el carro frena).
"""
import sys
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist


class Avanzar(Node):
    def __init__(self, vel):
        super().__init__("avanzar")
        self.vel = vel
        self.pub = self.create_publisher(Twist, "/cmd_vel", 10)
        # 10 Hz: muchos chasis frenan por watchdog si el comando no se repite
        self.timer = self.create_timer(0.1, self.tick)
        self.get_logger().info(f"Avanzando a {vel:.2f} m/s por /cmd_vel. Ctrl+C para parar.")

    def tick(self):
        t = Twist()
        t.linear.x = self.vel
        self.pub.publish(t)

    def parar(self):
        self.pub.publish(Twist())  # todo en cero -> frena


def main():
    vel = 0.12
    if len(sys.argv) > 1:
        try:
            vel = float(sys.argv[1])
        except ValueError:
            pass
    rclpy.init()
    node = Avanzar(vel)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.parar()   # frena al salir
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
