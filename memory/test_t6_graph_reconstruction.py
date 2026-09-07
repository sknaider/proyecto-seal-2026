"""Tests herméticos del enforcement T6 contra el oráculo de aceptación de ALICE.

Los 3 controles del oráculo + resistencia a los bypass que SÍ cerramos (paginación, multi-finalidad,
fail-closed). Colusión = residual DOCUMENTADO (ver test al final, marcado xfail-honesto), no lo
afirmamos cerrado. Control anti-vacuo incluido.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import t6_graph_reconstruction as t6


def _b():
    return t6.InMemoryGraphBudget(window_sec=3600)


# ── los 3 controles del oráculo ──────────────────────────────────────────
def test_control_positive_pagination_over_budget_blocks():
    """POSITIVO: barrer material NUEVO (aunque paginado) hasta pasar el budget -> DENY."""
    b = _b()
    dec = None
    for page in range(6):                       # 6 páginas x 100 nodos NUEVOS = 600 > 500
        ids = [f"n{page*100+i}" for i in range(100)]
        dec = t6.check_graph_access(b, "aaron", ids, budget=500, now=1000.0 + page)
    assert dec.allow is False and dec.reason == "graph_budget_exceeded"
    assert dec.distinct_used > 500


def test_control_negative_bounded_navigation_passes():
    """NEGATIVO: navegación acotada (pocos nodos, aunque re-consultados) -> ALLOW."""
    b = _b()
    dec = None
    for _ in range(20):                          # 20 consultas de los MISMOS 30 nodos
        dec = t6.check_graph_access(b, "kary", [f"n{i}" for i in range(30)], budget=500, now=1000.0)
    assert dec.allow is True and dec.distinct_used == 30   # distinto = 30, no 600


def test_control_not_vacuous():
    """Control anti-vacuo: un budget 0 SIEMPRE deniega cualquier acceso no vacío -> el test PUEDE fallar."""
    nonce = os.urandom(3).hex()
    dec = t6.check_graph_access(_b(), "x", ["n1"], budget=0, now=1000.0)
    assert dec.allow is False, f"control {nonce}: budget 0 debería denegar"


# ── bypass que SÍ cerramos ───────────────────────────────────────────────
def test_bypass_pagination_same_nodes_does_not_respend():
    """Paginar los MISMOS nodos no re-gasta (cuenta el SET distinto, no las requests)."""
    b = _b()
    for _ in range(50):
        dec = t6.check_graph_access(b, "aaron", [f"n{i}" for i in range(100)], budget=200, now=1000.0)
    assert dec.allow is True and dec.distinct_used == 100


def test_bypass_multipurpose_does_not_reset():
    """Cambiar de 'finalidad' NO resetea: el budget es por PRINCIPAL, no por purpose."""
    b = _b()
    # simular 'distintas finalidades' = simplemente más consultas del mismo principal con material nuevo
    t6.check_graph_access(b, "aaron", [f"a{i}" for i in range(300)], budget=500, now=1000.0)
    dec = t6.check_graph_access(b, "aaron", [f"b{i}" for i in range(300)], budget=500, now=1001.0)
    assert dec.allow is False and dec.reason == "graph_budget_exceeded"  # 600 distintos > 500


def test_fail_closed_on_backend_error():
    """Fail-closed: si el backend lanza, deny (no se sirve estructura por un fallo del guard)."""
    class _Broken:
        def add_and_count(self, *a, **k): raise RuntimeError("db down")
    dec = t6.check_graph_access(_Broken(), "aaron", ["n1"], budget=500, now=1000.0)
    assert dec.allow is False and dec.reason.startswith("backend_error")


def test_enumeration_coverage_flag():
    """Cobertura sistemática alta dentro del budget -> ALLOW pero MARCADO para auditoría."""
    b = _b()
    # 400 distintos, budget 500 (no excede), pero graph_size 1000 -> 40% cobertura >> 2% alerta
    dec = t6.check_graph_access(b, "aaron", [f"n{i}" for i in range(400)],
                                budget=500, graph_size=1000, now=1000.0)
    assert dec.allow is True and dec.reason == "allow_but_enumeration_suspected"


# ── residual que halló ALICE (barrido lento cross-ventana) — AHORA CERRADO por el tier lifetime ──
def test_slow_cross_window_sweep_now_blocked():
    """El bypass que ALICE cazó: 500 nodos NUEVOS por ventana × N ventanas reconstruye el grafo sin
    exceder el budget por-ventana. El tier LIFETIME (no resetea con la ventana corta) lo caza."""
    b = t6.InMemoryGraphBudget(window_sec=3600, lifetime_sec=30 * 24 * 3600)
    blocked = False
    for w in range(8):                                   # 8 ventanas separadas por >1h
        ids = [f"n{w*500+i}" for i in range(500)]         # 500 NUEVOS cada ventana
        dec = t6.check_graph_access(b, "aaron", ids, budget=500, lifetime_budget=2000,
                                    now=1000.0 + w * 4000)  # 4000s > window_sec -> resetea rate
        if not dec.allow:
            blocked = True
            assert dec.reason == "graph_lifetime_budget_exceeded"
            break
    assert blocked, "el barrido lento cross-ventana NO fue bloqueado (residual de ALICE sigue abierto)"


# ── residual HONESTO: colusión NO está cerrada ───────────────────────────
def test_collusion_is_a_documented_residual_NOT_closed():
    """El budget PER-PRINCIPAL no caza colusión: N principales, cada uno bajo el cap, juntos barren
    el grafo. Este test DOCUMENTA el hueco (no lo esconde). Cerrarlo requiere budget de GRUPO o
    detección de correlación entre principales — fuera del alcance de este módulo per-principal."""
    b = _b()
    # 3 cómplices, cada uno 200 nodos DISTINTOS (bajo budget 300) -> los 3 PASAN...
    decs = [t6.check_graph_access(b, f"complice{k}", [f"n{k}_{i}" for i in range(200)],
                                  budget=300, now=1000.0) for k in range(3)]
    assert all(d.allow for d in decs)   # ...pese a que JUNTOS tocaron 600 nodos. HUECO conocido.
    # afirmación del residual: el módulo NO tiene señal de grupo -> por diseño, documentado.


def _main() -> int:
    import subprocess
    return subprocess.run([sys.executable, "-m", "pytest", "-q", __file__],
                          cwd=str(Path(__file__).parent)).returncode


if __name__ == "__main__":
    sys.exit(_main())
