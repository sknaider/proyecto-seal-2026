#!/usr/bin/env python3
"""Launch the maze_navigator with YAML params."""
import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg = get_package_share_directory("capytown_maze_pkg")
    params = os.path.join(pkg, "config", "maze_params.yaml")
    return LaunchDescription([
        DeclareLaunchArgument("scan_topic", default_value="/scan"),
        DeclareLaunchArgument("odom_topic", default_value="/odom_raw"),
        DeclareLaunchArgument("cmd_vel_topic", default_value="/cmd_vel"),
        DeclareLaunchArgument("course_mode", default_value="loop_boxes"),
        DeclareLaunchArgument("side", default_value="right"),
        DeclareLaunchArgument("target_laps", default_value="10"),
        DeclareLaunchArgument("lap_topic", default_value="/capytown_lap"),
        DeclareLaunchArgument("state_topic", default_value="/capytown_state"),
        DeclareLaunchArgument("wall_align_enabled", default_value="true"),
        DeclareLaunchArgument("wall_align_tol", default_value="0.04"),
        DeclareLaunchArgument("wall_align_kp", default_value="1.2"),
        DeclareLaunchArgument("wall_align_w_max", default_value="0.18"),
        DeclareLaunchArgument("wall_align_max_range", default_value="0.50"),
        DeclareLaunchArgument("corner_align_enabled", default_value="true"),
        DeclareLaunchArgument("corner_rear_max", default_value="0.45"),
        DeclareLaunchArgument("corner_side_max", default_value="0.38"),
        DeclareLaunchArgument("corner_min_turn_t", default_value="0.45"),
        DeclareLaunchArgument("front_angle_offset", default_value="0.0"),
        DeclareLaunchArgument("recover_persist", default_value="3.0"),
        DeclareLaunchArgument("veer_resume_t", default_value="0.0"),
        DeclareLaunchArgument("veer_grace_t", default_value="0.8"),
        DeclareLaunchArgument("veer_min_dist", default_value="0.85"),
        DeclareLaunchArgument("veer_min_t", default_value="2.3"),
        DeclareLaunchArgument("veer_timeout", default_value="4.0"),
        DeclareLaunchArgument("veer_out_angle", default_value="4.0"),
        DeclareLaunchArgument("veer_out_speed", default_value="0.10"),
        DeclareLaunchArgument("veer_out_kp", default_value="1.0"),
        DeclareLaunchArgument("veer_turn_speed", default_value="0.08"),
        DeclareLaunchArgument("veer_pass_speed", default_value="0.08"),
        DeclareLaunchArgument("veer_max_yaw_delta", default_value="25.0"),
        DeclareLaunchArgument("veer_finish_yaw_tol", default_value="25.0"),
        DeclareLaunchArgument("veer_force_away_from_wall", default_value="true"),
        DeclareLaunchArgument("veer_back_enabled", default_value="false"),
        DeclareLaunchArgument("post_veer_reacquire_t", default_value="3.0"),
        DeclareLaunchArgument("post_veer_reacquire_dist", default_value="0.30"),
        DeclareLaunchArgument("post_veer_wall_max", default_value="0.42"),
        DeclareLaunchArgument("post_veer_w_max", default_value="0.10"),
        DeclareLaunchArgument("debug_report_enabled", default_value="true"),
        DeclareLaunchArgument("debug_report_path", default_value="/tmp/capytown_maze_report.log"),
        DeclareLaunchArgument("debug_report_period", default_value="0.5"),
        DeclareLaunchArgument("loop_away_dist", default_value="0.30"),
        DeclareLaunchArgument("loop_min_path", default_value="1.0"),
        DeclareLaunchArgument("obstacle_detect", default_value="0.35"),
        DeclareLaunchArgument("obstacle_clear", default_value="0.65"),
        DeclareLaunchArgument("box_shoulder_margin", default_value="0.12"),
        DeclareLaunchArgument("enable_obstacle_veer", default_value="true"),
        DeclareLaunchArgument("disable_recover_180", default_value="true"),
        DeclareLaunchArgument("turn_in_max_accum_deg", default_value="135.0"),
        DeclareLaunchArgument("turn_in_reset_clear_t", default_value="3.0"),
        DeclareLaunchArgument("box_stop_wait_t", default_value="3.0"),
        DeclareLaunchArgument("quiet_terminal", default_value="true"),
        DeclareLaunchArgument("run_id", default_value="1"),
        DeclareLaunchArgument("metrics_csv", default_value="/tmp/metricas_lidar.csv"),
        DeclareLaunchArgument("detections_csv", default_value="/tmp/capytown_box_detections.csv"),
        DeclareLaunchArgument("lidar_visual_save_path", default_value="/tmp/lidar_detection.png"),
        DeclareLaunchArgument("lidar_visual_save_period", default_value="1.0"),
        DeclareLaunchArgument("lidar_visual_plot_range", default_value="2.0"),
        DeclareLaunchArgument("wall_clearance", default_value="0.06"),  # m borde->pared derecha (Henry pidio 6cm); menor = MAS pegado. wall_target = W/2 + esto.
        Node(
            package="capytown_maze_pkg",
            executable="maze_navigator",
            name="maze_navigator",
            output="screen",
            arguments=["--ros-args", "--log-level", "fatal"],
            parameters=[
                params,
                {
                    "scan_topic": LaunchConfiguration("scan_topic"),
                    "odom_topic": LaunchConfiguration("odom_topic"),
                    "cmd_vel_topic": LaunchConfiguration("cmd_vel_topic"),
                    "course_mode": LaunchConfiguration("course_mode"),
                    "side": LaunchConfiguration("side"),
                    "target_laps": ParameterValue(
                        LaunchConfiguration("target_laps"), value_type=int),
                    "state_topic": LaunchConfiguration("state_topic"),
                    "loop_away_dist": ParameterValue(
                        LaunchConfiguration("loop_away_dist"), value_type=float),
                    "loop_min_path": ParameterValue(
                        LaunchConfiguration("loop_min_path"), value_type=float),
                    "lap_topic": LaunchConfiguration("lap_topic"),
                    "wall_align_enabled": ParameterValue(
                        LaunchConfiguration("wall_align_enabled"), value_type=bool),
                    "wall_align_tol": ParameterValue(
                        LaunchConfiguration("wall_align_tol"), value_type=float),
                    "wall_align_kp": ParameterValue(
                        LaunchConfiguration("wall_align_kp"), value_type=float),
                    "wall_align_w_max": ParameterValue(
                        LaunchConfiguration("wall_align_w_max"), value_type=float),
                    "wall_align_max_range": ParameterValue(
                        LaunchConfiguration("wall_align_max_range"), value_type=float),
                    "corner_align_enabled": ParameterValue(
                        LaunchConfiguration("corner_align_enabled"), value_type=bool),
                    "corner_rear_max": ParameterValue(
                        LaunchConfiguration("corner_rear_max"), value_type=float),
                    "corner_side_max": ParameterValue(
                        LaunchConfiguration("corner_side_max"), value_type=float),
                    "corner_min_turn_t": ParameterValue(
                        LaunchConfiguration("corner_min_turn_t"), value_type=float),
                    "front_angle_offset": ParameterValue(
                        LaunchConfiguration("front_angle_offset"), value_type=float),
                    "recover_persist": ParameterValue(
                        LaunchConfiguration("recover_persist"), value_type=float),
                    "veer_resume_t": ParameterValue(
                        LaunchConfiguration("veer_resume_t"), value_type=float),
                    "veer_grace_t": ParameterValue(
                        LaunchConfiguration("veer_grace_t"), value_type=float),
                    "veer_min_dist": ParameterValue(
                        LaunchConfiguration("veer_min_dist"), value_type=float),
                    "veer_min_t": ParameterValue(
                        LaunchConfiguration("veer_min_t"), value_type=float),
                    "veer_timeout": ParameterValue(
                        LaunchConfiguration("veer_timeout"), value_type=float),
                    "veer_out_angle": ParameterValue(
                        LaunchConfiguration("veer_out_angle"), value_type=float),
                    "veer_out_speed": ParameterValue(
                        LaunchConfiguration("veer_out_speed"), value_type=float),
                    "veer_out_kp": ParameterValue(
                        LaunchConfiguration("veer_out_kp"), value_type=float),
                    "veer_turn_speed": ParameterValue(
                        LaunchConfiguration("veer_turn_speed"), value_type=float),
                    "veer_pass_speed": ParameterValue(
                        LaunchConfiguration("veer_pass_speed"), value_type=float),
                    "veer_max_yaw_delta": ParameterValue(
                        LaunchConfiguration("veer_max_yaw_delta"), value_type=float),
                    "veer_finish_yaw_tol": ParameterValue(
                        LaunchConfiguration("veer_finish_yaw_tol"), value_type=float),
                    "veer_force_away_from_wall": ParameterValue(
                        LaunchConfiguration("veer_force_away_from_wall"), value_type=bool),
                    "veer_back_enabled": ParameterValue(
                        LaunchConfiguration("veer_back_enabled"), value_type=bool),
                    "post_veer_reacquire_t": ParameterValue(
                        LaunchConfiguration("post_veer_reacquire_t"), value_type=float),
                    "post_veer_reacquire_dist": ParameterValue(
                        LaunchConfiguration("post_veer_reacquire_dist"), value_type=float),
                    "post_veer_wall_max": ParameterValue(
                        LaunchConfiguration("post_veer_wall_max"), value_type=float),
                    "post_veer_w_max": ParameterValue(
                        LaunchConfiguration("post_veer_w_max"), value_type=float),
                    "debug_report_enabled": ParameterValue(
                        LaunchConfiguration("debug_report_enabled"), value_type=bool),
                    "debug_report_path": LaunchConfiguration("debug_report_path"),
                    "debug_report_period": ParameterValue(
                        LaunchConfiguration("debug_report_period"), value_type=float),
                    "obstacle_detect": ParameterValue(
                        LaunchConfiguration("obstacle_detect"), value_type=float),
                    "obstacle_clear": ParameterValue(
                        LaunchConfiguration("obstacle_clear"), value_type=float),
                    "box_shoulder_margin": ParameterValue(
                        LaunchConfiguration("box_shoulder_margin"), value_type=float),
                    "enable_obstacle_veer": ParameterValue(
                        LaunchConfiguration("enable_obstacle_veer"), value_type=bool),
                    "disable_recover_180": ParameterValue(
                        LaunchConfiguration("disable_recover_180"), value_type=bool),
                    "turn_in_max_accum_deg": ParameterValue(
                        LaunchConfiguration("turn_in_max_accum_deg"), value_type=float),
                    "turn_in_reset_clear_t": ParameterValue(
                        LaunchConfiguration("turn_in_reset_clear_t"), value_type=float),
                    "box_stop_wait_t": ParameterValue(
                        LaunchConfiguration("box_stop_wait_t"), value_type=float),
                    "quiet_terminal": ParameterValue(
                        LaunchConfiguration("quiet_terminal"), value_type=bool),
                    "wall_clearance": ParameterValue(
                        LaunchConfiguration("wall_clearance"), value_type=float),
                },
            ],
        ),
        Node(
            package="capytown_maze_pkg",
            executable="box_detector",
            name="box_detector",
            output="screen",
            arguments=["--ros-args", "--log-level", "fatal"],
            parameters=[
                {
                    "scan_topic": LaunchConfiguration("scan_topic"),
                    "odom_topic": LaunchConfiguration("odom_topic"),
                    "lap_topic": LaunchConfiguration("lap_topic"),
                    "run_id": ParameterValue(LaunchConfiguration("run_id"), value_type=int),
                    "metrics_csv": LaunchConfiguration("metrics_csv"),
                    "detections_csv": LaunchConfiguration("detections_csv"),
                },
            ],
        ),
        Node(
            package="capytown_maze_pkg",
            executable="lidar_visualizer",
            name="lidar_visualizer",
            output="screen",
            arguments=["--ros-args", "--log-level", "fatal"],
            parameters=[
                {
                    "scan_topic": LaunchConfiguration("scan_topic"),
                    "state_topic": LaunchConfiguration("state_topic"),
                    "save_path": LaunchConfiguration("lidar_visual_save_path"),
                    "save_period": ParameterValue(
                        LaunchConfiguration("lidar_visual_save_period"), value_type=float),
                    "plot_range": ParameterValue(
                        LaunchConfiguration("lidar_visual_plot_range"), value_type=float),
                },
            ],
        ),
    ])
