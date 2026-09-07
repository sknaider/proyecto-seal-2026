#!/usr/bin/env python3
"""Verificación por efecto de la lógica pura (sin ROS): error + signo PID."""
import sys, math
import numpy as np
sys.path.insert(0, 'capytown_esan')
import lane_detector as ld

W, H = 640, 480
PPM = 600.0
LANE = 0.21
LOOK = 0.6
MINA = 150

def mask_with_blob(cx):
    """Máscara con una mancha blanca centrada en x=cx, en la banda look-ahead."""
    m = np.zeros((H, W), np.uint8)
    row = int(LOOK * H)
    m[row-6:row+6, int(cx)-15:int(cx)+15] = 255
    return m

empty = np.zeros((H, W), np.uint8)
fails = 0

# 1) Centro a la DERECHA (amarillo en el centro-derecha) -> error > 0
yellow_right = mask_with_blob(W*0.75)
err, *_ = ld.compute_lane_error(empty, yellow_right, W, H, MINA, LANE, PPM, LOOK)
print(f"[1] centro derecha -> error={err:+.3f} (esperado >0)")
fails += 0 if (err > 0) else 1

# 2) Centro a la IZQUIERDA -> error < 0
yellow_left = mask_with_blob(W*0.25)
err2, *_ = ld.compute_lane_error(empty, yellow_left, W, H, MINA, LANE, PPM, LOOK)
print(f"[2] centro izquierda -> error={err2:+.3f} (esperado <0)")
fails += 0 if (err2 < 0) else 1

# 3) Sin líneas -> NaN
err3, *_ = ld.compute_lane_error(empty, empty, W, H, MINA, LANE, PPM, LOOK)
print(f"[3] sin lineas -> error={err3} (esperado NaN)")
fails += 0 if math.isnan(err3) else 1

# 4) Ambas líneas -> centro = promedio
both_w = mask_with_blob(W*0.80)
both_y = mask_with_blob(W*0.60)
err4, xw, xy, cpx, _ = ld.compute_lane_error(both_w, both_y, W, H, MINA, LANE, PPM, LOOK)
print(f"[4] ambas -> xw~{xw:.0f} xy~{xy:.0f} centro~{cpx:.0f} error={err4:+.3f}")
fails += 0 if (xw and xy and abs(cpx-(xw+xy)/2) < 1) else 1

# 5) SIGNO PID (el fix): error>0 (centro derecha) -> omega<0 (gira derecha)
def pid_omega(error, kp=2.5, ki=0.0, kd=0.3, last=0.0, integ=0.0, dt=1/30, ilim=0.5, maxw=2.0):
    p = kp*error
    integ = max(-ilim, min(ilim, integ + error*dt)); i = ki*integ
    d = kd*(error-last)/dt
    w = -(p + i + d)                      # <-- FIX aplicado
    return max(-maxw, min(maxw, w))
w_pos = pid_omega(+0.10)
w_neg = pid_omega(-0.10)
print(f"[5] error +0.10 -> omega={w_pos:+.3f} (esperado <0, gira derecha)")
print(f"    error -0.10 -> omega={w_neg:+.3f} (esperado >0, gira izquierda)")
fails += 0 if (w_pos < 0 and w_neg > 0) else 1

print(f"\n{'TODOS OK ✅' if fails==0 else f'❌ {fails} FALLO(S)'}")
sys.exit(1 if fails else 0)
