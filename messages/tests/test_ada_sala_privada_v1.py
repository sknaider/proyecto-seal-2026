"""Sala privada ``user:<uid>:<agente>`` — sólo la persona y ese agente; el destinatario por defecto es el agente.

Carril `ada-sala-privada-user-room-20260908` (owner ADA, revisor JARVIS). Qué lo generó: William, 8-sep-2026 13:14,
«explicame esto: ¿por qué veo en tu DM a NEXUS y ALICE?». Medido: en ``user:1:ada-claude`` sus mensajes llegaban sin
``to`` y el servidor los defaulteaba a ``equipo`` (el coordinador repartía a los cinco), y la ACL dejaba escribir a
cualquier remitente pleno en cualquier canal.

Los canales de los tests son literales y son los reales (``user:1:ada-claude``): no son rutas del sistema de
archivos, no tocan ``/home`` ni ``/tmp``; usar la forma real da más valor que un señuelo.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

MESSAGES_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MESSAGES_DIR))
# ChatDB sólo valida que exista un DSN al importar; estos tests nunca conectan.
os.environ.setdefault("SEAL_PG_DSN", "postgresql://test:test@127.0.0.1/test")

import channel_acl  # noqa: E402
import chat_server  # noqa: E402

SALA = "user:1:ada-claude"


# ── unit: parseo de la sala ─────────────────────────────────────────────────────

def test_unit_el_agente_de_la_sala_sale_del_nombre():
    assert chat_server._user_room_agent("user:1:ada-claude") == "ADA"
    assert chat_server._user_room_agent("user:2:jarvis") == "JARVIS"
    assert chat_server._user_room_agent("user:7:alice-v2") == "ALICE"
    assert channel_acl.agente_de_canal_usuario("user:1:ada-claude") == "ADA"


def test_unit_lo_que_no_es_sala_no_se_parsea():
    for ch in ("web_chat", "dm:ada:william", "shadow:ada", "user:x:ada", "user:1", "user:1:", "users:1:ada", ""):
        assert chat_server._user_room_agent(ch) is None, ch
        assert channel_acl.agente_de_canal_usuario(ch) is None, ch


# ── positivo: la persona y el agente (todos sus cuerpos) escriben ───────────────

def test_positivo_solo_el_cuerpo_Claude_escribe_en_su_sala_exclusiva():
    assert channel_acl.cuerpo_de_canal_usuario(SALA) == "ADA_CLAUDE"
    assert channel_acl.puede_escribir("ADA", SALA, instance_id="ADA_CLAUDE") is True


def test_positivo_una_sala_sin_cuerpo_admite_a_todos_los_cuerpos_del_agente():
    sala = "user:1:ada"
    assert channel_acl.cuerpo_de_canal_usuario(sala) is None
    assert channel_acl.puede_escribir("ADA", sala) is True
    assert channel_acl.puede_escribir("ADA", sala, instance_id="ADA_CLAUDE") is True
    assert channel_acl.puede_escribir("ADA", sala, instance_id="ADA-CODEX") is True


def test_positivo_William_puede_escribir_en_la_sala():
    assert channel_acl.puede_escribir("William", SALA) is True
    assert channel_acl.puede_escribir("WILLIAM", SALA) is True


# ── negativo: otro agente de casa -> prohibido (403 en el servidor) ─────────────

def test_negativo_NEXUS_no_puede_escribir_en_la_sala_de_ADA():
    assert channel_acl.puede_escribir("NEXUS", SALA) is False
    assert "user:1:ada-claude" in channel_acl.motivo_del_rechazo("NEXUS", SALA)


def test_negativo_ALICE_ni_su_cuerpo_v2_ni_un_desconocido():
    assert channel_acl.puede_escribir("ALICE", SALA) is False
    assert channel_acl.puede_escribir("ALICE", SALA, instance_id="ALICE-V2") is False
    assert channel_acl.puede_escribir("ALGUIEN", SALA) is False


def test_negativo_el_prefijo_no_alcanza_ADAMANTIO_no_es_ADA():
    assert channel_acl.puede_escribir("ADAMANTIO", SALA) is False


def test_negativo_el_cuerpo_Codex_no_escribe_en_la_sala_exclusiva_de_Claude():
    # El puente Codex escribe como «ADA» sin declarar cuerpo, o declarando el suyo: ninguno entra.
    assert channel_acl.puede_escribir("ADA", SALA) is False
    assert channel_acl.puede_escribir("ADA", SALA, instance_id="ADA_CODEX_BRIDGE") is False
    assert channel_acl.puede_escribir("ADA", SALA, instance_id="ADA-CODEX") is False


# ── control: el destinatario por defecto y lo que NO cambia ─────────────────────

def test_control_el_default_to_en_la_sala_es_el_agente_no_equipo():
    assert chat_server._default_to(SALA, "William", "") == "ADA"
    assert chat_server._default_to(SALA, "William") == "ADA"


def test_control_si_el_agente_escribe_en_su_sala_el_default_no_es_el_mismo():
    # ADA hablando en su propia sala no se manda a sí misma: conserva el comportamiento anterior.
    assert chat_server._default_to(SALA, "ADA", "") == "equipo"
    assert chat_server._default_to(SALA, "ADA_CLAUDE", "") == "equipo"


def test_control_un_to_explicito_siempre_gana():
    assert chat_server._default_to(SALA, "William", "JARVIS") == "JARVIS"
    assert chat_server._default_to("web_chat", "William", "ADA") == "ADA"


def test_control_una_sala_de_proyecto_no_es_una_sala_de_agente():
    # user:3:gtl-sistemas es la sala del proyecto GTL de Henry (canal real, matriz de NEXUS): no hay agente «GTL».
    assert chat_server._user_room_agent("user:3:gtl-sistemas") is None
    assert channel_acl.agente_de_canal_usuario("user:3:gtl-sistemas") is None
    assert chat_server._default_to("user:3:gtl-sistemas", "henry", "") == "equipo"
    for quien in ("NEXUS", "ADA", "ALICE", "JARVIS", "William", "henry"):
        assert channel_acl.puede_escribir(quien, "user:3:gtl-sistemas") is True, quien


def test_control_fuera_de_las_salas_nada_cambia():
    assert chat_server._default_to("web_chat", "William", "") == "equipo"
    assert chat_server._default_to("dm:ada:william", "William", "") == "equipo"
    assert channel_acl.puede_escribir("NEXUS", "web_chat") is True
    assert channel_acl.puede_escribir("ADA", "web_chat") is True
    assert channel_acl.puede_escribir("ALGUIEN", "web_chat") is False
    assert channel_acl.puede_escribir("ALGUIEN", "shadow:alguien") is True


# ── entrega: la sala privada NO se escribe en el canal compartido que tail-ean los monitores ────

def test_negativo_la_sala_privada_no_va_a_william_channel(monkeypatch):
    """NEXUS midió (13:37) que seguía recibiendo la sala por `william_channel.jsonl`, que tail-ea su monitor."""
    chat_server.DIR = MESSAGES_DIR
    chat_server._ROUTING_CONFIG = {}
    chat_server._ROUTING_CHANNEL_PATHS = {}
    chat_server._load_routing_config()
    normales = chat_server._log_paths_for("William", "ADA", "web_chat")
    privados = chat_server._log_paths_for("William", "ADA", SALA)
    assert chat_server.LOG_WILLIAM in normales, "control: fuera de la sala, william_channel sigue recibiendo"
    assert chat_server.LOG_WILLIAM not in privados
    assert privados and all(p != chat_server.LOG_WILLIAM for p in privados)
    assert [p for p in normales if p != chat_server.LOG_WILLIAM] == privados, "sólo se quita william_channel"

