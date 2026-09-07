#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collision_counter.py — Conteo AUTOMÁTICO de colisiones/roces para
metricas_granprix.csv (ALICE, 2026-07-13). Cierra el campo `colisiones` que hoy
se llena a mano (colisiones_manual=-1, «completar viendo el video/ros2 bag»).

IDEA (honesta, es un PROXY, no un sensor de contacto): el robot no tiene bumper,
pero el LiDAR sí sabe cuándo el frente quedó peligrosamente cerca de una pared.
Un ROCE/colisión se aproxima como: el rango frontal cruzó por DEBAJO de un umbral
de contacto (`touch_dist`, un poco menor que emerg_dist) y se mantuvo. Para no
contar 20 veces el mismo toque, se aplica DEBOUNCE por flanco + tiempo muerto:
un evento nuevo solo cuenta si hubo una recuperación (frente se despejó sobre
`clear_dist`) entre medio.

LIMITACIÓN declarada (no la escondo): sobre-cuenta si el robot pasa legítimamente
muy pegado sin tocar, y sub-cuenta un roce lateral que el frente no ve. Es una
COTA razonable y objetiva, mejor que el -1 manual; para el conteo oficial de la
competencia sigue mandando el árbitro/video. Sirve para tener una métrica viva
comparable entre corridas.

USO (lo llama maze_solver en cada tick, con el rango frontal ya calculado):
    cc = CollisionCounter(touch_dist=0.10, clear_dist=0.20)
    # cada tick:
    cc.update(front_range_m, t_now_s)
    # al escribir métricas:  colisiones = cc.count
"""
from __future__ import annotations


class CollisionCounter:
    def __init__(self, touch_dist=0.10, clear_dist=0.20, min_gap_s=1.0):
        # touch_dist: frente por debajo de esto = posible contacto (m).
        # clear_dist: frente por encima de esto = recuperado (rearma el detector).
        # min_gap_s: tiempo mínimo entre dos colisiones distintas (anti-rebote).
        assert clear_dist > touch_dist, "clear_dist debe ser > touch_dist"
        self.touch_dist = float(touch_dist)
        self.clear_dist = float(clear_dist)
        self.min_gap_s = float(min_gap_s)
        self.count = 0
        self._armed = True          # listo para contar el próximo toque
        self._last_hit_t = None

    def update(self, front_range_m, t_now_s):
        """Alimentar con el rango frontal (m) y el tiempo (s) en cada tick.
        Devuelve True SOLO en el tick donde se registra una colisión nueva."""
        r = front_range_m
        # rango inválido (nan/inf/<=0) → ignorar, no rearmar ni contar
        if r is None or r != r or r <= 0.0:
            return False
        # recuperación: el frente se despejó → rearmar para el próximo toque
        if r >= self.clear_dist:
            self._armed = True
            return False
        # toque: frente por debajo del umbral y detector armado
        if r < self.touch_dist and self._armed:
            if self._last_hit_t is not None and (t_now_s - self._last_hit_t) < self.min_gap_s:
                # demasiado pronto tras el último → mismo toque, no re-contar
                self._armed = False
                return False
            self.count += 1
            self._last_hit_t = t_now_s
            self._armed = False     # no re-contar hasta que se despeje (clear_dist)
            return True
        return False

    def reset(self):
        self.count = 0
        self._armed = True
        self._last_hit_t = None


# ── Autotest por efecto ─────────────────────────────────────────────────────
def _selftest():
    cc = CollisionCounter(touch_dist=0.10, clear_dist=0.20, min_gap_s=1.0)
    # 1) un toque limpio: se acerca, toca, se despeja
    assert cc.update(0.30, 0.0) is False          # lejos
    assert cc.update(0.08, 0.1) is True           # TOQUE #1
    assert cc.count == 1
    # 2) sigue pegado varios ticks → NO re-cuenta (mismo toque)
    assert cc.update(0.07, 0.2) is False
    assert cc.update(0.06, 0.3) is False
    assert cc.count == 1
    # 3) se despeja y vuelve a tocar (con gap suficiente) → cuenta de nuevo
    assert cc.update(0.25, 1.5) is False          # recuperado → rearma
    assert cc.update(0.05, 1.6) is True           # TOQUE #2
    assert cc.count == 2
    # 4) toque muy seguido tras despejar pero dentro de min_gap → NO cuenta
    assert cc.update(0.30, 1.7) is False          # rearma
    assert cc.update(0.05, 2.0) is False          # gap 0.4s < 1.0 → no cuenta
    assert cc.count == 2
    # 5) rango inválido no rompe ni cuenta
    assert cc.update(float('nan'), 3.0) is False
    assert cc.update(0.0, 3.1) is False
    assert cc.count == 2
    # 6) reset
    cc.reset(); assert cc.count == 0
    print("collision_counter autotest: OK (2 colisiones distintas contadas, rebotes ignorados)")


if __name__ == "__main__":
    _selftest()
