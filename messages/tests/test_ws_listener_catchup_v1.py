#!/usr/bin/env python3
"""Recuperacion de lo perdido en el listener del Monitor (ADA, 8-sep-2026).

Que cubre y POR QUE: la sala privada `user:<uid>:<agente>-<cuerpo>` no tiene outbox durable
(`needs_guarantee` cubre solo `dm:`), no se escribe al feed en disco (excluida a proposito por
privacidad) y el catch-up de arranque de agentes filtra `web_chat%`. Su UNICA entrega era el WS
en vivo: con el listener caido, el mensaje de William se perdia para siempre. El catch-up por
`/api/agents/poll` con cursor persistido es esa red.

Los cuatro casos son los que REFUTARIAN el arreglo, no los que lo confirman:
  1. arranque en frio que vuelca historico (rafaga al relanzar)   -> debe NO emitir
  2. tope que corta por el PRINCIPIO y pierde lo mas reciente     -> defecto real que tuvo el
                                                                     codigo; se entregan los ultimos
  3. re-emision de lo ya visto en cada sondeo                     -> dedupe por id
  4. eco de mis propios mensajes                                  -> no se emiten
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib

import pytest

AQUI = pathlib.Path(__file__).resolve().parent
LISTENER = AQUI.parent / "ws_listener.py"


@pytest.fixture()
def wsl(tmp_path, monkeypatch):
    """El listener cargado por ruta, con el cursor aislado del Monitor vivo."""
    spec = importlib.util.spec_from_file_location("ws_listener_bajo_prueba", LISTENER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_cursor_path",
                        lambda agente: str(tmp_path / f".ws_cursor_{agente.upper()}"))
    mod._seen_ids.clear()
    return mod


def _falso_poll(mod, monkeypatch, mensajes, cursor):
    monkeypatch.setattr(mod, "_poll_sync",
                        lambda agente, since: {"messages": mensajes, "cursor": cursor})


def _emitidos(capsys):
    salida = []
    for linea in capsys.readouterr().out.splitlines():
        try:
            salida.append(json.loads(linea))
        except ValueError:
            pass
    return salida


def _msg(i, canal="user:1:ada-claude", quien="William"):
    return {"id": f"db_{i}", "from": quien, "to": "ADA", "channel": canal, "message": f"m{i}"}


def test_arranque_en_frio_no_vuelca_historico(wsl, monkeypatch, capsys):
    """Sin archivo de cursor NO se emite nada: solo se fija el cursor al dia."""
    _falso_poll(wsl, monkeypatch, [_msg(i) for i in range(5)], cursor=500)
    asyncio.run(wsl.catchup_once("ADA"))
    assert _emitidos(capsys) == []
    assert wsl._load_cursor("ADA") == 500


def test_el_tope_entrega_los_mensajes_mas_RECIENTES(wsl, monkeypatch, capsys):
    """El defecto que tuvo el codigo: cortar por el principio deja fuera lo ultimo de William."""
    wsl._save_cursor("ADA", 1)
    total = wsl.CATCHUP_MAX_EMIT + 10
    _falso_poll(wsl, monkeypatch, [_msg(i) for i in range(total)], cursor=999)
    asyncio.run(wsl.catchup_once("ADA"))
    salida = _emitidos(capsys)
    ids = [m["id"] for m in salida if m.get("id")]
    aviso = [m for m in salida if m.get("catchup_truncado")]

    assert len(ids) == wsl.CATCHUP_MAX_EMIT
    assert ids[-1] == f"db_{total - 1}", "el mensaje MAS RECIENTE tiene que llegar"
    assert f"db_0" not in ids, "los viejos son los que se omiten, no los nuevos"
    assert aviso and aviso[0]["catchup_truncado"] == 10, "hay que avisar cuantos se omitieron"


def test_un_segundo_sondeo_no_repite_lo_ya_entregado(wsl, monkeypatch, capsys):
    wsl._save_cursor("ADA", 1)
    mensajes = [_msg(i) for i in range(3)]
    _falso_poll(wsl, monkeypatch, mensajes, cursor=10)
    asyncio.run(wsl.catchup_once("ADA"))
    primeros = [m["id"] for m in _emitidos(capsys) if m.get("id")]
    assert len(primeros) == 3

    wsl._save_cursor("ADA", 1)  # el servidor vuelve a ofrecer los mismos
    asyncio.run(wsl.catchup_once("ADA"))
    assert [m["id"] for m in _emitidos(capsys) if m.get("id")] == []


def test_el_mismo_mensaje_por_el_WS_no_se_emite_dos_veces(wsl, capsys):
    """Hueco que destapo la mutacion: `catchup_once` filtra por `_seen_ids` ANTES de emitir, asi
    que sobrevivia con `_mark_seen` roto. El camino del WS llama a `_emit` directo y NO tiene ese
    filtro previo: ahi el dedupe es `_mark_seen` o no hay ninguno. Un mensaje que llega por el
    socket y ademas aparece en el sondeo se entregaria dos veces a William."""
    m = _msg(42)
    assert wsl._emit(m, "ADA", via="ws") is True
    assert wsl._emit(m, "ADA", via="catchup") is False, "el mismo id no se entrega dos veces"
    assert [x["id"] for x in _emitidos(capsys)] == ["db_42"]


def test_no_emite_mis_propios_mensajes_pero_si_los_ajenos(wsl, capsys):
    propio = {"id": "eco_1", "from": "ADA", "to": "William", "message": "eco"}
    ajeno = {"id": "suyo_1", "from": "William", "to": "ADA", "message": "hola"}
    assert wsl._emit(propio, "ADA", via="catchup") is False
    assert wsl._emit(ajeno, "ADA", via="catchup") is True
    salida = _emitidos(capsys)
    assert [m["id"] for m in salida] == ["suyo_1"]
    assert salida[0]["recovered_via"] == "catchup", "la procedencia se marca al recuperar"


def test_si_el_servidor_reinicia_su_cola_el_cursor_viejo_no_deja_ciego_al_listener(wsl, monkeypatch, capsys):
    """Hueco medido el 8-sep tras reiniciar seal-chat.

    El cursor de la cola es un contador EN MEMORIA del servidor: al reiniciar vuelve a cero. Con un
    cursor guardado alto (403) y la cola nueva en 5, el sondeo pedia `since=403`, el servidor no
    devolvia nada y los mensajes de William posteriores al reinicio quedaban invisibles -- justo en
    el escenario que esta red existe para cubrir, porque un reinicio del servidor ES una
    desconexion del listener."""
    wsl._save_cursor("ADA", 403)
    tras_reinicio = [_msg(900)]

    def poll(agente, since):
        return {"messages": [] if since > 5 else tras_reinicio, "cursor": 5}

    monkeypatch.setattr(wsl, "_poll_sync", poll)
    asyncio.run(wsl.catchup_once("ADA"))
    salida = _emitidos(capsys)

    assert [m["id"] for m in salida if m.get("id")] == ["db_900"], \
        "tras un reinicio del servidor los mensajes nuevos TIENEN que recuperarse"
    assert any(m.get("cursor_reiniciado") for m in salida), "el retroceso de cursor se declara"
    assert wsl._load_cursor("ADA") == 5, "el cursor se realinea con el del servidor"


def test_un_sondeo_que_falla_no_tumba_el_listener(wsl, monkeypatch, capsys):
    """Control: la red de recuperacion nunca puede dejar sordo al agente."""
    def explota(agente, since):
        raise OSError("conexion rechazada")

    wsl._save_cursor("ADA", 7)
    monkeypatch.setattr(wsl, "_poll_sync", explota)
    asyncio.run(wsl.catchup_once("ADA"))  # no debe propagar
    salida = _emitidos(capsys)
    assert salida and salida[0]["error"] == "catchup_failed"
    assert wsl._load_cursor("ADA") == 7, "un fallo no puede mover el cursor"
