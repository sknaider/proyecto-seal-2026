#!/usr/bin/env python3
"""Verifica que el fix del doble-lead esté CABLEADO en el path real.

JARVIS, 19-ago-2026. El parámetro `prior_lead` de choose_lead + get_last_turn_by_author
existían en soul_coordination pero `_stamp_coordination` en chat_server los tenía
DORMIDOS (llamaba choose_lead sin prior_lead). Este test prueba el cableado: que ante
un fragmento del MISMO autor, chat_server consulta el turno previo y se lo pasa a
choose_lead como prior_lead. En ENFORCE, si ambos paths de lectura fallan,
no se crea un turno nuevo ni se vuelve a sortear el lead.

Hermético: mockea `_council` y `chat_db`, no toca DB viva. Vive en messages/tests/
para heredar el SEAL_PG_DSN dummy del conftest.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

MESSAGES = Path(__file__).resolve().parents[1]
if str(MESSAGES) not in sys.path:
    sys.path.insert(0, str(MESSAGES))

import chat_server  # noqa: E402


class _FakeStore:
    def __init__(self, pool, ret):
        self._ret = ret

    async def get_last_turn_by_author(self, requester, within):
        _FakeStore.last_call = (requester, within)
        return self._ret


def _install_council(monkeypatch, *, prior_ret, capture):
    """Instala un _council falso que captura el prior_lead que recibe choose_lead."""
    def choose_lead(text, source_id, roster, *, mode, to, prior_lead=None):
        capture["prior_lead"] = prior_lead
        return "JARVIS"

    fake = types.SimpleNamespace(
        classify_mode=lambda text, to_field, msg_type: "execution",
        choose_lead=choose_lead,
        has_group_audience=lambda text: False,
        build_assignments=lambda *a, **k: [{"agent": "JARVIS", "role": "lead", "public_write": True}],
        SoulCoordinationStore=lambda pool: _FakeStore(pool, prior_ret),
        LEAD_STICKINESS_WINDOW_S=90,
        # El handler pasa `voz_inmediata_flag=_council._VOZ_INMEDIATA_PATH` desde el
        # 9-sep (NEXUS): el doble tiene que declararlo o el wiring revienta con
        # AttributeError antes de llegar a lo que este brazo mide. Se apunta a una
        # ruta INEXISTENTE a proposito: asi el doble no lee la bandera de produccion
        # y el test no depende del estado real del sistema.
        _VOZ_INMEDIATA_PATH="/nonexistent/.voz_inmediata",
    )
    monkeypatch.setattr(chat_server, "_council", fake)
    monkeypatch.setattr(chat_server, "_coordination_mode", lambda: "ENFORCE")


def _entry():
    return {
        "id": "e1", "idempotency_key": "frag-2", "from": "William",
        "to": "equipo", "channel": "web_chat", "type": "conversation",
        "message": "en resumen",
    }


def test_wiring_passes_prior_lead_from_last_turn(monkeypatch):
    """Con un turno previo del mismo autor, choose_lead recibe ese lead como prior_lead."""
    cap = {}
    _install_council(monkeypatch, prior_ret={"lead_agent": "ADA"}, capture=cap)

    class _Pool: pass
    monkeypatch.setattr(chat_server.chat_db, "pool", _Pool())

    asyncio.run(chat_server._stamp_coordination(_entry(), verified_human=True))
    assert cap["prior_lead"] == "ADA"  # <- el fix está cableado
    assert _FakeStore.last_call == ("William", 90)  # autor + ventana correctos


def test_wiring_none_when_no_prior_turn(monkeypatch):
    """Sin turno previo, prior_lead=None (cae al desempate por hash)."""
    cap = {}
    _install_council(monkeypatch, prior_ret=None, capture=cap)

    class _Pool: pass
    monkeypatch.setattr(chat_server.chat_db, "pool", _Pool())

    asyncio.run(chat_server._stamp_coordination(_entry(), verified_human=True))
    assert cap["prior_lead"] is None


def test_wiring_fail_closed_on_db_error_in_enforce(monkeypatch):
    """ENFORCE no re-sortea lead cuando falla toda lectura del turno previo."""
    cap = {}
    _install_council(monkeypatch, prior_ret=None, capture=cap)

    class _BoomStore:
        def __init__(self, pool): ...
        async def get_last_turn_by_author(self, requester, within):
            raise RuntimeError("db down")

    chat_server._council.SoulCoordinationStore = _BoomStore

    class _Pool: pass
    monkeypatch.setattr(chat_server.chat_db, "pool", _Pool())
    monkeypatch.setattr(chat_server.chat_db, "admin_pool", _Pool())

    result = asyncio.run(chat_server._stamp_coordination(_entry(), verified_human=True))
    assert "prior_lead" not in cap
    assert result["coordination"]["blocked"] is True
    assert result["coordination"]["error"] == "prior_lead_lookup_failed"
