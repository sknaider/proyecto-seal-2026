#!/usr/bin/env python3
"""
test_maze_fsm_integration.py — Test de integración multi-tick del FSM del laberinto.
Autor: JARVIS · Gran Prix CapyTown, Escenario D · 10-jul-2026.

A diferencia del self-test unitario (decisiones de 1 tick), esto guiona el FSM por una
SECUENCIA completa de recorrido y valida el PROGRESO de estados + métricas (E1):
tiempo, PARE respetados, celdas exploradas. Testeable SIN ROS ni robot.

NO es un sim de física (eso lo valida el robot real — lección de NEXUS); es una
verificación de SECUENCIA/integración que caza bugs que los tests de 1 tick no ven.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "capytown_maze_pkg"))
from maze_fsm import MazeFSM, SensorFrame, PARAR_PARE, ESPERAR_3S, GIRAR, DEAD_END, META, EXPLORAR

# Secuencia guionada de un recorrido: (dt_desde_inicio, frame, nota)
# Simula: avanzar pasillo -> PARE en una celda -> esperar 3s -> reanudar ->
#         intersección (der abre) -> girar -> dead-end -> 180° -> META.
RUN = [
    (0.0, SensorFrame(front=2.0, right=0.30, left=2.0, x=0.1, y=0.0, yaw=0.0), "pasillo"),
    (0.2, SensorFrame(front=1.5, right=0.30, left=2.0, x=0.3, y=0.0, yaw=0.0), "pasillo"),
    (0.4, SensorFrame(front=1.0, right=0.30, left=2.0, pare=True, x=0.5, y=0.0, yaw=0.0), "PARE!"),
    (0.6, SensorFrame(front=1.0, right=0.30, left=2.0, pare=True, x=0.5, y=0.0, yaw=0.0), "parado"),
    (2.0, SensorFrame(front=1.0, right=0.30, left=2.0, pare=True, x=0.5, y=0.0, yaw=0.0), "esperando"),
    (3.6, SensorFrame(front=1.0, right=0.30, left=2.0, pare=False, x=0.5, y=0.0, yaw=0.0), "reanuda >3s"),
    (3.8, SensorFrame(front=2.0, right=1.5, left=0.30, x=0.7, y=0.0, yaw=0.0), "intersección der"),
    (4.0, SensorFrame(front=2.0, right=1.5, left=0.30, x=0.7, y=0.0, yaw=-1.4), "girando"),
    (4.3, SensorFrame(front=2.0, right=0.30, left=2.0, x=0.7, y=-0.1, yaw=-1.57), "giro ~completo"),
    (4.5, SensorFrame(front=0.20, right=0.30, left=0.30, x=0.7, y=-0.3, yaw=-1.57), "dead-end"),
    (4.7, SensorFrame(front=0.20, right=0.30, left=0.30, x=0.7, y=-0.3, yaw=-0.2), "girando 180"),
    (4.9, SensorFrame(front=2.0, right=0.30, left=2.0, x=0.7, y=-0.1, yaw=1.57), "180 ~completo"),
    (5.2, SensorFrame(front=2.0, right=0.30, left=2.0, at_goal=True, x=0.7, y=0.5, yaw=1.57), "META"),
]

def run():
    m = MazeFSM()
    states=[]; pare_ticks=0; reached_meta=False; t_meta=None
    for (t, f, note) in RUN:
        d = m.step(f, t)
        states.append(d.state)
        if d.log_pare: pare_ticks += 1
        if d.state == META and not reached_meta:
            reached_meta=True; t_meta=t
    ok=True
    def chk(n,c):
        nonlocal ok; ok&=bool(c); print(f"  [{'OK' if c else 'FAIL'}] {n}")

    # progreso de estados esperado
    chk("respetó PARE (>=1 log_pare)", pare_ticks >= 1)
    chk("pasó por PARAR_PARE/ESPERAR_3S", PARAR_PARE in states or ESPERAR_3S in states)
    chk("mantuvo parada durante la ventana PARE", states.count(ESPERAR_3S) >= 1)
    chk("ejecutó GIRAR (intersección)", GIRAR in states)
    chk("ejecutó DEAD_END (180°)", DEAD_END in states)
    chk("alcanzó META", reached_meta)
    chk("META llegó al final, no antes de PARE", t_meta is not None and t_meta > 3.0)
    # métricas E1 (formato para la matriz de ALICE)
    print(f"\n  MÉTRICAS E1: t_meta={t_meta}s · PARE_respetados={pare_ticks} · "
          f"celdas_exploradas={len(m.visited)} · estados_únicos={len(set(states))}")
    print(f"\nTEST INTEGRACIÓN maze_fsm: {'✅ VERDE' if ok else '❌ FALLO'}")
    return ok

if __name__ == "__main__":
    sys.exit(0 if run() else 1)
