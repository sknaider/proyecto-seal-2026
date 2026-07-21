"""Lanza el nodo maze_solver. Permite remapear tópicos si el Yahboom usa otros nombres.

Ejemplo con remapeo:
  ros2 launch capytown_maze maze.launch.py scan:=/scan_filtered cmd_vel:=/cmd_vel
"""
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='capytown_maze',
            executable='maze_solver',
            name='maze_solver',
            output='screen',
            # remapeos por si la base usa nombres distintos (ajústalos si hace falta)
            remappings=[
                ('/scan', '/scan'),
                ('/odom', '/odom'),
                ('/cmd_vel', '/cmd_vel'),
            ],
        ),
    ])
