#!/usr/bin/env python3
"""
granprix_metrics.py — Logger de métricas del Gran Prix (E1 de la matriz de ALICE).
Autor: JARVIS · CapyTown Escenario D · 10-jul-2026.

Produce metricas_granprix.csv con las métricas que la consigna exige por ronda:
tiempo, colisiones, PARE respetados, exploración (celdas visitadas / total).
PURO/testeable (sin ROS): el nodo ROS o el sim lo alimentan tick a tick.

Consigna E1: "metricas_granprix.csv (rondas corridas: tiempo, colisiones,
PARE respetados, exploración)".
"""
import csv
import os

TOTAL_CELLS = 6 * 4  # maze 6x4 del Escenario D


class GranPrixMetrics:
    """Acumula métricas de UNA ronda. Llamar update() por tick, finish() al terminar."""

    def __init__(self, ronda: int, modo: str = "exploracion"):
        self.ronda = ronda
        self.modo = modo                 # "exploracion" | "time_attack"
        self.t0 = None
        self.t_end = None
        self.colisiones = 0
        self.pare_respetados = 0
        self.visited = set()
        self.reached_meta = False
        self._collision_active = False   # anti-doble-conteo de la misma colisión

    def update(self, now: float, *, cell=None, collision: bool = False,
               pare_respetado: bool = False, at_goal: bool = False,
               min_obstacle_dist: float = None, robot_radius: float = 0.11):
        if self.t0 is None:
            self.t0 = now
        # colisión: por flag directo o por distancia < radio (flanco, no cada tick)
        hit = collision or (min_obstacle_dist is not None and min_obstacle_dist < robot_radius)
        if hit and not self._collision_active:
            self.colisiones += 1
        self._collision_active = hit
        if pare_respetado:
            self.pare_respetados += 1
        if cell is not None:
            self.visited.add(cell)
        if at_goal and not self.reached_meta:
            self.reached_meta = True
            self.t_end = now

    def finish(self, now: float = None):
        if self.t_end is None:
            self.t_end = now if now is not None else self.t0

    @property
    def tiempo_s(self):
        if self.t0 is None:
            return 0.0
        return round((self.t_end if self.t_end is not None else self.t0) - self.t0, 2)

    @property
    def exploracion_pct(self):
        return round(100.0 * len(self.visited) / TOTAL_CELLS, 1)

    def row(self):
        return {
            "ronda": self.ronda,
            "modo": self.modo,
            "tiempo_s": self.tiempo_s,
            "colisiones": self.colisiones,
            "pare_respetados": self.pare_respetados,
            "celdas_visitadas": len(self.visited),
            "exploracion_pct": self.exploracion_pct,
            "llego_meta": int(self.reached_meta),
        }


def write_csv(path: str, metrics_list):
    """Escribe metricas_granprix.csv con una fila por ronda."""
    fields = ["ronda", "modo", "tiempo_s", "colisiones", "pare_respetados",
              "celdas_visitadas", "exploracion_pct", "llego_meta"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for m in metrics_list:
            w.writerow(m.row())
    return path


# ── self-test (sin ROS, por efecto) ──
def _selftest():
    ok = True
    def chk(n, c):
        nonlocal ok; ok &= bool(c); print(f"  [{'OK' if c else 'FAIL'}] {n}")

    m = GranPrixMetrics(ronda=1, modo="exploracion")
    m.update(0.0, cell=(0, 0))
    m.update(0.5, cell=(1, 0), min_obstacle_dist=0.05)   # colisión (flanco)
    m.update(0.6, cell=(1, 0), min_obstacle_dist=0.05)   # misma colisión -> no re-cuenta
    m.update(0.7, cell=(1, 0), min_obstacle_dist=0.5)    # se aleja
    m.update(1.0, cell=(2, 0), min_obstacle_dist=0.04)   # NUEVA colisión
    m.update(2.0, cell=(2, 1), pare_respetado=True)
    m.update(5.0, cell=(3, 1), at_goal=True)
    m.finish()

    chk("tiempo medido (0->5s)", m.tiempo_s == 5.0)
    chk("colisiones anti-doble-conteo (2, no 3)", m.colisiones == 2)
    chk("PARE respetado contado", m.pare_respetados == 1)
    chk("celdas visitadas (5 únicas)", len(m.visited) == 5)
    chk("exploración % (5/24=20.8)", abs(m.exploracion_pct - 20.8) < 0.1)
    chk("llegó a META", m.reached_meta is True)

    # escritura CSV
    path = "/tmp/_test_metricas_granprix.csv"
    write_csv(path, [m])
    exists = os.path.exists(path)
    content = open(path).read() if exists else ""
    chk("CSV escrito con header + fila", exists and "ronda,modo" in content and "20.8" in content)
    if exists:
        os.remove(path)

    print(f"\nSELF-TEST granprix_metrics: {'✅ VERDE' if ok else '❌ FALLO'}")
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
