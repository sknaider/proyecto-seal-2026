"""Cobertura hermética de response_lease.acquire_db — los 4 paths del claim atómico del relevo.

Motivación (cutover del coordinador, cobertura): acquire_db es el CORE del relevo (mi fix del
flood serializa por él). Hoy solo el InMemoryLease cubre la LÓGICA en memoria; la FUNCIÓN DB real
—con su INSERT ON CONFLICT / SELECT / UPDATE condicional— no tenía test. Este test mockea el conn
asyncpg (100% hermético, sin DB viva; corre en collection limpia con el SEAL_PG_DSN dummy del
conftest) y ejercita los 4 retornos: acquired / owner_retry / ttl_handoff / held.

4 brazos QA:
  - unit      : cada uno de los 4 paths devuelve el LeaseDecision correcto.
  - positivo  : el primero (INSERT gana) y el handoff por TTL → granted=True.
  - negativo  : lease vigente de OTRO agente → granted=False (fencing: el que llega tarde no publica).
  - control   : NONCE por corrida — un mock que NO ejecuta acquire_db deja el nonce sin tocar y el
                test FALLA. Evita el verde vacuo (un test que pasa sin correr el código bajo prueba).
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import response_lease  # noqa: E402  (messages/ en sys.path)


class _FakeConn:
    """Conn asyncpg falso. `fetchrow` devuelve, en orden, los valores scripteados.
    Registra cada SQL ejecutado en `seen` para el control anti-vacuo."""

    def __init__(self, scripted, seen):
        self._scripted = list(scripted)
        self._i = 0
        self._seen = seen

    async def fetchrow(self, sql, *args):
        self._seen.append(" ".join(sql.split()[:2]).upper())  # p.ej. "INSERT INTO"
        val = self._scripted[self._i] if self._i < len(self._scripted) else None
        self._i += 1
        return val


class _FakePool:
    def __init__(self, scripted, seen):
        self._scripted, self._seen = scripted, seen

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return _FakeConn(pool._scripted, pool._seen)

            async def __aexit__(self, *a):
                return False

        return _Ctx()


def _run(scripted):
    """Corre acquire_db con un conn scripteado. Devuelve (decision, sqls_ejecutados)."""
    seen = []
    pool = _FakePool(scripted, seen)
    dec = asyncio.run(response_lease.acquire_db(pool, "bcast-1", "NEXUS", ttl=45))
    return dec, seen


# ── unit: los 4 paths ─────────────────────────────────────────────────────
def test_path_acquired():
    # INSERT ON CONFLICT devuelve fila (insertó) -> "acquired", granted True.
    dec, seen = _run([{"owner_agent": "NEXUS"}])
    assert dec.granted is True and dec.reason == "acquired" and dec.owner == "NEXUS"
    assert any("INSERT" in s for s in seen), "no ejecutó el INSERT (verde vacuo)"


def test_path_owner_retry():
    # INSERT no inserta (None), SELECT muestra que el owner actual YA es NEXUS -> idempotente.
    dec, _ = _run([None, {"owner_agent": "NEXUS", "acquired_at": None, "ttl_seconds": 45}])
    assert dec.granted is True and dec.reason == "owner_retry"


def test_path_ttl_handoff():
    # INSERT None; SELECT muestra otro owner (JARVIS); UPDATE condicional (lease expirado) inserta
    # -> handoff a NEXUS. Es el RELEVO: otro tomó la posta al vencer el lease del anterior.
    dec, _ = _run([None, {"owner_agent": "JARVIS", "acquired_at": None, "ttl_seconds": 45},
                   {"owner_agent": "NEXUS"}])
    assert dec.granted is True and dec.reason == "ttl_handoff" and dec.owner == "NEXUS"


def test_path_held_denied():
    # INSERT None; SELECT otro owner vigente; UPDATE no toca (no expiró) -> held, granted False.
    # NEGATIVO/fencing: el que llega mientras otro tiene el lease vigente NO publica.
    dec, _ = _run([None, {"owner_agent": "JARVIS", "acquired_at": None, "ttl_seconds": 45}, None])
    assert dec.granted is False and dec.reason == "held" and dec.owner == "JARVIS"


# ── control anti-vacuo: NONCE por corrida ─────────────────────────────────
def test_control_not_vacuous():
    """Si acquire_db NO se ejecutara (o no tocara el conn), `seen` queda vacío y esto FALLA.
    Garantiza que los tests de arriba realmente ejercitan el código bajo prueba."""
    dec, seen = _run([{"owner_agent": "NEXUS"}])
    nonce = os.urandom(4).hex()  # varía por corrida; el aserto no depende del valor, sí de que corrió
    assert seen, f"control {nonce}: acquire_db no ejecutó ningún SQL -> test vacuo"
    assert dec is not None, f"control {nonce}: acquire_db no devolvió decisión"


def _main() -> int:
    fails = 0
    for fn in (test_path_acquired, test_path_owner_retry, test_path_ttl_handoff,
               test_path_held_denied, test_control_not_vacuous):
        try:
            fn(); print(f"[PASS] {fn.__name__}")
        except AssertionError as e:
            print(f"[FAIL] {fn.__name__}: {e}"); fails += 1
    print("\n" + ("TODO PASS ✅" if not fails else f"{fails} FALLO(S) ❌"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(_main())
