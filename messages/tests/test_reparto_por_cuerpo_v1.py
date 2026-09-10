#!/usr/bin/env python3
"""La sala de un cuerpo se entrega SOLO a ese cuerpo (ADA, 8-sep-2026).

William, 18:02, textual: «cada dm es privado eso lo sabes». Medido ese dia: el ACL ya impedia que
ADA Codex ESCRIBIERA en `user:1:ada-claude`, pero la ENTREGA usaba otra clave -- `agent_ws` se
indexa por NOMBRE de agente y los dos cuerpos hacen el handshake como "ADA", asi que sus mensajes
tambien viajaban al otro cuerpo. Se cerro un eje y quedo abierto el gemelo.

El caso que refuta el arreglo no es «llega al cuerpo correcto» (eso ya pasaba): es
**«NO llega al otro cuerpo»**, y ademas que el resto del reparto siga intacto.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

os.environ.setdefault("SEAL_PG_DSN", "postgresql://noop@127.0.0.1:1/noop")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import chat_server as cs  # noqa: E402


class WSFalso:
    """Una conexion de agente que solo guarda lo que le mandan."""

    def __init__(self, etiqueta: str):
        self.etiqueta = etiqueta
        self.recibidos: list[str] = []

    async def send_text(self, texto: str) -> None:
        self.recibidos.append(texto)


@pytest.fixture()
def sala(monkeypatch):
    """Dos cuerpos de ADA y un JARVIS conectados, como en produccion."""
    claude, codex, mudo, jarvis = (WSFalso("ada-claude"), WSFalso("ada-codex"),
                                   WSFalso("ada-sin-cuerpo"), WSFalso("jarvis"))
    monkeypatch.setattr(cs, "agent_ws", {"ADA": {claude, codex, mudo}, "JARVIS": {jarvis}})
    monkeypatch.setattr(cs, "agent_ws_body", {claude: "claude", codex: "codex"})
    monkeypatch.setattr(cs, "_msgdelivery", None)
    return claude, codex, mudo, jarvis


def _push(msg):
    asyncio.run(cs._push_to_agents(msg))


def test_la_sala_de_un_cuerpo_no_llega_al_otro_cuerpo(sala):
    claude, codex, mudo, jarvis = sala
    _push({"id": "db_1", "from": "William", "to": "ADA",
           "channel": "user:1:ada-claude", "message": "privado"})

    assert len(claude.recibidos) == 1, "el cuerpo dueño tiene que recibir"
    assert codex.recibidos == [], "EL OTRO CUERPO NO PUEDE RECIBIR la sala privada"
    assert jarvis.recibidos == [], "ningun otro agente"


def test_una_conexion_sin_cuerpo_declarado_queda_fuera(sala):
    """Falla CERRADO: quien no declara cuerpo no entra a una sala de cuerpo."""
    claude, codex, mudo, jarvis = sala
    _push({"id": "db_2", "from": "William", "to": "ADA",
           "channel": "user:1:ada-claude", "message": "privado"})
    assert mudo.recibidos == [], "sin cuerpo declarado no se recibe; el olvido cierra, no abre"


def test_el_resto_del_reparto_no_cambia(sala):
    """Control negativo: un canal normal sigue llegando a TODAS las conexiones del agente."""
    claude, codex, mudo, jarvis = sala
    _push({"id": "db_3", "from": "William", "to": "ADA",
           "channel": "web_chat", "message": "hola equipo"})
    assert len(claude.recibidos) == 1
    assert len(codex.recibidos) == 1, "web_chat no es una sala de cuerpo: no se filtra"
    assert len(mudo.recibidos) == 1
    assert jarvis.recibidos == []


def test_un_broadcast_a_equipo_sigue_llegando_a_todos(sala):
    claude, codex, mudo, jarvis = sala
    _push({"id": "db_4", "from": "William", "to": "equipo",
           "channel": "web_chat", "message": "buenos dias"})
    for ws in (claude, codex, mudo, jarvis):
        assert len(ws.recibidos) == 1, f"{ws.etiqueta} deberia recibir un broadcast"


def test_una_sala_SIN_cuerpo_llega_a_todas_las_conexiones_del_agente(sala):
    """`user:1:ada` (sin sufijo de cuerpo) no distingue cuerpos: sigue como antes."""
    claude, codex, mudo, jarvis = sala
    _push({"id": "db_5", "from": "William", "to": "ADA",
           "channel": "user:1:ada", "message": "sala sin cuerpo"})
    assert len(claude.recibidos) == 1 and len(codex.recibidos) == 1 and len(mudo.recibidos) == 1
    assert jarvis.recibidos == []


@pytest.mark.parametrize("canal,agente,cuerpo", [
    ("user:1:ada-claude", "ADA", "claude"),
    ("user:1:jarvis-codex", "JARVIS", "codex"),
    ("user:1:ada", "ADA", None),
    ("user:3:gtl-sistemas", None, None),   # proyecto, no agente
    ("web_chat", None, None),
    ("dm:ada:william", None, None),
])
def test_lectura_del_canal(canal, agente, cuerpo):
    assert cs._user_room_agent(canal) == agente
    assert cs._user_room_body(canal) == cuerpo
