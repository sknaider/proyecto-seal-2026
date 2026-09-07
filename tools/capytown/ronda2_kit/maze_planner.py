#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
maze_planner.py — Planificador de RUTA MÁS CORTA para la Ronda 2 (Time Attack)
del CapyTown Gran Prix «El Qhapaq Ñan». Pieza NUEVA (ALICE, 2026-07-13).
================================================================================
POR QUÉ EXISTE (hueco de la rúbrica que cerré en la review):
  El maze_solver actual es un wall-follower REACTIVO — llega a la META pero NO
  optimiza la ruta. La rúbrica pide DOS rondas sobre la misma pista:
    • Ronda 1 (Exploración): recorre sin conocer el trazado.
    • Ronda 2 (Time Attack): «gana el menor tiempo con la RUTA MÁS CORTA».
  Un wall-follower corre igual en las dos → la eficiencia de R2 ≈ R1. Este módulo
  aporta lo que falta: en R1 MAPEA el laberinto (rejilla 6x4, celdas de 60cm) a
  partir de /odom, y en R2 calcula la ruta más corta INICIO→META por BFS y la
  entrega como waypoints (centros de celda) que el FSM sigue.

DISEÑO (puro Python, sin ROS — importable y testeable; el nodo lo enchufa):
  - La pista es 360x240cm = rejilla 6 columnas x 4 filas (24 celdas de 60cm).
  - INICIO = celda inferior izquierda (0,0); META = superior derecha (5,3).
  - Grafo: nodos = celdas; aristas = pasos LIBRES entre celdas adyacentes
    (4-conexo: N/S/E/O). Una arista se marca ABIERTA cuando en R1 el robot
    cruzó de una celda a la vecina (o el LiDAR vio apertura). Lo NO observado
    queda desconocido (cerrado por defecto → BFS solo usa lo confirmado libre).
  - BFS da la ruta con MENOS celdas (= más corta en una rejilla uniforme).

USO TÍPICO (lo cablea maze_solver.py):
    planner = MazePlanner(origin_xy=(0.0, 0.0))   # odom de la celda (0,0)
    # --- Ronda 1: por cada tick de /odom ---
    planner.observe_pose(x, y)                     # infiere celda + marca aristas
    # --- Fin de R1 / inicio de R2 ---
    wps = planner.plan_waypoints()                 # [(x,y), ...] INICIO→META
    # el FSM navega de waypoint en waypoint (control a punto).

Autor: ALICE · 2026-07-13 · pensado para acoplarse sin tocar el wall-following.
"""
from __future__ import annotations
from collections import deque

# ── Geometría oficial de la pista (rúbrica) ──
COLS = 6            # columnas (eje X)
ROWS = 4            # filas (eje Y)
CELL = 0.60        # m, lado de celda
START = (0, 0)     # INICIO: celda inferior izquierda
GOAL = (COLS - 1, ROWS - 1)   # META: celda superior derecha (5,3)


def _neighbors(cell):
    """Vecinas 4-conexas dentro de la rejilla."""
    c, r = cell
    for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nc, nr = c + dc, r + dr
        if 0 <= nc < COLS and 0 <= nr < ROWS:
            yield (nc, nr)


class MazePlanner:
    """Mapea la rejilla en R1 (por odometría) y planifica la ruta más corta en R2."""

    def __init__(self, origin_xy=(0.0, 0.0), cell=CELL, invert_x=False, invert_y=False):
        # origin_xy = coordenada /odom del CENTRO de la celda INICIO (0,0).
        self.ox, self.oy = origin_xy
        self.cell = cell
        self.invert_x = invert_x   # si el +X del robot va hacia columnas decrecientes
        self.invert_y = invert_y   # idem para +Y / filas
        # aristas abiertas: set de pares ORDENADOS de celdas (a,b) con a<b
        self.open_edges = set()
        self.visited = set()
        self._last_cell = None

    # ── Mapeo odom <-> celda ──────────────────────────────────────────────
    def cell_from_xy(self, x, y):
        """(x,y) de /odom → (col,row) de la rejilla, recortado a los límites."""
        c = int(round((x - self.ox) / self.cell))
        r = int(round((y - self.oy) / self.cell))
        if self.invert_x:
            c = (COLS - 1) - c
        if self.invert_y:
            r = (ROWS - 1) - r
        c = max(0, min(COLS - 1, c))
        r = max(0, min(ROWS - 1, r))
        return (c, r)

    def cell_center_xy(self, cell):
        """(col,row) → (x,y) /odom del centro de esa celda (para waypoints)."""
        c, r = cell
        if self.invert_x:
            c = (COLS - 1) - c
        if self.invert_y:
            r = (ROWS - 1) - r
        return (self.ox + c * self.cell, self.oy + r * self.cell)

    # ── Ronda 1: observación ──────────────────────────────────────────────
    def _edge(self, a, b):
        return (a, b) if a <= b else (b, a)

    def mark_open(self, a, b):
        """Marca EXPLÍCITAMENTE una arista libre entre dos celdas adyacentes."""
        if b in dict.fromkeys(_neighbors(a)):
            self.open_edges.add(self._edge(a, b))

    def observe_pose(self, x, y):
        """Llamar en cada tick de /odom durante R1. Infiere la celda actual y,
        si cambió a una celda ADYACENTE, marca esa arista como libre (el robot
        acaba de cruzarla, o sea no había pared)."""
        cell = self.cell_from_xy(x, y)
        self.visited.add(cell)
        if self._last_cell is not None and cell != self._last_cell:
            # solo confiamos en transiciones a una celda adyacente (sin teletransporte)
            if cell in dict.fromkeys(_neighbors(self._last_cell)):
                self.open_edges.add(self._edge(self._last_cell, cell))
        self._last_cell = cell
        return cell

    # ── Ronda 2: planificación ────────────────────────────────────────────
    def shortest_path(self, start=START, goal=GOAL):
        """BFS sobre las aristas ABIERTAS. Devuelve la lista de celdas
        INICIO→META (la más corta), o None si aún no hay ruta conocida."""
        if start == goal:
            return [start]
        prev = {start: None}
        q = deque([start])
        while q:
            cur = q.popleft()
            if cur == goal:
                break
            for nb in _neighbors(cur):
                if nb in prev:
                    continue
                if self._edge(cur, nb) in self.open_edges:
                    prev[nb] = cur
                    q.append(nb)
        if goal not in prev:
            return None            # todavía no se conoce una ruta libre completa
        # reconstruir
        path = []
        node = goal
        while node is not None:
            path.append(node)
            node = prev[node]
        path.reverse()
        return path

    def plan_waypoints(self, start=START, goal=GOAL):
        """Ruta más corta como waypoints (x,y) de /odom listos para el control
        a-punto del FSM. None si aún no hay ruta conocida."""
        path = self.shortest_path(start, goal)
        if path is None:
            return None
        return [self.cell_center_xy(c) for c in path]

    # ── Persistencia entre rondas (R1 y R2 son runs de ROS SEPARADOS) ─────
    # El mapa aprendido en Ronda 1 DEBE sobrevivir al cierre del nodo para que
    # Ronda 2 lo use. Guardar al terminar R1; cargar al arrancar R2.
    def save_map(self, path="/tmp/granprix_map.json"):
        """Serializa las aristas abiertas + celdas visitadas a JSON."""
        import json
        data = {
            "open_edges": [list(map(list, e)) for e in sorted(self.open_edges)],
            "visited": [list(c) for c in sorted(self.visited)],
        }
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def load_map(self, path="/tmp/granprix_map.json"):
        """Carga un mapa guardado (para Ronda 2). Devuelve True si cargó algo."""
        import json
        import os
        if not os.path.exists(path):
            return False
        with open(path) as f:
            data = json.load(f)
        for e in data.get("open_edges", []):
            a, b = tuple(e[0]), tuple(e[1])
            self.open_edges.add(self._edge(a, b))
        for c in data.get("visited", []):
            self.visited.add(tuple(c))
        return True

    # ── Métrica de la rúbrica ─────────────────────────────────────────────
    def optimal_len_cm(self, start=START, goal=GOAL):
        """Longitud de la ruta más corta conocida en cm (para long_optima_cm de
        metricas_granprix.csv → cierra el campo que hoy se carga a mano)."""
        path = self.shortest_path(start, goal)
        if path is None:
            return None
        return (len(path) - 1) * self.cell * 100.0


# ============================================================================
# AUTOTEST por efecto (sin ROS): construye un laberinto de juguete con dos rutas
# (una corta y una con desvío) y verifica que BFS elige la corta.
#   python3 maze_planner.py
# ============================================================================
def _selftest():
    p = MazePlanner(origin_xy=(0.0, 0.0))
    # Simulo R1: el robot exploró y dejó libres estas transiciones.
    # Ruta CORTA por el borde inferior + subida por la última columna:
    corta = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (5, 0),
             (5, 1), (5, 2), (5, 3)]
    for a, b in zip(corta, corta[1:]):
        p.mark_open(a, b)
    # Un DESVÍO adicional (callejón) que NO debe elegir:
    for a, b in [((2, 0), (2, 1)), ((2, 1), (2, 2))]:
        p.mark_open(a, b)

    path = p.shortest_path()
    assert path is not None, "debería existir ruta INICIO→META"
    assert path[0] == START and path[-1] == GOAL, "ruta debe ir de INICIO a META"
    assert len(path) == len(corta), f"esperaba {len(corta)} celdas, dio {len(path)}"
    assert path == corta, f"BFS no eligió la ruta corta: {path}"
    wps = p.plan_waypoints()
    assert len(wps) == len(corta)
    assert wps[0] == (0.0, 0.0)
    assert wps[-1] == (5 * CELL, 3 * CELL)
    assert abs(p.optimal_len_cm() - (len(corta) - 1) * CELL * 100.0) < 1e-6

    # Sin ruta conocida (grafo vacío) → None, no crashea:
    vacio = MazePlanner()
    assert vacio.shortest_path() is None
    assert vacio.plan_waypoints() is None
    assert vacio.optimal_len_cm() is None

    # Mapeo odom→celda y de vuelta:
    assert p.cell_from_xy(0.0, 0.0) == (0, 0)
    assert p.cell_from_xy(5 * CELL, 3 * CELL) == (5, 3)
    assert p.cell_from_xy(0.62, 0.01) == (1, 0)   # tolerancia de redondeo
    print("maze_planner autotest: OK")
    print("  ruta más corta:", path)
    print("  waypoints:", [(round(x, 2), round(y, 2)) for x, y in wps])
    print("  long_optima_cm:", p.optimal_len_cm())


if __name__ == "__main__":
    _selftest()
