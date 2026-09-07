"""El canal manda sobre el campo `to`: un DM no llega a terceros.

POR QUE ESTE ARCHIVO EXISTE Y NO ALCANZABA EL TEST QUE YA TENIA (NEXUS, 3-sep-2026)

`agents/NEXUS/test_dm_no_se_filtra.py` mide sobre TRAFICO REAL del canal y por eso
es el que caza la regresion viva -- pero depende de que ese trafico contenga DMs, y
un dia sin DMs de William lo deja sin sujetos. Este archivo fija las mismas
propiedades sobre casos construidos, que estan siempre disponibles.

Los dos se necesitan: el de trafico real prueba que el sistema HOY no filtra; este
prueba que la REGLA sigue escrita. Ninguno reemplaza al otro.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
FILTRO = RAIZ / "messages" / "seal_monitor_filter.py"


@pytest.fixture(scope="module")
def filtro():
    spec = importlib.util.spec_from_file_location("filtro_bajo_prueba", FILTRO)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.MAX_AGE_SECONDS = 10 ** 9
    m._claim_turn = lambda *a, **k: None
    return m


def evento(**kw) -> str:
    base = {
        "id": "t1",
        "from": "William",
        "to": "ALICE",
        "type": "conversation",
        "message": "un mensaje suficientemente largo como para no caer en el filtro de ACKs cortos",
        "channel": "dm:alice:william",
    }
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


# --- unit -----------------------------------------------------------------

def test_unit_un_canal_dm_nombra_a_sus_dos_participantes(filtro):
    """La propiedad de la que cuelga todo: el nombre del canal ES el ruteo."""
    assert filtro.filter_line(evento(), "ALICE", {}) is not None
    assert filtro.filter_line(evento(), "NEXUS", {}) is None


# --- qa_positive: la capacidad EJERCIDA, no inferida -----------------------

def test_qa_positive_el_destinatario_legitimo_recibe_su_dm(filtro):
    """Sin esto, un filtro que descarte TODO pasaria los tests negativos."""
    for agente, canal in (("ALICE", "dm:alice:william"), ("NEXUS", "dm:nexus:william")):
        assert filtro.filter_line(evento(to=agente, channel=canal), agente, {}) is not None


def test_qa_positive_el_canal_general_no_queda_afectado(filtro):
    """El fix no debe cerrar lo que no es correspondencia privada."""
    linea = evento(to="equipo", channel="web_chat")
    assert filtro.filter_line(linea, "NEXUS", {}) is not None


# --- qa_negative: los tres agujeros medidos el 3-sep ----------------------

@pytest.mark.parametrize("tercero", ["NEXUS", "JARVIS", "FABLE", "ADA"])
def test_qa_negative_un_dm_de_william_no_llega_a_terceros(filtro, tercero):
    """EL AGUJERO QUE DOLIA: 'mensajes de William: siempre pasar' estaba ANTES
    del chequeo de destinatario, asi que los DMs mas privados del sistema eran
    los UNICOS que ninguna regla filtraba."""
    assert filtro.filter_line(evento(), tercero, {}) is None


@pytest.mark.parametrize("to_falso", ["equipo", "EQUIPO", "William", ""])
def test_qa_negative_el_campo_to_no_puede_abrir_un_canal_dm(filtro, to_falso):
    """`to` lo llena el EMISOR; `channel` es ruteo. Un cliente nuevo que ponga
    mal ese campo no debe abrir la correspondencia."""
    assert filtro.filter_line(evento(to=to_falso), "NEXUS", {}) is None


def test_qa_negative_un_dm_entre_agentes_tampoco_se_filtra(filtro):
    linea = evento(**{"from": "ALICE", "to": "JARVIS", "channel": "dm:alice:jarvis"})
    assert filtro.filter_line(linea, "FABLE", {}) is None
    assert filtro.filter_line(linea, "JARVIS", {}) is not None


def test_qa_negative_el_cifrado_no_es_lo_que_protege(filtro):
    """Lo que se filtraba era metadato + texto cifrado. Que no se pueda leer no
    lo vuelve publicable: la regla de William del 2-jun no distingue."""
    linea = evento(message="gAAAAAB" + "x" * 120, _encrypted=True)
    assert filtro.filter_line(linea, "NEXUS", {}) is None


# --- qa_control: el test puede fallar ------------------------------------

def test_control_no_vacuo_el_test_puede_fallar(filtro):
    """Un control que nunca puede fallar no es evidencia de nada."""
    entregado = filtro.filter_line(evento(), "ALICE", {})
    assert entregado is not None
    with pytest.raises(AssertionError):
        assert filtro.filter_line(evento(), "ALICE", {}) is None
