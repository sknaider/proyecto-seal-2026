#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_maze_scenarios.py — verificación de COMPORTAMIENTO por escenario (JARVIS)
=============================================================================
Complementa test_maze_logic.py de NEXUS (que prueba las funciones puras una a
una) con las 5+ SITUACIONES CANÓNICAS del laberinto: alimenta sectores LiDAR
representativos a las funciones REALES del nodo (decide_state / follow_cmd) y
afirma la decisión correcta en cada una. Sin ROS, sin modelo de trayectoria
especulativo (el hardware sigue siendo la lente final) — solo confirma que el
"cerebro" decide bien situación por situación. Útil también para la defensa.

Regla de la mano DERECHA (side='right'):
  FOLLOW_WALL  — corredor: avanza pegado a la pared derecha.
  TURN_OUT     — se abre la pared derecha (hueco) -> toma la esquina (gira der).
  TURN_IN      — pared al frente, derecha aún con pared -> gira a la izquierda.
  RECOVER      — encajonado (frente+der+izq) -> giro de 180°.

Corre:  python3 test_maze_scenarios.py
"""
import os
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
NAV = os.path.join(HERE, "capytown_maze_pkg", "maze_navigator.py")

spec = importlib.util.spec_from_file_location("maze_navigator", NAV)
mn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mn)   # ROS imports están guardados -> importa sin ROS

Sectors = mn.Sectors
decide_state = mn.decide_state
follow_cmd = mn.follow_cmd
should_hold_straight = mn.should_hold_straight
P = dict(mn.DEFAULTS)   # side='right' por defecto
P_MAZE = dict(mn.DEFAULTS); P_MAZE["course_mode"] = "maze"; P_MAZE["disable_recover_180"] = False

_fail = 0


def check(nombre, cond, detalle=""):
    global _fail
    ok = bool(cond)
    print(f"  {'ok ' if ok else 'XX '} {nombre}" + (f"  [{detalle}]" if detalle and not ok else ""))
    if not ok:
        _fail += 1


# ── Escenario 1: corredor recto, pared derecha a la distancia deseada ──
s = Sectors(front=2.0, left=2.0, right=P["wall_target"])
st = decide_state("FOLLOW_WALL", s, P)
lin, ang = follow_cmd(s, P)
check("1 corredor recto -> FOLLOW_WALL", st == "FOLLOW_WALL", st)
check("1 corredor recto -> avanza a v_max", abs(lin - P["v_max"]) < 1e-6, lin)
check("1 corredor recto -> giro ~0 (a distancia objetivo)", abs(ang) < 1e-6, ang)

# ── Escenario 2: pared al frente, pared derecha presente -> TURN_IN (gira izq) ──
s = Sectors(front=0.25, left=2.0, right=P["wall_target"])
st = decide_state("FOLLOW_WALL", s, P, front_blocked=True)
check("2 pared al frente -> TURN_IN", st == "TURN_IN", st)

# ── Escenario 3: hueco a la derecha (apertura) -> TURN_OUT (toma la esquina) ──
s = Sectors(front=2.0, left=2.0, right=P["wall_lost"] + 0.1)
st = decide_state("FOLLOW_WALL", s, P)
check("3 hueco a la derecha -> TURN_OUT", st == "TURN_OUT", st)

# ── Escenario 4: callejón / encajonado (frente+der+izq bloqueados) -> RECOVER ──
s = Sectors(front=0.12, left=P["wall_block"] - 0.02, right=P["wall_block"] - 0.02)
st = decide_state("FOLLOW_WALL", s, P_MAZE, front_blocked=True)
check("4 maze: callejón encajonado -> RECOVER (180)", st == "RECOVER", st)
st_loop = decide_state("FOLLOW_WALL", s, P, front_blocked=True)
check("4 loop_boxes: caja/boxed reading -> NO RECOVER 180", st_loop == "TURN_IN", st_loop)

# ── Escenario 5: pared derecha demasiado LEJOS (aún no apertura) -> acercarse ──
s = Sectors(front=2.0, left=2.0, right=P["wall_target"] + 0.20)
st = decide_state("FOLLOW_WALL", s, P)
lin, ang = follow_cmd(s, P)
check("5 pared der lejos -> sigue FOLLOW_WALL", st == "FOLLOW_WALL", st)
# err < 0 (lejos) -> para right-wall, angular<0 = girar hacia la pared (derecha)
check("5 pared der lejos -> gira hacia la pared (ang<0)", ang < 0, ang)

# ── Escenario 6: pared derecha demasiado CERCA -> alejarse ──
s = Sectors(front=2.0, left=2.0, right=P["wall_target"] - 0.05)
lin, ang = follow_cmd(s, P)
# err > 0 (cerca) -> angular>0 = girar a la izquierda (alejarse del muro der)
check("6 pared der cerca -> gira alejándose (ang>0)", ang > 0, ang)

# ── Escenario 7: zona de frenado frontal (front_block < front < front_slow) ──
s = Sectors(front=(P["front_block"] + P["front_slow"]) / 2, left=2.0, right=P["wall_target"])
st = decide_state("FOLLOW_WALL", s, P, front_blocked=False)
lin, ang = follow_cmd(s, P)
check("7 frente intermedio -> sigue FOLLOW_WALL", st == "FOLLOW_WALL", st)
check("7 frente intermedio -> reduce velocidad (v_min<=lin<v_max)",
      P["v_min"] <= lin < P["v_max"], lin)

# ── Escenario 8: histéresis frontal — front_blocked explícito manda sobre el umbral ──
# frente > front_block pero el nodo aún lo considera bloqueado por histéresis (saliendo de un giro)
s = Sectors(front=(P["front_block"] + P["front_clear"]) / 2, left=2.0, right=P["wall_target"])
st = decide_state("TURN_IN", s, P, front_blocked=True)
check("8 histéresis: front_blocked=True fuerza TURN_IN aunque front>front_block",
      st == "TURN_IN", st)

# ── Escenario 9: ambos lados abiertos -> NO girar en círculo; avanzar recto hasta reacoplar pared ──
s = Sectors(front=2.0, left=P["wall_lost"] + 0.4, right=P["wall_lost"] + 0.4)
st = decide_state("FOLLOW_WALL", s, P, front_blocked=False)
hold = should_hold_straight(s, P, front_blocked=False)
check("9 sin pared lateral visible -> FSM pura ve apertura",
      st == "TURN_OUT", st)
check("9 sin pared lateral visible -> supervisor mantiene recto anti-círculo",
      hold is True, hold)

print()
if _fail == 0:
    print("RESULT: ALL SCENARIO TESTS PASSED")
else:
    print(f"RESULT: {_fail} SCENARIO TEST(S) FAILED")
    raise SystemExit(1)
