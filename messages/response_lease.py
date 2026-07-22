#!/usr/bin/env python3
"""
response_lease.py — Lease atómico de respuesta para single-voice (P1-10).
Auditoría v2.0. ADITIVO/NO-DESTRUCTIVO: módulo standalone, NO cableado a chat_server
hasta el deploy (que requiere OK explícito de William). Testeable en shadow sin DDL.

Uso en prod (al deployar, con OK): chat_server importa acquire() y lo llama antes de
aceptar un POST que sea respuesta a un broadcast de William (in_reply_to=broadcast_id).
Backend prod = tabla soul_v3.response_lease (ON CONFLICT DO NOTHING = claim atómico).
Backend test = in-memory (este archivo), sin tocar la DB.

Diseño: docs/SPEC_singlevoice_lease_JARVIS.md
"""
import re
import time
from dataclasses import dataclass

# Detector de broadcasts multi-respuesta legítimos (William quiere N voces) → skip lease.
_MULTI = re.compile(
    r'\btodos\b|\bequipo\b|cada uno|todos agentes|todos ustedes|revisen todos|trabajen todos',
    re.IGNORECASE,
)

DEFAULT_TTL = 45  # segundos; owner debe postear dentro de este plazo o se hace handoff


@dataclass(frozen=True)
class LeaseDecision:
    """Resultado auditable del gate productivo."""

    granted: bool
    owner: str
    reason: str


async def acquire_db(pool, broadcast_id: str, agent: str, ttl: int = DEFAULT_TTL) -> LeaseDecision:
    """Adquiere el lease en PostgreSQL sin ventana de carrera.

    La migracion crea ``soul_v3.response_lease``. El INSERT decide el primer
    owner; un UPDATE condicional permite handoff solo cuando el lease expiro.
    No crea tablas durante el arranque del daemon.
    """
    broadcast_id = str(broadcast_id or "").strip()
    agent = str(agent or "").strip().upper()
    ttl = max(5, min(int(ttl), 300))
    if not broadcast_id or not agent:
        raise ValueError("broadcast_id y agent son requeridos")

    async with pool.acquire() as conn:
        inserted = await conn.fetchrow(
            """
            INSERT INTO soul_v3.response_lease
                (broadcast_id, owner_agent, ttl_seconds, state)
            VALUES ($1, $2, $3, 'held')
            ON CONFLICT (broadcast_id) DO NOTHING
            RETURNING owner_agent
            """,
            broadcast_id, agent, ttl,
        )
        if inserted:
            return LeaseDecision(True, agent, "acquired")

        current = await conn.fetchrow(
            """
            SELECT owner_agent, acquired_at, ttl_seconds
            FROM soul_v3.response_lease
            WHERE broadcast_id = $1
            """,
            broadcast_id,
        )
        if current and str(current["owner_agent"]).upper() == agent:
            return LeaseDecision(True, agent, "owner_retry")

        handed = await conn.fetchrow(
            """
            UPDATE soul_v3.response_lease
               SET owner_agent = $2,
                   acquired_at = now(),
                   ttl_seconds = $3,
                   state = 'handed_off'
             WHERE broadcast_id = $1
               AND acquired_at + make_interval(secs => ttl_seconds) < now()
            RETURNING owner_agent
            """,
            broadcast_id, agent, ttl,
        )
        if handed:
            return LeaseDecision(True, agent, "ttl_handoff")
        owner = str(current["owner_agent"]) if current else "unknown"
        return LeaseDecision(False, owner, "held")


def is_multi_response(text: str) -> bool:
    """True si William pidió explícitamente múltiples voces (no aplicar lease)."""
    return bool(_MULTI.search(text or ""))


class InMemoryLease:
    """Backend de lease para test/shadow. Prod usa la tabla postgres con ON CONFLICT."""

    def __init__(self):
        self._held = {}  # broadcast_id -> (owner, acquired_at, ttl)

    def acquire(self, broadcast_id: str, agent: str, ttl: int = DEFAULT_TTL) -> bool:
        """Devuelve True si `agent` GANA el lease (o ya es owner); False si otro lo tiene vigente."""
        now = time.time()
        cur = self._held.get(broadcast_id)
        if cur is None:
            self._held[broadcast_id] = (agent, now, ttl)
            return True
        owner, at, t = cur
        if owner == agent:
            return True  # idempotente: el owner puede re-postear
        if now - at > t:  # lease expirado → handoff: el nuevo lo toma
            self._held[broadcast_id] = (agent, now, ttl)
            return True
        return False  # otro agente lo tiene vigente → rechazar (409)


def gate_allows(lease, broadcast_id: str, agent: str, william_text: str) -> bool:
    """
    Punto de decisión que chat_server llamaría (al deployar).
    - Broadcast multi-respuesta → siempre permite (no lease).
    - Si no → solo el ganador del lease atómico posta.
    FAIL-OPEN: si el backend falla, chat_server debe permitir (no silenciar a William).
    """
    if is_multi_response(william_text):
        return True
    return lease.acquire(broadcast_id, agent)


# ---- self-test (shadow, sin DB) ----
def _selftest():
    ok = True

    # 1) primer respondedor gana, segundo pierde (dedup pile-on)
    L = InMemoryLease()
    a = gate_allows(L, "b1", "JARVIS", "arregla esto")
    b = gate_allows(L, "b1", "NEXUS", "arregla esto")
    ok &= (a is True and b is False)
    print(f"  [1] dedup pile-on: primero={a} segundo={b} -> {'OK' if a and not b else 'FAIL'}")

    # 2) idempotencia: el owner puede re-postear
    c = gate_allows(L, "b1", "JARVIS", "arregla esto")
    ok &= (c is True)
    print(f"  [2] idempotencia owner: {c} -> {'OK' if c else 'FAIL'}")

    # 3) multi-response ("todos") saltea el lease -> todos pasan
    L2 = InMemoryLease()
    r = [gate_allows(L2, "b2", ag, "todos revisen") for ag in ("JARVIS", "ADA", "ALICE")]
    ok &= all(r)
    print(f"  [3] multi-response 'todos': {r} -> {'OK' if all(r) else 'FAIL'}")

    # 4) handoff por TTL expirado
    L3 = InMemoryLease()
    gate_allows(L3, "b3", "JARVIS", "hazlo")           # JARVIS toma
    L3._held["b3"] = ("JARVIS", time.time() - 100, 45)  # simular expiración
    d = gate_allows(L3, "b3", "ADA", "hazlo")           # ADA toma por handoff
    ok &= (d is True)
    print(f"  [4] handoff TTL expirado: {d} -> {'OK' if d else 'FAIL'}")

    # 5) 30 contendientes concurrentes -> exactamente 1 gana (replica el claim 30->1)
    L4 = InMemoryLease()
    wins = sum(1 for i in range(30) if gate_allows(L4, "b4", f"agent{i}", "responde"))
    ok &= (wins == 1)
    print(f"  [5] 30 contendientes -> ganadores={wins} -> {'OK' if wins == 1 else 'FAIL'}")

    print(f"\nSELF-TEST response_lease: {'✅ VERDE (5/5)' if ok else '❌ FALLO'}")
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
