#!/usr/bin/env python3
"""
Pure-logic tests for the maze navigator (NO ROS needed).
Run: python3 test_maze_logic.py
Validates: range sanitization, sector extraction, state transitions, and the
wall-follow control-law SIGN (the classic place a wall-follower goes wrong).
"""
import math
import sys

from capytown_maze_pkg.maze_navigator import (
    sanitize, sector_min, sector_robust, yaw_from_quat, ang_diff,
    Sectors, decide_state, follow_cmd, should_hold_straight,
    localized_front_obstacle, wall_parallel_error, wall_is_parallel,
    corner_pose_aligned, heading_hold_cmd, coerce_param, DEFAULTS,
)
from capytown_maze_pkg.box_detector import census_metrics, detect_boxes_in_scan, segment_scan

P = dict(DEFAULTS)
P_MAZE = dict(DEFAULTS); P_MAZE["course_mode"] = "maze"; P_MAZE["disable_recover_180"] = False
fails = []


def check(name, cond):
    print(("  ok  " if cond else " FAIL ") + name)
    if not cond:
        fails.append(name)


# 1) sanitize: nan/inf/<=0 -> range_max ; clamp
check("sanitize nan->max", sanitize(float("nan"), 0.05, 8.0) == 8.0)
check("sanitize inf->max", sanitize(float("inf"), 0.05, 8.0) == 8.0)
check("sanitize 0->max", sanitize(0.0, 0.05, 8.0) == 8.0)
check("sanitize clamp-min", sanitize(0.01, 0.05, 8.0) == 0.05)
check("sanitize passthrough", abs(sanitize(1.23, 0.05, 8.0) - 1.23) < 1e-9)

# 2) sector_min on a synthetic 360° scan (1° increments).
n = 360
inc = math.radians(1.0)
amin = -math.pi
ranges = [5.0] * n
# put a close obstacle straight ahead (index where angle≈0 -> i=180)
ranges[180] = 0.5
# close wall on the right (angle ≈ -75° -> aa=-75°, i = ( -75°-(-180°) )=105)
ranges[105] = 0.4
front = sector_min(ranges, amin, inc, -25, 25, 0.05, 8.0)
right = sector_min(ranges, amin, inc, -100, -50, 0.05, 8.0)
left = sector_min(ranges, amin, inc, 50, 100, 0.05, 8.0)
check("sector front sees 0.5", abs(front - 0.5) < 1e-6)
check("sector right sees 0.4", abs(right - 0.4) < 1e-6)
check("sector left clear(5.0)", abs(left - 5.0) < 1e-6)

# 3) yaw + ang_diff
check("yaw 0", abs(yaw_from_quat(0, 0, 0, 1)) < 1e-9)
check("yaw 90", abs(yaw_from_quat(0, 0, math.sin(math.pi/4), math.cos(math.pi/4)) - math.pi/2) < 1e-6)
check("ang_diff wrap", abs(ang_diff(math.radians(170), math.radians(-170)) - math.radians(-20)) < 1e-6)

# 4) state machine (right-hand rule)
# clear corridor, wall on right at target -> FOLLOW_WALL
s = Sectors(front=2.0, left=2.0, right=0.35)
check("FSM follow", decide_state("FOLLOW_WALL", s, P) == "FOLLOW_WALL")
# front blocked, right wall present -> TURN_IN (turn away from wall)
s = Sectors(front=0.30, left=2.0, right=0.35)
check("FSM turn_in", decide_state("FOLLOW_WALL", s, P) == "TURN_IN")
# right opening (corner) -> TURN_OUT
s = Sectors(front=2.0, left=2.0, right=1.5)
check("FSM turn_out", decide_state("FOLLOW_WALL", s, P) == "TURN_OUT")
# boxed on 3 sides -> RECOVER
s = Sectors(front=0.12, left=P["wall_block"] - 0.02, right=P["wall_block"] - 0.02)
check("FSM recover in maze mode", decide_state("FOLLOW_WALL", s, P_MAZE) == "RECOVER")
check("loop_boxes never maps boxed reading to 180 RECOVER",
      decide_state("FOLLOW_WALL", s, P, front_blocked=True) == "TURN_IN")

# 5) control-law SIGN (right-hand rule):
# too CLOSE to right wall (err>0) must steer LEFT (angular > 0, away from wall)
s = Sectors(front=2.0, left=2.0, right=P["wall_target"] - 0.05)
lin, ang = follow_cmd(s, P)
check("steer away when too close (ang>0)", ang > 0)
# too FAR from right wall must steer RIGHT (angular < 0, toward wall)
s = Sectors(front=2.0, left=2.0, right=P["wall_target"] + 0.20)
lin, ang = follow_cmd(s, P)
check("steer toward when too far (ang<0)", ang < 0)
# front tight -> slow down (lin < v_max)
s = Sectors(front=(P["front_block"] + P["front_slow"]) / 2, left=2.0, right=P["wall_target"])
lin, ang = follow_cmd(s, P)
check("slow near front obstacle", lin < P["v_max"] + 1e-9 and lin >= P["v_min"] - 1e-9)

# 6) left-hand mirror sign
PL = dict(DEFAULTS); PL["side"] = "left"
s = Sectors(front=2.0, left=PL["wall_target"] - 0.05, right=2.0)
lin, ang = follow_cmd(s, PL)
check("left-rule steer away (ang<0)", ang < 0)

# 7) sector_robust: a single phantom near-ray must NOT collapse the sector
n = 360; inc = math.radians(1.0); amin = -math.pi
rr = [2.0] * n
rr[180] = 0.06           # one spurious super-close ray straight ahead
raw = sector_min(rr, amin, inc, -25, 25, 0.05, 8.0)
rob = sector_robust(rr, amin, inc, -25, 25, 0.05, 8.0, drop=1)
check("raw min fooled by phantom (=0.06)", abs(raw - 0.06) < 1e-6)
check("robust drops phantom (~2.0)", abs(rob - 2.0) < 1e-6)
# but TWO close rays (real obstacle) still register
rr[179] = 0.30; rr[180] = 0.30
rob2 = sector_robust(rr, amin, inc, -25, 25, 0.05, 8.0, drop=1)
check("robust keeps real obstacle (2 rays)", abs(rob2 - 0.30) < 1e-6)

# 8) decide_state honors the externally-computed (hysteresis) front_blocked
s = Sectors(front=0.30, left=2.0, right=0.35)   # front numerically <= front_block
check("hyst: fb=False overrides -> FOLLOW",
      decide_state("FOLLOW_WALL", s, P, front_blocked=False) == "FOLLOW_WALL")
s = Sectors(front=2.0, left=2.0, right=0.35)     # front numerically clear
check("hyst: fb=True forces -> TURN_IN",
      decide_state("FOLLOW_WALL", s, P, front_blocked=True) == "TURN_IN")

# 9) Henry correction: default behavior remains wall-follower when a wall exists,
# but if no side wall is visible the robot must not spin in TURN_OUT forever.
s = Sectors(front=2.0, left=2.0, right=P["wall_lost"] + 0.5)
check("no-wall space drives straight by default (anti-circle)",
      should_hold_straight(s, P, front_blocked=False) is True)
P_OPEN = dict(P); P_OPEN["open_space_straight"] = True
check("open-space straight only when explicitly enabled",
      should_hold_straight(s, P_OPEN, front_blocked=False) is True)
lin, ang = heading_hold_cmd(current_yaw=math.radians(5), target_yaw=0.0, p=P_OPEN)
check("heading hold corrects right drift left? (ang<0)", ang < 0)
check("heading hold keeps forward speed", abs(lin - P_OPEN["v_max"]) < 1e-9)

# 10) Robot 9 regression: TURN_IN must not trigger from noisy/lateral front hits every tick.
check("front sector narrowed for robot9 anti-false-TURN_IN", P["front_sector"] <= 15.0)
check("front blocked must persist before TURN_IN", P["front_block_persist"] >= 0.30)
check("loop_boxes default disables 180 recover", P["disable_recover_180"] is True)
check("loop_boxes default enables obstacle veer", P["enable_obstacle_veer"] is True)
check("loop_boxes protrusion mode has no heading-hold resume", P["veer_resume_t"] == 0.0)
check("loop_boxes has short post-veer re-detect grace", 0.0 < P["veer_grace_t"] <= 1.0)
check("box gate accepts localized front obstacle",
      localized_front_obstacle(0.30, 0.75, 0.80, P) is True)
check("box gate rejects broad wall/corner",
      localized_front_obstacle(0.30, 0.35, 0.37, P) is False)
check("box gate rejects early far wall", P["obstacle_detect"] <= 0.50)
check("veer must commit past box length", P["veer_min_dist"] >= 0.80)
check("veer must commit minimum time", P["veer_min_t"] >= 2.2)
check("veer uses tiny yaw offset", 3.0 <= P["veer_out_angle"] <= 5.0)
check("veer OUT must move forward, not pivot in place", P["veer_out_speed"] >= P["v_min"])
check("veer OUT angular amplitude/speed is not excessive", P["veer_turn_speed"] <= 0.10)
check("veer has safe pass speed", 0.07 <= P["veer_pass_speed"] <= 0.10)
check("veer anti-180 yaw guard is strict", P["veer_max_yaw_delta"] <= 30.0)
check("veer finish requires route-facing yaw", P["veer_finish_yaw_tol"] <= 30.0)
check("right-wall protrusion forces dodge away from wall", P["veer_force_away_from_wall"] is True)
check("right-wall protrusion disables hard BACK phase", P["veer_back_enabled"] is False)
check("right-wall protrusion returns immediately to wall follower", P["veer_resume_t"] == 0.0)
check("right-wall protrusion grace only suppresses re-detect", P["veer_grace_t"] <= 1.0)
check("post-veer reacquire suppresses fake corner turn long enough", P["post_veer_reacquire_t"] >= 2.5)
check("post-veer reacquire requires forward movement", P["post_veer_reacquire_dist"] >= 0.25)
check("post-veer reacquire uses gentle steering only", P["post_veer_w_max"] <= 0.12)
check("debug report is enabled by default", P["debug_report_enabled"] is True)
check("guardian waits 3s before box bypass", P["box_stop_wait_t"] >= 3.0)
check("right wall alignment: nose into wall -> steer left",
      wall_parallel_error(0.16, 0.26, "right", P) > 0)
check("right wall alignment: nose away from wall -> steer right",
      wall_parallel_error(0.26, 0.16, "right", P) < 0)
check("right wall alignment detects parallel",
      wall_is_parallel(0.20, 0.22, "right", P) is True)
check("wall alignment ignores far readings",
      wall_parallel_error(0.80, 0.82, "right", P) is None)
check("corner detector accepts front-open rear+right-wall pose",
      corner_pose_aligned(front=0.80, rear=0.30, followed_wall=0.22, p=P) is True)
check("corner detector rejects blocked front",
      corner_pose_aligned(front=0.20, rear=0.30, followed_wall=0.22, p=P) is False)
check("coerce bool false string", coerce_param("false", True) is False)
check("coerce float launch string", abs(coerce_param("3.0", 1.0) - 3.0) < 1e-9)

# 11) RC-4 metrics helper: VP/FP/FN and average position error.
m = census_metrics(
    detected=[(1.05, 1.00), (2.00, 2.00), (9.00, 9.00)],
    ground_truth=[(1.00, 1.00), (2.20, 2.00), (3.00, 3.00)],
    match_dist=0.30,
)
check("census metrics VP", m["VP"] == 2)
check("census metrics FP", m["FP"] == 1)
check("census metrics FN", m["FN"] == 1)
check("census metrics rate", abs(m["tasa_deteccion"] - (2 / 3)) < 1e-9)
check("census metrics error", 0.12 < m["error_pos_prom"] < 0.13)

# 12) box_detector stricter FP guard: accept a close 20cm protrusion, reject far/wall fragments.
def synthetic_scan(n=360, base=1.0):
    return [base] * n

rr = synthetic_scan()
# A 20cm object at 1m spans ~11 deg; with 1 deg scan it is ~11 rays.
for idx in range(175, 186):
    rr[idx] = 0.78
boxes = detect_boxes_in_scan(rr, -math.pi, math.radians(1.0), 0.12, 8.0)
check("box detector accepts close protruding 20cm box", len(boxes) == 1)

rr = synthetic_scan(base=1.0)
for idx in range(175, 186):
    rr[idx] = 1.0
boxes = detect_boxes_in_scan(rr, -math.pi, math.radians(1.0), 0.12, 8.0)
check("box detector rejects flat wall segment", len(boxes) == 0)

rr = synthetic_scan(base=2.0)
for idx in range(175, 186):
    rr[idx] = 1.60
boxes = detect_boxes_in_scan(rr, -math.pi, math.radians(1.0), 0.12, 8.0)
check("box detector rejects far wall fragments", len(boxes) == 0)

# 13) Visual RC-4 v28: no marcar áreas grandes como caja (un segmento de 50cm debe ser pared).
rr = synthetic_scan(base=1.0)
for idx in range(160, 189):   # ~50cm a 1m con LiDAR de 1 grado
    rr[idx] = 0.78
clusters = segment_scan(rr, -math.pi, math.radians(1.0), 0.12, 8.0)
check("visual rejects 50cm area as box",
      all(c["status"] == "pared" for c in clusters if abs(c["width"] - 0.39) < 0.20))

rr = synthetic_scan(base=1.0)
for idx in range(176, 186):   # ~17cm a 1m
    rr[idx] = 0.78
clusters = segment_scan(rr, -math.pi, math.radians(1.0), 0.12, 8.0)
check("visual accepts 17cm area as candidate/confirmed",
      any(c["status"] in ("candidato", "confirmada") for c in clusters))

# 14) Visual v32: caja escalonada compacta sí confirma, pared larga fragmentada no.
rr = synthetic_scan(base=1.0)
for idx in range(176, 185):
    rr[idx] = 0.78
for idx in range(188, 197):
    rr[idx] = 0.85
clusters = segment_scan(rr, -math.pi, math.radians(1.0), 0.12, 8.0)
check("visual confirms compact stepped box fragments",
      any(c["status"] == "confirmada" for c in clusters))

rr = synthetic_scan(base=1.0)
for idx in range(130, 140):
    rr[idx] = 0.78
for idx in range(155, 165):
    rr[idx] = 0.95
for idx in range(180, 190):
    rr[idx] = 0.78
clusters = segment_scan(rr, -math.pi, math.radians(1.0), 0.12, 8.0)
check("visual rejects long fragmented wall as box",
      all(c["status"] != "confirmada" for c in clusters))

print()
if fails:
    print(f"RESULT: {len(fails)} FAILED -> {fails}")
    sys.exit(1)
print("RESULT: ALL TESTS PASSED")
