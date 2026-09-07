#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
maze_map_viz.py — Visualización del MAPA del laberinto + la RUTA MÁS CORTA
(ALICE, 2026-07-13). Complementa maze_planner.py: dibuja lo que el robot aprendió.

Muestra, sobre la rejilla 6x4 (celdas de 60cm) del Gran Prix:
  • las PAREDES (pasos entre celdas que NO están libres) en negro,
  • los PASAJES (aristas libres aprendidas) abiertos,
  • las celdas VISITADAS sombreadas,
  • INICIO (verde) y META (rojo),
  • la RUTA MÁS CORTA (BFS) resaltada en azul de INICIO a META.

Uso:
  # a) desde un mapa guardado por el robot (Ronda 1):
  python3 maze_map_viz.py --map /tmp/granprix_map.json --out /tmp/mapa_laberinto.png
  # b) demo con un laberinto de ejemplo:
  python3 maze_map_viz.py --demo --out /tmp/mapa_demo.png

Como nodo ROS (opcional): un nodo puede llamar render_maze(planner, save_path=...)
cada vez que actualiza el mapa, o al cerrar la Ronda 1, para dejar la imagen lista.
"""
from __future__ import annotations
import argparse

try:
    from maze_planner import MazePlanner, COLS, ROWS, CELL, START, GOAL, _neighbors
except Exception:  # permite correr desde otra ruta
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from maze_planner import MazePlanner, COLS, ROWS, CELL, START, GOAL, _neighbors


def render_maze(planner, path=None, save_path=None, title="CapyTown — mapa del laberinto"):
    """Dibuja el mapa aprendido + la ruta más corta. Devuelve la figura."""
    import matplotlib
    matplotlib.use("Agg")  # sin ventana (para guardar PNG en headless)
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    if path is None:
        path = planner.shortest_path()

    fig, ax = plt.subplots(figsize=(COLS * 1.1, ROWS * 1.1))
    W = 0.5  # media celda (en unidades de celda)

    # 1) celdas visitadas sombreadas
    for (c, r) in planner.visited:
        ax.add_patch(Rectangle((c - W, r - W), 1, 1, facecolor="#eef4ff", edgecolor="none"))

    # 2) paredes: para cada celda, si el paso a la vecina NO está abierto -> pared
    for c in range(COLS):
        for r in range(ROWS):
            for (nc, nr) in _neighbors((c, r)):
                if (nc, nr) < (c, r):
                    continue  # dibujar cada arista una vez
                is_open = planner._edge((c, r), (nc, nr)) in planner.open_edges
                # segmento de pared en el borde compartido entre (c,r) y (nc,nr)
                if nc == c + 1:      # vecina a la derecha -> pared vertical en x=c+0.5
                    x = c + W
                    seg = ([x, x], [r - W, r + W])
                else:                # vecina arriba -> pared horizontal en y=r+0.5
                    y = r + W
                    seg = ([c - W, c + W], [y, y])
                if not is_open:
                    ax.plot(seg[0], seg[1], color="#111", linewidth=3, solid_capstyle="round")
    # borde exterior (siempre pared)
    ax.add_patch(Rectangle((-W, -W), COLS, ROWS, fill=False, edgecolor="#111", linewidth=3))

    # 3) ruta más corta resaltada
    if path:
        xs = [c for (c, r) in path]
        ys = [r for (c, r) in path]
        ax.plot(xs, ys, color="#1f77ff", linewidth=4, marker="o", markersize=7,
                markerfacecolor="#1f77ff", zorder=5, label="ruta más corta")

    # 4) INICIO y META
    sc, sr = START
    gc, gr = GOAL
    ax.plot(sc, sr, marker="s", markersize=16, color="#2ca02c", zorder=6)
    ax.text(sc, sr - 0.32, "INICIO", ha="center", va="top", color="#2ca02c", fontsize=9, weight="bold")
    ax.plot(gc, gr, marker="*", markersize=22, color="#d62728", zorder=6)
    ax.text(gc, gr + 0.30, "META", ha="center", va="bottom", color="#d62728", fontsize=9, weight="bold")

    n = len(path) - 1 if path else None
    subt = f"ruta más corta: {n} pasos · {n * CELL * 100:.0f} cm" if path else "aún sin ruta conocida"
    ax.set_title(f"{title}\n{subt}", fontsize=11)
    ax.set_xlim(-W - 0.2, COLS - 1 + W + 0.2)
    ax.set_ylim(-W - 0.2, ROWS - 1 + W + 0.2)
    ax.set_aspect("equal")
    ax.set_xticks(range(COLS)); ax.set_yticks(range(ROWS))
    ax.grid(True, alpha=0.15)
    ax.set_xlabel("columna"); ax.set_ylabel("fila")
    fig.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=110)
        print(f"mapa guardado en {save_path}")
    return fig


def _demo_planner():
    p = MazePlanner()
    ruta = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (5, 1), (5, 2), (5, 3)]
    for a, b in zip(ruta, ruta[1:]):
        p.mark_open(a, b)
    # un desvío/callejón que NO es la ruta corta
    for a, b in [((2, 0), (2, 1)), ((2, 1), (2, 2)), ((2, 1), (3, 1))]:
        p.mark_open(a, b)
    for c in ruta + [(2, 1), (2, 2), (3, 1)]:
        p.visited.add(c)
    return p


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Visualizador del mapa del laberinto CapyTown")
    ap.add_argument("--map", help="ruta al granprix_map.json guardado por el robot")
    ap.add_argument("--demo", action="store_true", help="laberinto de ejemplo")
    ap.add_argument("--out", default="/tmp/mapa_laberinto.png", help="PNG de salida")
    a = ap.parse_args()
    if a.demo or not a.map:
        planner = _demo_planner()
    else:
        planner = MazePlanner()
        if not planner.load_map(a.map):
            raise SystemExit(f"no pude cargar el mapa: {a.map}")
    render_maze(planner, save_path=a.out)
