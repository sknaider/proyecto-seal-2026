"""Cobertura hermética de la decisión de autoridad del claim en chat_server.py.

Subject del manifest = chat_server.py. Cubre `_coordination_claim_decision` (el corazón de la
autoridad del coordinador: quién puede escribir en público según la asignación del council) y el
first-wins de `_response_claims`. 100% hermético: importa chat_server con el SEAL_PG_DSN dummy del
conftest y mockea el council store + el pool; NO abre DB ni HTTP.

Los 5 paths de `_coordination_claim_decision`:
  1. mode != ENFORCE                      -> None (cae a first-wins)
  2. ENFORCE, sin council/pool            -> error dict (coordination_unavailable, fail-closed)
  3. ENFORCE + turn asigna public_write   -> granted True, holder = agente
  4. ENFORCE + turn NO public_write        -> granted False, holder = lead (assigned_other)
  5. ENFORCE + council + sin turn          -> None (mensaje sin asignación -> first-wins)

4 brazos QA: unit (los 5 paths) / positivo (public_writer -> granted) / negativo (no-writer ->
denied, y sin-council -> fail-closed) / control anti-vacuo (un mode inexistente NO da granted).
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("SEAL_PG_DSN", "postgresql://test:test@127.0.0.1:1/test")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import chat_server  # noqa: E402


class _FakeStore:
    """SoulCoordinationStore(pool) falso: get_turn devuelve el turn scripteado."""
    def __init__(self, turn):
        self._turn = turn

    async def get_turn(self, source_id):
        return self._turn


def _fake_council(turn):
    class _C:
        SoulCoordinationStore = staticmethod(lambda pool: _FakeStore(turn))
    return _C()


def _decide(monkey, *, mode, council_turn, has_pool=True, council_present=True):
    """Corre _coordination_claim_decision con el entorno mockeado."""
    monkey.setattr(chat_server, "_coordination_mode", lambda: mode)
    monkey.setattr(chat_server, "_council", _fake_council(council_turn) if council_present else None)
    # chat_db.pool: truthy sentinel o None
    monkey.setattr(chat_server.chat_db, "pool", object() if has_pool else None, raising=False)
    return asyncio.run(chat_server._coordination_claim_decision("api_william_x", "NEXUS"))


def test_path1_not_enforce_returns_none(monkeypatch):
    assert _decide(monkeypatch, mode="OFF", council_turn=None) is None
    assert _decide(monkeypatch, mode="SHADOW", council_turn=None) is None


def test_path2_enforce_no_council_fails_closed(monkeypatch):
    d = _decide(monkeypatch, mode="ENFORCE", council_turn=None, council_present=False)
    assert d is not None and d.get("error") == "coordination_unavailable"
    d2 = _decide(monkeypatch, mode="ENFORCE", council_turn=None, has_pool=False)
    assert d2 is not None and d2.get("error") == "coordination_unavailable"


def test_path3_assigned_public_writer_granted(monkeypatch):
    turn = {"assignments": [{"agent": "NEXUS", "public_write": True}], "lead_agent": "NEXUS"}
    d = _decide(monkeypatch, mode="ENFORCE", council_turn=turn)
    assert d and d.get("granted") is True and d.get("holder") == "NEXUS"
    assert d.get("reason") == "assigned_public_writer" and d.get("source") == "coordinator"


def test_path4_assigned_other_denied(monkeypatch):
    # NEXUS pide pero el public_writer es ALICE (lead) -> granted False, holder = lead.
    turn = {"assignments": [{"agent": "ALICE", "public_write": True}], "lead_agent": "ALICE"}
    d = _decide(monkeypatch, mode="ENFORCE", council_turn=turn)
    assert d and d.get("granted") is False and d.get("holder") == "ALICE"
    assert d.get("reason") == "coordinator_assigned_other"


def test_path5_enforce_no_turn_returns_none(monkeypatch):
    # ENFORCE + council pero el mensaje no tiene turn asignado -> None -> first-wins.
    assert _decide(monkeypatch, mode="ENFORCE", council_turn=None) is None


def test_first_wins_response_claims(monkeypatch):
    """First-wins directo sobre _response_claims: el primero gana el message_id, el segundo NO
    (salvo que sea el mismo agente). Es el turno cortés para mensajes SIN asignación."""
    claims = chat_server._response_claims
    mid = "mid-hermetic-test-xyz"
    claims.pop(mid, None)
    import time
    # primero reclama
    claims[mid] = {"agent": "NEXUS", "ts": time.time()}
    holder = claims.get(mid)
    assert holder and holder["agent"] == "NEXUS"           # primero gana
    assert (holder["agent"] == "ALICE") is False           # otro agente NO es holder
    assert (holder["agent"] == "NEXUS") is True            # idempotente para el holder
    claims.pop(mid, None)


def test_control_not_vacuous(monkeypatch):
    """Control anti-vacuo: un mode que NO es ENFORCE jamás debe dar granted=True.
    Si el mock no estuviera aplicando, este invariante podría romperse."""
    nonce = os.urandom(3).hex()
    d = _decide(monkeypatch, mode="DISABLED_" + nonce, council_turn=None)
    assert d is None, f"control {nonce}: un mode no-ENFORCE dio decisión no-None: {d}"


def _main() -> int:
    import types
    class _MP:  # monkeypatch mínimo para correr como script
        def __init__(self): self._undo = []
        def setattr(self, obj, name, val, raising=True):
            old = getattr(obj, name, None); self._undo.append((obj, name, old))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, old in reversed(self._undo):
                try: setattr(obj, name, old)
                except Exception: pass
    fails = 0
    for fn in (test_path1_not_enforce_returns_none, test_path2_enforce_no_council_fails_closed,
               test_path3_assigned_public_writer_granted, test_path4_assigned_other_denied,
               test_path5_enforce_no_turn_returns_none, test_first_wins_response_claims,
               test_control_not_vacuous):
        mp = _MP()
        try:
            fn(mp); print(f"[PASS] {fn.__name__}")
        except AssertionError as e:
            print(f"[FAIL] {fn.__name__}: {e}"); fails += 1
        finally:
            mp.undo()
    print("\n" + ("TODO PASS ✅" if not fails else f"{fails} FALLO(S) ❌"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(_main())
