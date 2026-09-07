# capytown_maze_pkg — LiDAR + odometry navigator (no camera)

Reactive navigator for the **Yahboom MicroROS-Pi5 Robot Car** (RPi5-4GB,
LiDAR MS200). Uses only `/scan` (2D LiDAR) + `/odom_raw`; publishes `/cmd_vel`.
No camera, no map.

Default mode is now `loop_boxes`, for Henry's current challenge: a loop around a
central island with loose boxes in the corridor. In this mode the robot keeps
wall-following, veers around boxes, and does **not** do a 180° `RECOVER` when a
box is detected. The original maze behavior is still available with
`course_mode:=maze`.

The veer behavior is phased by odometry: `OUT -> PASS -> BACK -> VEER_RESUME`.
It opens with a smooth forward arc, passes the box for a minimum distance/time,
returns to the pre-veer heading, then ignores the same obstacle for a short grace
window. This prevents the "starts turning early, returns early, clips the box"
failure without the abrupt in-place pivot that made robot 9 lose the lane.

The box trigger is gated by front shoulders: a localized center hit with clear
shoulders is treated as a box; a broad hit across center+shoulders is treated as
wall/corner and left to wall-following.

## Build
```bash
# copy this folder into your workspace src/, then:
cd ~/yahboomcar_ws         # robot 9 workspace
colcon build --packages-select capytown_maze_pkg
source install/setup.bash
```

## Run
```bash
ros2 launch capytown_maze_pkg maze.launch.py
# or directly:
ros2 run capytown_maze_pkg maze_navigator --ros-args --params-file \
  $(ros2 pkg prefix capytown_maze_pkg)/share/capytown_maze_pkg/config/maze_params.yaml
```

For the original maze/dead-end behavior:
```bash
ros2 launch capytown_maze_pkg maze.launch.py course_mode:=maze enable_obstacle_veer:=false disable_recover_180:=false recover_persist:=1.0
```

## Topics (confirmed against YahboomCar_ROS2_Packages)
| dir | topic | type | note |
|-----|-------|------|------|
| sub | `/scan` | sensor_msgs/LaserScan | MS200, subscribed **BEST_EFFORT** QoS |
| sub | `/odom_raw` | nav_msgs/Odometry | subscribed BEST_EFFORT (compatible w/ RELIABLE too) |
| pub | `/cmd_vel` | geometry_msgs/Twist | yahboomcar_base_node listens here |

**EKF-fused odom:** the platform runs robot_localization. If the fused odom is on
a different topic (e.g. `/odometry/filtered`), just override:
```bash
--ros-args -p odom_topic:=/odometry/filtered
```

## Why BEST_EFFORT QoS (the #1 "it does nothing" trap)
LiDAR drivers (and micro-ROS) publish `/scan` as **BEST_EFFORT**. A RELIABLE
subscriber against a BEST_EFFORT publisher receives **zero** messages silently —
the robot just sits still with no error. This node subscribes BEST_EFFORT, which
is compatible with both publisher kinds. If you ever see "no movement, no error",
check `ros2 topic echo /scan` and the QoS first.

## Key parameters (`config/maze_params.yaml`)
| param | default | meaning |
|-------|---------|---------|
| `course_mode` | `loop_boxes` | current boxes-loop challenge; use `maze` for dead-end maze |
| `side` | `right` | wall to follow: `right` or `left` hand rule |
| `wall_target` | 0.20 | desired LiDAR-to-wall distance for robot 9 (m) |
| `front_block` / `front_clear` | 0.30 / 0.38 | front blocked / clear (hysteresis) |
| `front_block_persist` | 0.35 | seconds front must remain blocked before `TURN_IN` |
| `front_sector` | 12.0 | +/- degrees around forward; avoids side-wall false `TURN_IN` |
| `wall_lost` | 0.55 | side opening (corner) threshold for ~60cm corridor (m) |
| `wall_align_*` | enabled | right-wall front/back LiDAR alignment; cuts over-turn when parallel |
| `corner_align_*` | enabled | stops corner turns by geometry: front open + rear wall + followed wall |
| `emerg_dist` | 0.15 | front emergency-stop distance (m) |
| `v_max` / `v_min` | 0.18 / 0.06 | linear speed range (m/s) |
| `turn_speed` | 1.5 | in-place turn rate (rad/s) |
| `control_hz` | 10.0 | loop rate (match MS200 ~10 Hz) |
| `scan_timeout` | 0.5 | s without /scan → hard stop |
| `turn_timeout` | 6.0 | s max per turn → abort if odom froze |
| `stuck_t` / `stuck_dpos` | 2.5 / 0.03 | no-progress time / distance → reverse recovery |
| `sector_drop` | 2 | drop N closest rays per sector (kill phantom returns) |
| `enable_obstacle_veer` | true | veer around boxes without stopping to rotate |
| `disable_recover_180` | true | prevents false 180° turns in the boxes-loop course |
| `veer_resume_t` / `veer_grace_t` | 0.0 / 0.8 | return to wall-follow immediately; only suppress re-detect |
| `obstacle_detect` / `obstacle_clear` | 0.35 / 0.65 | localized box detection threshold / clear threshold |
| `box_shoulder_margin` | 0.12 | shoulder clearance needed to call the hit a box, not wall |
| `veer_min_dist` / `veer_min_t` | 0.85 / 2.3 | minimum committed bypass before returning to the route |
| `veer_out_angle` / `veer_out_speed` | 4.0 / 0.10 | tiny lane-shift offset and forward speed during `OUT` |
| `veer_turn_speed` / `veer_pass_speed` | 0.08 / 0.08 | max turn rate during `OUT` and speed during `PASS` |
| `veer_max_yaw_delta` / `veer_finish_yaw_tol` | 25.0 / 25.0 | anti-180 guard and route-facing finish gate |
| `veer_force_away_from_wall` / `veer_back_enabled` | true / false | treat right-wall boxes as protrusions, not free boxes |
| `post_veer_reacquire_t` / `post_veer_reacquire_dist` | 3.0 / 0.30 | suppress fake `TURN_OUT` after a right-wall box until the wall is reacquired |
| `post_veer_w_max` | 0.10 | gentle steering cap during post-box reacquire |
| `debug_report_path` | `/tmp/capytown_maze_report.log` | decision report file written on the robot |
| `front_angle_offset` | 0.0 | rotate LiDAR sectors if physical front is offset |

## Safety guards built in
- **Stale-scan freeze** — stop if LiDAR stops publishing (>`scan_timeout`).
- **Turn watchdog** — abort a turn if odom freezes (>`turn_timeout`).
- **Emergency stop** — hard stop if anything within `emerg_dist` ahead.
- **Stuck recovery** — reverse if driving but not advancing (odom Δpos≈0).
- **Robust sectors** — a lone phantom near-ray can't trigger a false obstacle.
- **Right-wall alignment** — compares right-front/right-back LiDAR hits (`rf/rb`
  in logs); when they match, the robot is parallel to the wall and stops
  over-turning after a box.
- **Corner alignment** — when logs show front open, `rear` close, and right wall
  close, the turn is considered complete without forcing the remaining 90°.

## State machine
`FOLLOW_WALL` (P control on lateral distance) → `TURN_IN` (front blocked → turn
away from wall, 90° closed-loop by odom) → `TURN_OUT` (wall opening → turn toward
it to take the corner) → `RECOVER` (boxed on 3 sides → 180°).

In `loop_boxes`, `RECOVER` 180 is disabled because the current course is a loop,
not a maze with dead ends. Box encounters are handled by veer/reverse instead of
turning around.

## Known limitation (topology)
The hand rule terminates only on a **simply-connected** maze (no loops/islands)
with the exit on the followed boundary. **If the CapyTown maze has loops**, switch
to a Pledge-style controller (tracks net turning) — ready to add on request.
Confirm the maze topology before the run.

## Test (no ROS needed)
```bash
python3 test_maze_logic.py
python3 test_maze_scenarios.py
```
