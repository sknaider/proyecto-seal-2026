#!/usr/bin/env python3
"""
maze_fsm.py — Núcleo PURO y testeable del FSM del Escenario D (El Laberinto del Chaski).
Autor: JARVIS (Lead impl ROS2, con ADA) · Gran Prix CapyTown · 10-jul-2026.

DISEÑO: separa la DECISIÓN (esta FSM pura, sin ROS) de la I/O (el nodo ROS la envuelve).
Igual patrón que maze_navigator.py / box_detector.py → verificable por efecto sin hardware.

Cubre los requisitos de la matriz de ALICE que faltaban:
  N2 intersección · N3 dead-end 180° + celda visitada · V3 parada 3s ante PARE ·
  F1 arbitraje (cámara-manda-PARAR sobre LiDAR) · F2 FSM completa.

La FSM NO reimplementa el wall-following (eso lo da maze_navigator, ya testeado);
decide el ESTADO y delega el comando fino de pasillo a la capa geométrica existente.

Entradas por tick (SensorFrame): distancias LiDAR por sector (front/right/left),
flag `pare` (de la cámara/visión, tópico /pare), conteo de cajas, y pose odom (x,y,yaw).
Salida por tick (Decision): estado + comando (v, w) + eventos (marcar celda, log PARE).
"""
from dataclasses import dataclass, field
import math

# Estados del reto (consigna Escenario D)
EXPLORAR      = "EXPLORAR"        # avanzar siguiendo pared hasta un evento
INTERSECCION  = "INTERSECCION"    # apertura lateral detectada -> decidir giro
PARAR_PARE    = "PARAR_PARE"      # PARE detectado -> parada completa
ESPERAR_3S    = "ESPERAR_3S"      # mantener parada ~3s
DECIDIR_GIRO  = "DECIDIR_GIRO"    # elegir dirección en la intersección
GIRAR         = "GIRAR"           # ejecutando giro
DEAD_END      = "DEAD_END"        # sin salida -> 180° + marcar celda
META          = "META"            # llegada

# Umbrales geométricos (m). Alineados con maze_navigator/robot 9.
FRONT_BLOCK = 0.35    # pared al frente
SIDE_OPEN   = 0.90    # apertura lateral => hay pasillo/intersección
SIDE_WALL   = 0.45    # hay pared a ese lado
PARE_STOP_S = 3.0     # segundos de parada ante PARE (consigna: ~3s)
CELL_M      = 0.60    # tamaño de celda del maze 6x4 (360/6 = 60cm)


@dataclass
class SensorFrame:
    front: float
    right: float
    left: float
    pare: bool = False
    boxes: int = 0
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0
    at_goal: bool = False   # señal externa de META (ej. marker/última celda)


@dataclass
class Decision:
    state: str
    v: float          # lineal (m/s); 0 = detenido
    w: float          # angular (rad/s)
    mark_cell: tuple = None    # (cx, cy) si hay que marcar celda visitada
    log_pare: bool = False     # True el tick en que se respeta un PARE (para métricas)
    reason: str = ""


class MazeFSM:
    """FSM pura. `step(frame, now)` devuelve una Decision. Sin dependencias de ROS."""

    def __init__(self):
        self.state = EXPLORAR
        self.pare_since = None       # timestamp en que empezó la parada por PARE
        self.pare_handled = set()    # celdas donde ya se respetó un PARE (no re-parar en la misma)
        self.turn_target = None      # yaw objetivo cuando GIRAR/DEAD_END
        self.visited = set()         # celdas visitadas (6x4)

    # -- helpers puros --
    @staticmethod
    def cell_of(x, y):
        return (int(math.floor(x / CELL_M)), int(math.floor(y / CELL_M)))

    @staticmethod
    def _ang_norm(a):
        while a > math.pi:  a -= 2 * math.pi
        while a < -math.pi: a += 2 * math.pi
        return a

    def _reached_turn(self, yaw):
        return self.turn_target is not None and abs(self._ang_norm(self.turn_target - yaw)) < 0.08

    # -- máquina de estados --
    def step(self, f: SensorFrame, now: float) -> Decision:
        cell = self.cell_of(f.x, f.y)
        newly = cell not in self.visited
        if newly:
            self.visited.add(cell)

        # META tiene prioridad absoluta
        if f.at_goal:
            self.state = META
            return Decision(META, 0.0, 0.0, reason="meta alcanzada")

        # ── F1 ARBITRAJE: PARE de la cámara MANDA sobre el LiDAR ──
        # Un PARE (no ya manejado en esta celda) fuerza PARAR_PARE aunque el pasillo esté libre.
        # [FIX FABLE] removidas pare_edge/_last_pare: eran variables muertas (nunca se leían).
        if f.pare and cell not in self.pare_handled and self.state not in (PARAR_PARE, ESPERAR_3S):
            self.state = PARAR_PARE
            self.pare_since = now
            return Decision(PARAR_PARE, 0.0, 0.0, log_pare=True,
                            mark_cell=cell if newly else None,
                            reason="PARE detectado (arbitraje cámara>LiDAR)")

        if self.state == PARAR_PARE:
            # [FIX FABLE] check explícito is None: 0.0 es falsy en Python, `or now` pisaría el timer.
            if self.pare_since is None:
                self.pare_since = now
            self.state = ESPERAR_3S
            return Decision(ESPERAR_3S, 0.0, 0.0, reason="parada completa, esperando 3s")

        if self.state == ESPERAR_3S:
            # [FIX FABLE] si pare_since==0.0, `or now` daba elapsed=0 y nunca completaba los 3s.
            start = self.pare_since if self.pare_since is not None else now
            if now - start >= PARE_STOP_S:
                self.pare_handled.add(cell)   # ya se respetó el PARE en esta celda
                self.state = EXPLORAR
                return Decision(EXPLORAR, 0.0, 0.0, reason="PARE respetado 3s, reanudar")
            return Decision(ESPERAR_3S, 0.0, 0.0, reason="manteniendo parada PARE")

        # ── N3 DEAD-END: frente + ambos lados bloqueados -> 180° ──
        boxed = (f.front < FRONT_BLOCK and f.right < SIDE_WALL and f.left < SIDE_WALL)
        if boxed and self.state != GIRAR:
            self.state = DEAD_END
            self.turn_target = self._ang_norm(f.yaw + math.pi)
            return Decision(DEAD_END, 0.0, 0.9, mark_cell=cell,
                            reason="callejón sin salida -> 180° + marcar celda")

        if self.state in (GIRAR, DEAD_END):
            if self._reached_turn(f.yaw):
                self.turn_target = None
                self.state = EXPLORAR
                return Decision(EXPLORAR, 0.10, 0.0, reason="giro completo, avanzar")
            # seguir girando hacia el objetivo
            err = self._ang_norm((self.turn_target or f.yaw) - f.yaw)
            return Decision(self.state, 0.0, 0.9 * (1 if err >= 0 else -1), reason="girando")

        # ── N2 INTERSECCIÓN: apertura en la pared SEGUIDA (derecha) o frente bloqueado ──
        # Regla de mano-derecha: seguimos la pared derecha. Que se abra la IZQUIERDA en un
        # pasillo recto NO es una intersección para girar (o giraríamos en cada corredor);
        # la intersección real es que se abra la DERECHA (la seguida) o que el frente se bloquee.
        opening_right = f.right > SIDE_OPEN
        opening_left = f.left > SIDE_OPEN
        front_blocked = f.front < FRONT_BLOCK
        if (opening_right or front_blocked):
            self.state = DECIDIR_GIRO
            # Política de exploración: preferir celda NO visitada; regla de mano-derecha por defecto.
            if opening_right and self._prefers(cell, f.yaw, +1):
                self.turn_target = self._ang_norm(f.yaw - math.pi / 2)
                d = Decision(GIRAR, 0.0, -0.9, reason="intersección: giro derecha (explorar)")
            elif opening_left and self._prefers(cell, f.yaw, -1):
                self.turn_target = self._ang_norm(f.yaw + math.pi / 2)
                d = Decision(GIRAR, 0.0, 0.9, reason="intersección: giro izquierda (explorar)")
            elif front_blocked:
                # sin apertura preferida pero frente bloqueado -> girar al lado abierto
                if opening_left:
                    self.turn_target = self._ang_norm(f.yaw + math.pi / 2); w = 0.9
                elif opening_right:
                    self.turn_target = self._ang_norm(f.yaw - math.pi / 2); w = -0.9
                else:
                    self.state = DEAD_END; self.turn_target = self._ang_norm(f.yaw + math.pi)
                    return Decision(DEAD_END, 0.0, 0.9, mark_cell=cell, reason="frente bloqueado sin apertura -> 180°")
                d = Decision(GIRAR, 0.0, w, reason="frente bloqueado -> girar a apertura")
            else:
                # apertura pero ya visitada por ambos: seguir recto (delegar a wall-follow)
                self.state = EXPLORAR
                return Decision(EXPLORAR, 0.12, 0.0, reason="apertura visitada, seguir explorando")
            self.state = GIRAR
            return d

        # ── EXPLORAR: pasillo libre -> avanzar (el nodo ROS mezcla con wall-follow P) ──
        self.state = EXPLORAR
        # control P suave sobre pared derecha para no chocar (fino lo da maze_navigator)
        err = 0.30 - f.right if f.right < SIDE_OPEN else 0.0
        w = max(-0.4, min(0.4, 1.2 * err))
        return Decision(EXPLORAR, 0.15, w, mark_cell=cell if newly else None,
                        reason="avanzando en pasillo")

    def _prefers(self, cell, yaw, side):
        """Prefiere abrir hacia una celda NO visitada (exploración eficiente)."""
        # celda estimada al girar `side` (derecha=-1 en yaw)
        ang = yaw - side * math.pi / 2
        nx = cell[0] + round(math.cos(ang)); ny = cell[1] + round(math.sin(ang))
        return (nx, ny) not in self.visited


# ─────────────────────────── self-test (sin ROS, por efecto) ───────────────────────────
def _selftest():
    ok = True
    def chk(name, cond):
        nonlocal ok; ok &= bool(cond); print(f"  [{'OK' if cond else 'FAIL'}] {name}")

    # 1) Pasillo libre -> EXPLORAR, avanza (v>0)
    m = MazeFSM(); d = m.step(SensorFrame(front=2.0, right=0.30, left=2.0), 0.0)
    chk("pasillo libre -> EXPLORAR avanza", d.state == EXPLORAR and d.v > 0)

    # 2) PARE de cámara con pasillo LIBRE -> igual PARA (arbitraje F1)
    m = MazeFSM(); d = m.step(SensorFrame(front=2.0, right=0.30, left=2.0, pare=True), 0.0)
    chk("F1 arbitraje: PARE con pasillo libre -> PARAR_PARE (v=0)", d.state == PARAR_PARE and d.v == 0 and d.log_pare)

    # 3) Espera 3s ante PARE, luego reanuda
    m = MazeFSM(); m.step(SensorFrame(2.0, 0.30, 2.0, pare=True), 0.0)      # PARAR_PARE
    m.step(SensorFrame(2.0, 0.30, 2.0, pare=True), 0.1)                     # ESPERAR_3S
    d_wait = m.step(SensorFrame(2.0, 0.30, 2.0, pare=True), 1.0)            # aún esperando
    d_go = m.step(SensorFrame(2.0, 0.30, 2.0, pare=False), 3.2)            # >=3s -> reanuda
    chk("V3 parada ~3s: mantiene a 1s, reanuda a 3.2s", d_wait.state == ESPERAR_3S and d_go.state == EXPLORAR)

    # 4) Dead-end (frente+2 lados bloqueados) -> DEAD_END con 180° + marca celda
    m = MazeFSM(); d = m.step(SensorFrame(front=0.20, right=0.30, left=0.30), 0.0)
    chk("N3 dead-end -> 180° + marca celda", d.state == DEAD_END and d.mark_cell is not None and d.w != 0)

    # 5) Intersección (apertura derecha) -> decide giro
    m = MazeFSM(); d = m.step(SensorFrame(front=2.0, right=1.5, left=0.30), 0.0)
    chk("N2 intersección apertura derecha -> GIRAR", d.state == GIRAR)

    # 6) META fuerza parada
    m = MazeFSM(); d = m.step(SensorFrame(2.0, 0.30, 2.0, at_goal=True), 0.0)
    chk("META -> detiene", d.state == META and d.v == 0)

    # 7) No re-parar en la MISMA celda por el mismo PARE (evita loop de parada)
    m = MazeFSM()
    m.step(SensorFrame(2.0, 0.30, 2.0, pare=True), 0.0)   # PARAR
    m.step(SensorFrame(2.0, 0.30, 2.0, pare=True), 0.1)   # ESPERAR
    m.step(SensorFrame(2.0, 0.30, 2.0, pare=True), 3.2)   # respetado -> pare_handled
    d = m.step(SensorFrame(2.0, 0.30, 2.0, pare=True), 3.3)  # mismo PARE misma celda -> NO re-para
    chk("no re-para en misma celda (anti-loop PARE)", d.state == EXPLORAR)

    print(f"\nSELF-TEST maze_fsm: {'✅ VERDE (7/7)' if ok else '❌ FALLO'}")
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
