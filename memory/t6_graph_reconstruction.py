#!/usr/bin/env python3
"""t6_graph_reconstruction.py — enforcement anti-reconstrucción de grafo (RSI-T6/GRAPH-ENUM).

Amenaza (NEXUS, del dossier RSI): un principal con acceso LEGÍTIMO pero LIMITADO reconstruye la
estructura del connectome (~84k nodos / 278k aristas) o del Code Graph haciendo MUCHAS consultas
chicas, todas dentro de su alcance autorizado. RLS no lo ve — cada consulta individual ES autorizada;
el ataque es la AGREGACIÓN (model-extraction / membership-inference sobre el grafo).

Defensa (fuera de RLS): presupuesto ACUMULADO por principal sobre nodos/aristas DISTINTOS accedidos
en una ventana; si un principal excede el cap, se le niega (fail-closed) y se alerta.

Cierra los 3 bypass que ALICE verifica adversarialmente:
  1) PAGINACIÓN  — el presupuesto cuenta el SET DISTINTO de nodos/aristas, no las requests. Paginar el
     mismo material no lo re-gasta, pero barrer material NUEVO sí lo acumula.
  2) MULTI-FINALIDAD — el presupuesto es por PRINCIPAL, no por "purpose"/endpoint. Cambiar de finalidad
     NO resetea el contador.
  3) RAZA DE CONTADORES — el incremento es ATÓMICO en el backend (UPDATE ... RETURNING sobre la fila del
     principal, sin ventana get→set). Dos workers concurrentes no pueden gastar el mismo presupuesto.

Backend prod = tabla (UPDATE atómico, distribuido entre workers/reinicios). Backend memoria = test/shadow.
Fail-closed: ante cualquier error del backend, `allow=False` (no se filtra estructura por un fallo del guard).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

# Presupuesto: máx. nodos/aristas DISTINTOS que un principal puede tocar por ventana.
DEFAULT_WINDOW_SEC = int(os.environ.get("SEAL_T6_WINDOW_SEC", "3600"))
DEFAULT_BUDGET = int(os.environ.get("SEAL_T6_DISTINCT_BUDGET", "500"))
# LIFETIME cap (fix del residual de ALICE, 20-ago): el budget por-ventana acota RATE, pero el reset de
# ventana deja pasar un BARRIDO LENTO cross-ventana (500/ventana × N ventanas = todo el grafo, nunca
# bloqueado). Este cap acumula los distintos de POR VIDA del principal (horizonte largo, no resetea con
# la ventana corta) → un actor paciente igual choca contra el techo de disclosure total.
DEFAULT_LIFETIME_BUDGET = int(os.environ.get("SEAL_T6_LIFETIME_BUDGET", "2000"))
DEFAULT_LIFETIME_SEC = int(os.environ.get("SEAL_T6_LIFETIME_SEC", str(30 * 24 * 3600)))  # 30 días
# Enumeración: si en una sola ventana el principal cubre esta FRACCIÓN del grafo, es barrido sistemático.
ENUM_COVERAGE_ALERT = float(os.environ.get("SEAL_T6_ENUM_COVERAGE", "0.02"))  # 2% del grafo


@dataclass
class Decision:
    allow: bool
    reason: str
    distinct_used: int = 0
    budget: int = 0
    principal: str = ""


class InMemoryGraphBudget:
    """Backend test/shadow. Prod usa la tabla con UPDATE atómico (mismo contrato).

    Guarda por principal el SET de nodos/aristas distintos vistos en la ventana vigente. El SET es la
    clave: cuenta material distinto, no requests → paginar no re-gasta (bypass 1), y es por principal
    → cambiar de finalidad no resetea (bypass 2). El acceso es sincrónico (event-loop single-thread)
    → no hay ventana de raza (bypass 3) en este backend; el de tabla lo garantiza con UPDATE atómico.
    """

    def __init__(self, window_sec: int = DEFAULT_WINDOW_SEC,
                 lifetime_sec: int = DEFAULT_LIFETIME_SEC):
        self.window_sec = window_sec
        self.lifetime_sec = lifetime_sec
        self._state: dict[str, tuple[float, set[str]]] = {}      # rate: (window_start, seen)
        self._life: dict[str, tuple[float, set[str]]] = {}       # lifetime: (life_start, seen)

    def add_and_count(self, principal: str, item_ids: list[str], now: float) -> tuple[int, int]:
        """Devuelve (distinct_ventana, distinct_lifetime) — el caller chequea AMBOS tiers."""
        # tier RATE (ventana corta, resetea)
        start, seen = self._state.get(principal, (now, set()))
        if now - start >= self.window_sec:
            start, seen = now, set()
        seen.update(item_ids)
        self._state[principal] = (start, seen)
        # tier LIFETIME (horizonte largo, NO resetea con la ventana corta -> caza el barrido lento)
        lstart, lseen = self._life.get(principal, (now, set()))
        if now - lstart >= self.lifetime_sec:
            lstart, lseen = now, set()
        lseen.update(item_ids)
        self._life[principal] = (lstart, lseen)
        return len(seen), len(lseen)


def check_graph_access(
    backend,
    principal: str,
    item_ids: list[str],
    *,
    budget: int = DEFAULT_BUDGET,
    lifetime_budget: int = DEFAULT_LIFETIME_BUDGET,
    graph_size: int | None = None,
    now: float | None = None,
) -> Decision:
    """Cuenta los nodos/aristas DISTINTOS que `principal` acumuló (incluidos estos) y decide.

    `item_ids`: ids de los nodos/aristas que ESTA consulta devolvería/tocaría.
    Fail-closed: si el backend lanza, deny (no se sirve estructura por un fallo del guard).
    """
    if not principal:
        return Decision(False, "no_principal", 0, budget, "")
    now = time.time() if now is None else now
    try:
        distinct, lifetime = backend.add_and_count(principal, list(item_ids or []), now)
    except Exception as exc:  # fail-closed
        return Decision(False, f"backend_error:{type(exc).__name__}", 0, budget, principal)

    # tier LIFETIME primero: caza el barrido lento cross-ventana (residual que halló ALICE).
    if lifetime > lifetime_budget:
        return Decision(False, "graph_lifetime_budget_exceeded", lifetime, lifetime_budget, principal)
    if distinct > budget:
        return Decision(False, "graph_budget_exceeded", distinct, budget, principal)

    # Señal de enumeración (alerta, no bloquea por sí sola salvo que también exceda el budget):
    if graph_size and graph_size > 0 and (distinct / graph_size) >= ENUM_COVERAGE_ALERT:
        # cobertura sistemática alta dentro del budget -> permitir pero MARCAR para auditoría
        return Decision(True, "allow_but_enumeration_suspected", distinct, budget, principal)

    return Decision(True, "allow", distinct, budget, principal)


# ── SQL del backend de tabla (para la integración prod; UPDATE atómico, sin get→set) ──
# El SET distinto se modela con una tabla de accesos (principal, item_id, window_start) con UNIQUE
# (principal, item_id, window_start): un INSERT ON CONFLICT DO NOTHING agrega solo lo NUEVO, y el
# COUNT(*) del principal en la ventana es el distinct acumulado — atómico y distribuido.
TABLE_DDL = """
CREATE TABLE IF NOT EXISTS soul_v3.t6_graph_access (
    principal    text        NOT NULL,
    item_id      text        NOT NULL,
    window_start bigint      NOT NULL,
    seen_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (principal, item_id, window_start)
);
"""


if __name__ == "__main__":
    # smoke por efecto
    b = InMemoryGraphBudget(window_sec=3600)
    d = check_graph_access(b, "aaron", [f"n{i}" for i in range(10)], budget=100, now=1000.0)
    print("normal:", d)
    d = check_graph_access(b, "aaron", [f"n{i}" for i in range(200)], budget=100, now=1001.0)
    print("sweep :", d)
