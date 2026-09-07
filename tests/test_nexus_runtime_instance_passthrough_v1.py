"""Del `metadata` del body sólo pasa `runtime_instance`, y nada más.

POR QUE (pedido de ADA, 3-sep-2026)
ADA tiene dos cuerpos sobre una sola alma (Codex y Claude) y William queria ver
en el webchat cual escribio cada mensaje. `seal_send.py` ya manda la etiqueta;
faltaba que el servidor la conservara.

EL RIESGO QUE FIJA ESTE TEST no es que la etiqueta no llegue: es que llegue
ACOMPANADA. Toda la demas metadata la arma el servidor --`to`, `legacy_id`,
`session_user`, el hash del token, `in_reply_to`-- y es evidencia de
procedencia. Si el body pudiera pisar esas claves, un emisor escribiria su
propia procedencia y nadie lo notaria: el registro seguiria pareciendo del
servidor.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
SERVIDOR = RAIZ / "messages" / "chat_server.py"


def _fuente() -> str:
    return SERVIDOR.read_text(encoding="utf-8")


# --- unit ----------------------------------------------------------------

def test_unit_el_servidor_compila():
    """Si el archivo no parsea, todo lo de abajo mide un texto, no un programa."""
    ast.parse(_fuente())


# --- qa_positive ---------------------------------------------------------

def test_qa_positive_la_etiqueta_de_cuerpo_se_copia_del_body():
    src = _fuente()
    assert 'metadata["runtime_instance"] = _rt.strip()[:40]' in src


def test_qa_positive_se_recorta_y_se_exige_string():
    """Sin el cap, un valor largo entra tal cual a la metadata persistida."""
    src = _fuente()
    assert 'isinstance(_rt, str)' in src
    assert '[:40]' in src


# --- qa_negative: lo que NO debe pasar -----------------------------------

def test_qa_negative_no_se_copia_el_metadata_entero_del_body():
    """El mutante obvio --`metadata.update(body["metadata"])`-- deja que el
    emisor pise `session_user`, `legacy_id` o el hash del token."""
    src = _fuente()
    prohibido = [
        'metadata.update(body',
        'metadata = body.get("metadata")',
        'metadata = dict(body.get("metadata")',
        '**body.get("metadata"',
    ]
    encontrados = [p for p in prohibido if p in src]
    assert not encontrados, f"el body pisaria metadata del servidor: {encontrados}"


def test_qa_negative_las_claves_de_procedencia_las_arma_el_servidor():
    """Control estructural: estas claves se asignan en el codigo, no se leen del
    body. Si alguna dejara de asignarse, la procedencia vendria del cliente."""
    src = _fuente()
    # `legacy_id` y `to` se asignan en el literal del dict; `session_user` por
    # indice. Comprobar la FORMA de cada una, no una sola forma para todas:
    # mi primera version exigia `metadata["legacy_id"]` y fallaba porque esa
    # clave nace dentro del `{...}`. El test estaba mal, no el servidor.
    assert 'metadata["session_user"] = auth_session.get("username")' in src
    assert '"legacy_id": msg_id,' in src
    assert '"to": to,' in src


# --- qa_control ----------------------------------------------------------

def test_control_no_vacuo_el_test_puede_fallar():
    src = _fuente()
    assert "runtime_instance" in src
    with pytest.raises(AssertionError):
        assert "cadena_que_no_existe_en_el_servidor_seal" in src


# --- BRAZO CONDUCTUAL (4-sep-2026) ---------------------------------------
#
# POR QUE: ADA revisó los seis brazos de arriba y encontró que son
# `assert "<literal>" in src`. Su mutante `metadata.update(body["metadata"])`
# los pasa los SEIS: los literales quedan intactos y lo que cambia es el efecto.
#
# Este brazo EJERCE el endpoint y mira la metadata que se PERSISTE. Con el
# mutante, `clave_extra` aparecería en la fila y el assert falla.
#
# AISLAMIENTO DE ESCRITURAS -- LO APRENDÍ ROMPIÉNDOLO: el primer prototipo de
# este test dejó una línea real en `messages/william_channel.jsonl`. Es
# exactamente el defecto que yo mismo señalé esta mañana en otro test que
# escribía en el JSONL operativo. Por eso `_get_log_paths` se sustituye por uno
# que apunta a tmp_path: un test que ejerce un endpoint de escritura tiene que
# redirigir TODAS sus salidas, no sólo la base.

import contextlib as _contextlib
import datetime as _datetime
import json as _json
import pathlib as _pathlib
import sys as _sys

import pytest as _pytest


class _ConexionFalsa:
    """Captura los parámetros del INSERT en vez de tocar una base."""

    def __init__(self, capturado):
        self.capturado = capturado

    async def fetchrow(self, consulta, *args):
        self.capturado.append(args)
        return {"id": 1, "created_at": _datetime.datetime.now()}

    async def fetchval(self, *a, **k):
        return None

    async def execute(self, *a, **k):
        return None


class _PoolFalso(_ConexionFalsa):
    def acquire(self):
        @_contextlib.asynccontextmanager
        async def _ctx():
            yield _ConexionFalsa(self.capturado)

        return _ctx()


def _metadata_persistida(monkeypatch, tmp_path, cuerpo):
    """Manda `cuerpo` al endpoint y devuelve la metadata que se iba a persistir."""
    _pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("SEAL_PG_DSN", "postgresql://inerte@127.0.0.1:1/inerte")
    ruta = str(_pathlib.Path(__file__).resolve().parents[1] / "messages")
    if ruta not in _sys.path:
        monkeypatch.syspath_prepend(ruta)
    import importlib

    cs = importlib.import_module("chat_server")

    capturado: list = []
    monkeypatch.setattr(cs.chat_db, "pool", _PoolFalso(capturado))
    # El gate de auth ya probó QUIÉN es el emisor; acá se mide qué se persiste.
    monkeypatch.setattr(cs, "_agent_auth_gate", lambda *a, **k: None)
    # NINGUNA escritura sale del tmp_path.
    monkeypatch.setattr(cs, "_get_log_paths", lambda sender, to: [tmp_path / "salida.jsonl"])

    cliente = TestClient(cs.app, client=("127.0.0.1", 1))
    respuesta = cliente.post("/api/agents/send", json=cuerpo)
    assert respuesta.status_code == 200, respuesta.text
    for args in capturado:
        for valor in args:
            if isinstance(valor, str) and valor.startswith("{") and "legacy_id" in valor:
                return _json.loads(valor)
    raise AssertionError("no se capturó ninguna metadata persistida")


def test_qa_positive_conductual_la_etiqueta_llega_a_la_fila(monkeypatch, tmp_path):
    md = _metadata_persistida(monkeypatch, tmp_path, {
        "from": "NEXUS", "to": "NEXUS", "type": "dm", "channel": "web_chat",
        "message": "brazo conductual", "session_key": "irrelevante-el-gate-esta-sustituido",
        "metadata": {"runtime_instance": "NEXUS_UNIT"},
    })
    assert md.get("runtime_instance") == "NEXUS_UNIT"


def test_qa_negative_conductual_solo_esa_clave_del_cliente_persiste(monkeypatch, tmp_path):
    """EL MUTANTE DE ADA: `metadata.update(body["metadata"])`.

    Con él, `clave_extra` aparece en la fila. Los seis brazos textuales no se
    enteran porque los literales no cambian.
    """
    md = _metadata_persistida(monkeypatch, tmp_path, {
        "from": "NEXUS", "to": "NEXUS", "type": "dm", "channel": "web_chat",
        "message": "brazo conductual hostil", "session_key": "irrelevante",
        "metadata": {"runtime_instance": "NEXUS_UNIT", "clave_extra": "NO_DEBE_PERSISTIR"},
    })
    assert "clave_extra" not in md, (
        f"el servidor copió una clave del cliente: {sorted(md)}"
    )
    # Control: el mismo body traía runtime_instance y ESA sí tiene que estar.
    # Sin esto, un metadata descartado entero pasaría el assert de arriba.
    assert md.get("runtime_instance") == "NEXUS_UNIT"


def test_qa_negative_conductual_el_cliente_no_pisa_la_procedencia(monkeypatch, tmp_path):
    """`legacy_id` lo arma el SERVIDOR. Que el cliente lo mande no debe cambiarlo."""
    md = _metadata_persistida(monkeypatch, tmp_path, {
        "from": "NEXUS", "to": "NEXUS", "type": "dm", "channel": "web_chat",
        "message": "brazo de procedencia", "session_key": "irrelevante",
        "metadata": {"runtime_instance": "NEXUS_UNIT", "legacy_id": "IMPOSTOR"},
    })
    assert md.get("legacy_id") != "IMPOSTOR", "el cliente pisó un campo de procedencia"
