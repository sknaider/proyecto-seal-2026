#!/usr/bin/env python3
"""granprix_full.launch.py — CapyTown Gran Prix en UN comando, con CÁMARA incluida.
====================================================================================
ALICE, 2026-07-13. Resuelve los 3 problemas que reportó Henry:
  1) «la cámara no abre / no detecta rojo»  → este launch SÍ arranca el driver de
     cámara (usb_cam) que publica /image_raw, que el granprix.launch.py original
     NO arrancaba (solo corría pare_detector, que esperaba una imagen que nadie daba).
  2) «no llega a la meta»                    → expone meta_enabled/meta_x/meta_y como
     argumentos; pasalos con las coords medidas en pista para que reconozca la META.

Uso mínimo (abre cámara + navegación + detección):
    ros2 launch capytown_granprix_pkg granprix_full.launch.py

Ronda 2 con meta medida en pista:
    ros2 launch capytown_granprix_pkg granprix_full.launch.py \
        ronda:=2 meta_enabled:=true meta_x:=3.0 meta_y:=1.8

Si tu cámara YA corre en otro contenedor/terminal, desactivá la de acá:
    ros2 launch capytown_granprix_pkg granprix_full.launch.py open_camera:=false

Copiar este archivo a: capytown_granprix_pkg/launch/granprix_full.launch.py
(y agregar 'launch/granprix_full.launch.py' a los data_files del setup.py, junto
al granprix.launch.py existente).
"""
import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch.conditions import IfCondition
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg = get_package_share_directory("capytown_granprix_pkg")
    params_yaml = os.path.join(pkg, "config", "granprix_params.yaml")

    # ── Argumentos ──
    ronda = DeclareLaunchArgument("ronda", default_value="1")
    run_id = DeclareLaunchArgument("run_id", default_value="1")
    side = DeclareLaunchArgument("side", default_value="right")
    meta_enabled = DeclareLaunchArgument("meta_enabled", default_value="false")
    meta_x = DeclareLaunchArgument("meta_x", default_value="3.0")
    meta_y = DeclareLaunchArgument("meta_y", default_value="2.0")
    enable_karpinchus = DeclareLaunchArgument("enable_karpinchus", default_value="true")
    open_camera = DeclareLaunchArgument(
        "open_camera", default_value="true",
        description="Arrancar el driver usb_cam (poné false si la cámara ya corre aparte)")
    video_device = DeclareLaunchArgument("video_device", default_value="/dev/video0")

    # ── Driver de cámara (usb_cam) → publica /image_raw ──
    # Es lo que el granprix.launch.py original NO hacía. Si tu robot usa otro
    # driver (v4l2_camera, o el paquete de cámara del Yahboom), cambiá package/
    # executable acá o usá open_camera:=false y arrancá tu cámara aparte.
    camera_node = Node(
        package="usb_cam",
        executable="usb_cam_node_exe",
        name="usb_cam",
        output="screen",
        parameters=[{
            "video_device": LaunchConfiguration("video_device"),
            "pixel_format": "yuyv",
            "framerate": 15.0,
        }],
        remappings=[("/image_raw", "/image_raw")],   # explícito: el tópico que espera pare_detector
        condition=IfCondition(LaunchConfiguration("open_camera")),
    )

    maze_solver_node = Node(
        package="capytown_granprix_pkg",
        executable="maze_solver",
        name="maze_solver",
        output="screen",
        parameters=[
            params_yaml,
            {
                "ronda": ParameterValue(LaunchConfiguration("ronda"), value_type=int),
                "run_id": ParameterValue(LaunchConfiguration("run_id"), value_type=int),
                "side": LaunchConfiguration("side"),
                "meta_enabled": ParameterValue(LaunchConfiguration("meta_enabled"), value_type=bool),
                "meta_x": ParameterValue(LaunchConfiguration("meta_x"), value_type=float),
                "meta_y": ParameterValue(LaunchConfiguration("meta_y"), value_type=float),
                "enable_obstacle_veer": ParameterValue(
                    LaunchConfiguration("enable_karpinchus"), value_type=bool),
            },
        ],
    )

    pare_detector_node = Node(
        package="capytown_granprix_pkg",
        executable="pare_detector",
        name="pare_detector",
        output="screen",
        parameters=[params_yaml],
    )

    box_detector_node = Node(
        package="capytown_granprix_pkg",
        executable="box_detector",
        name="box_detector",
        output="screen",
        parameters=[params_yaml],
        condition=IfCondition(LaunchConfiguration("enable_karpinchus")),
    )

    return LaunchDescription([
        ronda, run_id, side, meta_enabled, meta_x, meta_y, enable_karpinchus,
        open_camera, video_device,
        camera_node, maze_solver_node, pare_detector_node, box_detector_node,
    ])
