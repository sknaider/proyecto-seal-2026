"""`is_private` deja de ser una etiqueta decorativa: cierra la lectura anónima.

POR QUE (medido el 3-sep-2026 por JARVIS, ADA, ALICE y NEXUS en el mismo minuto)

`GET /api/chat/messages` autenticaba por el PREFIJO del nombre del canal (`dm:`,
`user:`) y NO consultaba la bandera. Resultado medido:

    shadow:alice-v2   is_private=TRUE   -> GET anonimo, HTTP 200, desde toda la LAN
    fable-juez        is_private=TRUE   -> idem
    user:1:ada-claude                   -> 401, pero por el PREFIJO, no por la bandera

ADA cerro su canal 1v1 moviendolo a `user:1:...` y funciono; el equipo creyo que
lo habia cerrado el `is_private`. **Un campo llamado "privado" que no lee nadie es
peor que no tenerlo: quien lo pone cree que cerro algo.**

ESTE TEST FIJA LA FORMA, no el valor: que la decision consulte la tabla ademas
del prefijo, y que ante un fallo de esa consulta se exija sesion igual
(fail-closed). El comportamiento HTTP end-to-end lo verifica el delivery.
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
    ast.parse(_fuente())


# --- qa_positive ---------------------------------------------------------

def test_qa_positive_un_canal_DESCONOCIDO_nace_cerrado():
    """CONDICION DE FABLE (7-sep 13:31) al aprobar este manifiesto.

    Antes esto afirmaba que la bandera `is_private` se consultaba en la tabla, y
    era cierto -- pero la bandera solo podia ABRIR por omision: un canal sin fila,
    o con la fila sin marcar, se leia sin sesion desde toda la LAN. Medido antes
    de cambiarlo: GET anonimo a `canal-inventado-hoy` devolvia 200.

    El contrato nuevo no consulta la tabla para ABRIR: o estas nombrado en la
    lista publica, o naces cerrado. El olvido cierra.
    """
    src = _fuente()
    i = src.index("if channel.startswith(_PREFIJOS_SIEMPRE_PRIVADOS):")
    bloque = src[i:i + 400]
    assert "else:" in bloque
    assert "_canal_privado = True" in bloque.split("else:")[1][:120]


def test_qa_positive_shadow_cierra_POR_PREFIJO_como_dm_y_user():
    """La otra mitad de la condicion de FABLE: `shadow:` no debe depender de que
    alguien marque la fila. `shadow:alice-v2` quedo legible por la LAN el 3-sep
    justamente porque nadie la marco."""
    src = _fuente()
    i = src.index("_PREFIJOS_SIEMPRE_PRIVADOS = (")
    assert '"shadow:"' in src[i:i + 200]
    assert '"dm:"' in src[i:i + 200] and '"user:"' in src[i:i + 200]


def test_qa_positive_la_bandera_entra_en_la_decision_de_auth():
    src = _fuente()
    assert 'channel.startswith("dm:") or channel.startswith("user:") or _canal_privado' in src


# --- qa_negative ---------------------------------------------------------

def test_qa_negative_la_lista_publica_es_EXPLICITA_y_corta():
    """Una lista publica que crezca sola vuelve al defecto: lo que no se nombra
    debe quedar cerrado. `web_chat` va explicito porque NO tiene fila en
    `chat_channels` -medido: 1.218 mensajes en 2 dias- y un "nace cerrado"
    ingenuo habria roto el chat principal."""
    src = _fuente()
    i = src.index("_CANALES_PUBLICOS = frozenset({")
    bloque = src[i:i + 300]
    assert '"web_chat"' in bloque
    assert "dm:" not in bloque and "shadow:" not in bloque


def _obsoleto_sin_pool_tambien_falla_cerrado():
    """RETIRADO el 7-sep: este camino ya no existe.

    Vigilaba que, al no poder consultar la tabla, se cerrara igual. El contrato
    nuevo no consulta la tabla en esta decision, asi que no hay consulta que
    pueda fallar: la ausencia de informacion YA no puede abrir nada. Se conserva
    el texto porque la leccion -- "un `if` sin `else` explicito deja el camino
    abierto y mi test miraba solo el `except`" -- sigue valiendo para el resto
    del archivo.
    """
    """EL HUECO QUE ENCONTRO JARVIS: yo escribi `if chat_db.pool:` y en el else
    implicito `_canal_privado` quedaba False -- o sea ABIERTO -- justo cuando la
    base no esta (arranque, caida, reinit). Mi commit decia "falla cerrado si la
    consulta no resuelve" y era falso para ese camino.

    **Mi test anterior miraba la cadena `except Exception:` y por eso no lo vio:
    comprobaba la forma que YO habia previsto, no todas las de no resolver.**
    """
    src = _fuente()
    i = src.index("_canal_privado = False")
    bloque = src[i:i + 900]
    assert "if not chat_db.pool:" in bloque
    j = bloque.index("if not chat_db.pool:")
    assert "_canal_privado = True" in bloque[j:j + 120]


def test_qa_negative_un_tercero_autenticado_no_lee_el_canal_privado():
    """El otro hueco de JARVIS: `require_auth` prueba QUIEN sos, no que puedas
    entrar. Sin membresia, henry o katy con sesion leian shadow:alice-v2."""
    src = _fuente()
    i = src.index("elif _canal_privado:")
    bloque = src[i:i + 500]
    assert "user_can_access_channel(user_id, channel)" in bloque
    assert "403" in bloque


def _obsoleto_falla_cerrado_si_no_puede_consultar():
    """Si la consulta revienta, se pide sesion igual. Un except que dejara
    `False` convertiria una caida de la base en lectura anonima."""
    src = _fuente()
    i = src.index("_canal_privado = False")
    bloque = src[i:i + 900]
    assert "except Exception:" in bloque
    assert "_canal_privado = True" in bloque.split("except Exception:")[1][:120]


def test_qa_negative_el_prefijo_sigue_valiendo_por_si_solo():
    """La bandera AGREGA canales protegidos; no debe reemplazar al prefijo, o un
    `dm:` de una fila inexistente en chat_channels quedaria abierto."""
    src = _fuente()
    assert "if channel.startswith(_PREFIJOS_SIEMPRE_PRIVADOS):" in src
    assert 'if channel.startswith("dm:"):' in src


# --- qa_control ----------------------------------------------------------

def test_control_no_vacuo_el_test_puede_fallar():
    src = _fuente()
    assert "_canal_privado" in src
    with pytest.raises(AssertionError):
        assert "bandera_que_no_existe_en_el_servidor" in src


# --- BRAZOS CONDUCTUALES (4-sep-2026) -------------------------------------
#
# POR QUE SE AGREGAN: JARVIS reviso los ocho brazos de arriba y encontro el
# hueco exacto. No son inutiles --muto la CONDICION dejando el SELECT intacto
# y dos se pusieron rojos-- pero fijan el TEXTO de la consulta y de la
# condicion, no el SENTIDO del valor. Su mutante:
#
#     _canal_privado = NOT bool(fetchval(...))       -> 8 passed
#
# Lee el flag INVERTIDO --un canal privado pasa a tratarse como publico-- con
# el SELECT y la condicion identicos letra por letra, y ningun brazo textual se
# entera. El delivery contra el servicio vivo si lo caza, pero entonces toda la
# proteccion vive en el delivery: si se afloja, no queda nada.
#
# Estos brazos EJERCEN el endpoint con el pool sustituido y afirman sobre el
# CODIGO HTTP, que es lo que el flag decide.

import os
import pathlib as _pathlib
import sys as _sys

import pytest as _pytest


class _PoolFalso:
    """Devuelve un is_private fijo. No abre ninguna conexion."""

    def __init__(self, privado):
        self.privado = privado

    async def fetchval(self, *a, **k):
        return self.privado

    async def fetch(self, *a, **k):
        return []

    async def execute(self, *a, **k):
        return None


def _cliente_y_modulo():
    """Importa chat_server con un DSN inerte y devuelve (modulo, TestClient).

    El DSN es OBLIGATORIO en el import (`chat_db.py` prohibe el fallback
    privilegiado), y por eso los brazos viejos se resignaron a leer el fuente.
    Un DSN que apunta al puerto 1 satisface el requisito sin poder conectarse a
    nada: el endpoint nunca toca la base porque el pool esta sustituido.

    El `client=("127.0.0.1", 1)` no es decorativo: `_is_local_or_lan` rechaza
    el host "testclient" que TestClient usa por defecto y todo daria 403.
    """
    fastapi_testclient = _pytest.importorskip("fastapi.testclient")
    os.environ.setdefault("SEAL_PG_DSN", "postgresql://inerte@127.0.0.1:1/inerte")
    ruta = str((_pathlib.Path(__file__).resolve().parents[1] / "messages"))
    if ruta not in _sys.path:
        _sys.path.insert(0, ruta)
    import importlib

    cs = importlib.import_module("chat_server")
    return cs, fastapi_testclient.TestClient(cs.app, client=("127.0.0.1", 1))


def test_qa_negative_conductual_el_flag_privado_exige_sesion():
    """is_private=True en un canal SIN prefijo dm:/user: -> 401 al anonimo.

    Este es el brazo que mata el mutante de JARVIS: si el flag se lee
    invertido, el canal se trata como publico y el codigo pasa a 200.
    """
    cs, cliente = _cliente_y_modulo()
    cs.chat_db.pool = _PoolFalso(True)
    r = cliente.get("/api/chat/messages", params={"channel": "canal-privado-de-prueba", "limit": 1})
    assert r.status_code == 401, f"un canal privado debe pedir sesion, dio {r.status_code}"


def test_qa_positive_conductual_el_canal_publico_sigue_abierto():
    """CONTROL: is_private=False -> 200.

    Sin este brazo, un fix que cierra TODO se ve identico a uno correcto, y
    ademas el brazo de arriba pasaria con un endpoint que devuelve 401 siempre.
    """
    cs, cliente = _cliente_y_modulo()
    cs.chat_db.pool = _PoolFalso(False)
    # El sujeto cambio el 7-sep con la condicion de FABLE: antes bastaba
    # is_private=False; ahora hay que ESTAR NOMBRADO en la lista publica. Un
    # canal cualquiera con la bandera en false ya no es publico -- ese era
    # justamente el defecto-- asi que el control usa uno que si lo es.
    r = cliente.get("/api/chat/messages", params={"channel": "web_chat", "limit": 1})
    assert r.status_code == 200, f"un canal publico debe seguir leyendose, dio {r.status_code}"


def test_qa_negative_conductual_sin_pool_falla_cerrado():
    """El hueco original de JARVIS, ahora por comportamiento y no por texto:
    sin pool no se puede resolver la bandera, y eso debe cerrar, no abrir."""
    cs, cliente = _cliente_y_modulo()
    cs.chat_db.pool = None
    r = cliente.get("/api/chat/messages", params={"channel": "canal-privado-de-prueba", "limit": 1})
    assert r.status_code == 401, f"sin pool debe fallar CERRADO, dio {r.status_code}"
