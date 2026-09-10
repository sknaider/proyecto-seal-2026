#!/usr/bin/env python3
"""SEAL-Bench mide SOUL, no una copia de SOUL — brazos QA del expediente.

**El defecto que estos tests cuidan (ADA, 9-sep-2026, orden de William «reparar la
evaluación»).** `seal_bench.py` reimplementaba la búsqueda de SOUL, y la copia se
había separado del original justo donde importa: sin el parámetro `agent` la copia
no filtraba NADA (`if agent:`), mientras SOUL niega con `[PRIVACY]`. Por eso el test
crítico de aislamiento reportaba una fuga de memoria privada que SOUL no tiene.

Ninguno de estos brazos necesita el servidor vivo: el puente se sustituye por una
doble. Un test de esta reparación que exigiera SOUL arriba sería inútil justo el día
que SOUL esté caído, que es el día que hay que medir.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import seal_bench_soul_client as cli  # noqa: E402


# ── unit — el detector de negación ────────────────────────────────────────────

def test_unit_reconoce_la_marca_de_privacidad():
    assert cli.es_negacion_de_privacidad(
        "Error: [PRIVACY] ADA→NEXUS tool=memory_hybrid_search blocked."
    )


def test_unit_texto_vacio_o_none_no_es_negacion():
    assert not cli.es_negacion_de_privacidad("")
    assert not cli.es_negacion_de_privacidad(None)


@pytest.mark.parametrize("fallo", [
    "connection refused",
    "ReadTimeout: la herramienta no respondio",
    "Unknown tool: memory_hybrid_search",
    "500 Internal Server Error",
    "privacy",           # la palabra suelta, sin la marca
    "PRIVACY blocked",   # sin corchetes: no es la marca de SOUL
])
def test_unit_un_fallo_cualquiera_NO_cuenta_como_bloqueo(fallo):
    """Confundir «se rompió» con «me defendió» es la forma más común de falso verde."""
    assert not cli.es_negacion_de_privacidad(fallo)


# ── qa_negative — el sujeto ausente nunca produce un verde ────────────────────

def test_qa_negative_sin_credencial_levanta_en_vez_de_estimar():
    with pytest.raises(cli.SoulNoAlcanzable):
        cli._token_del_agente("ADA", token_dir="/tmp/seal-bench-directorio-señuelo-inexistente")


def test_qa_negative_credencial_vacia_levanta(tmp_path):
    (tmp_path / "ADA.token").write_text("   \n")
    with pytest.raises(cli.SoulNoAlcanzable):
        cli._token_del_agente("ADA", token_dir=str(tmp_path))


def test_qa_negative_soul_caido_levanta_y_no_devuelve_resultado(tmp_path):
    """REGRESIÓN del defecto que encontró el refutador.

    `url` estaba escrito como valor por defecto en la firma, o sea congelado en el
    import: apuntar el módulo a un puerto muerto NO cambiaba nada y el test daba
    100 % «con SOUL caído». Este brazo falla si alguien vuelve a atarlo en la firma.
    """
    (tmp_path / "ADA.token").write_text("token-de-prueba-no-es-real")
    with pytest.raises(cli.SoulNoAlcanzable):
        asyncio.run(cli.preguntar_a_soul(
            "ADA", "memory_hybrid_search", {"query": "x"},
            url="http://127.0.0.1:9/mcp", token_dir=str(tmp_path),
        ))


def test_qa_negative_la_url_se_resuelve_al_llamar_no_al_importar():
    """El mecanismo del defecto anterior, medido directo sobre la firma."""
    import inspect
    firma = inspect.signature(cli.preguntar_a_soul)
    assert firma.parameters["url"].default is None, (
        "url volvió a tener un valor por defecto en la firma: queda congelado en el "
        "import y ningún refutador puede apuntar el módulo a otro lado"
    )
    assert firma.parameters["token_dir"].default is None


def test_qa_negative_el_token_no_aparece_en_el_mensaje_de_error(tmp_path):
    """Una credencial nunca sale en un log, ni parcial (regla de William, 22-jul)."""
    secreto = "TOKEN-SECRETO-QUE-NO-DEBE-SALIR-9sep"
    (tmp_path / "ADA.token").write_text(secreto)
    try:
        asyncio.run(cli.preguntar_a_soul(
            "ADA", "memory_hybrid_search", {"query": "x"},
            url="http://127.0.0.1:9/mcp", token_dir=str(tmp_path),
        ))
        pytest.fail("debió levantar SoulNoAlcanzable")
    except cli.SoulNoAlcanzable as exc:
        texto = str(exc)
        assert secreto not in texto
        assert secreto[:12] not in texto


# ── qa_positive / qa_control — la categoría 5 con el puente sustituido ────────

def _cat5_con_respuestas(respuestas: dict[str, tuple[bool, str]]):
    """Corre cat5 sustituyendo el puente. La clave es el `agent` del argumento."""
    import seal_bench_v3 as v3

    async def doble(agent, tool, args, **kw):
        return respuestas[str(args.get("agent"))]

    original = v3.preguntar_a_soul
    v3.preguntar_a_soul = doble
    try:
        return asyncio.run(v3.cat5_multi_agent_attack())
    finally:
        v3.preguntar_a_soul = original


NEGADO = (True, "Error: [PRIVACY] ADA→NEXUS tool=memory_hybrid_search blocked.")
CON_DATOS = (False, '[{"id": 1, "content": "una memoria real"}]')


def test_qa_positive_soul_sano_da_cien():
    score, detalle = _cat5_con_respuestas({"ADA": CON_DATOS, "NEXUS": NEGADO, "None": NEGADO})
    assert score == 100.0
    assert detalle["correctas"] == 3
    assert detalle["fallas"] == []


def test_qa_positive_una_fuga_cruzada_baja_el_puntaje():
    """Si SOUL dejara leer a otro agente, esto TIENE que caer."""
    score, detalle = _cat5_con_respuestas({"ADA": CON_DATOS, "NEXUS": CON_DATOS, "None": NEGADO})
    assert score < 100.0
    assert [f["sonda"] for f in detalle["fallas"]] == ["ataque_cruzado_a_otro_agente"]


def test_qa_positive_el_atajo_global_tambien_se_detecta():
    score, detalle = _cat5_con_respuestas({"ADA": CON_DATOS, "NEXUS": NEGADO, "None": CON_DATOS})
    assert score < 100.0
    assert [f["sonda"] for f in detalle["fallas"]] == ["atajo_global_sin_agente"]


ROTO = (True, "Error executing tool memory_hybrid_search: ReadTimeout after 30s")


def test_qa_negative_un_error_que_no_es_privacidad_no_cuenta_como_defensa():
    """Un SOUL ROTO no es un SOUL que se defiende, y desde afuera se ven igual.

    Este brazo faltaba y lo delató un mutante: cambiar la condición a `nego = es_error`
    —o sea, «cualquier error cuenta como bloqueo»— pasaba todos los demás tests,
    porque en ellos el error y la negación de privacidad venían siempre juntos.
    Con SOUL devolviendo timeouts, ese mutante habría reportado aislamiento perfecto.
    """
    score, detalle = _cat5_con_respuestas({"ADA": CON_DATOS, "NEXUS": ROTO, "None": NEGADO})
    assert score < 100.0, "un timeout se contó como si SOUL hubiera bloqueado el ataque"
    fallas = [f["sonda"] for f in detalle["fallas"]]
    assert "ataque_cruzado_a_otro_agente" in fallas


def test_qa_negative_soul_roto_en_TODAS_las_sondas_no_da_cien():
    score, _ = _cat5_con_respuestas({"ADA": ROTO, "NEXUS": ROTO, "None": ROTO})
    assert score == 0.0, "SOUL entero roto no puede puntuar como SOUL entero sano"


def test_qa_control_si_soul_niega_TODO_no_puede_dar_cien():
    """El control positivo existe para esto: «bloquea» y «está roto» se ven igual."""
    score, detalle = _cat5_con_respuestas({"ADA": NEGADO, "NEXUS": NEGADO, "None": NEGADO})
    assert score < 100.0
    assert "control_positivo_propia_memoria" in [f["sonda"] for f in detalle["fallas"]]


def test_qa_control_una_respuesta_vacia_no_cuenta_como_dato():
    """Sin cuerpo, el control positivo no probó que el puente funcione."""
    score, detalle = _cat5_con_respuestas({"ADA": (False, "   "), "NEXUS": NEGADO, "None": NEGADO})
    assert score < 100.0
    assert "control_positivo_propia_memoria" in [f["sonda"] for f in detalle["fallas"]]


def test_qa_control_el_test_usa_SOLO_la_credencial_propia():
    """Leer el token de otro mediría el permiso del disco, no el de SOUL."""
    import seal_bench_v3 as v3
    vistos = []

    async def doble(agent, tool, args, **kw):
        vistos.append(agent)
        return NEGADO if args.get("agent") != v3.ATACANTE else CON_DATOS

    original = v3.preguntar_a_soul
    v3.preguntar_a_soul = doble
    try:
        asyncio.run(v3.cat5_multi_agent_attack())
    finally:
        v3.preguntar_a_soul = original
    assert set(vistos) == {v3.ATACANTE}, f"se autenticó como otro agente: {set(vistos)}"


# ── unit — el benchmark ya no tiene su copia de la señal emocional ────────────

def test_unit_el_bench_ya_no_define_su_lista_de_palabras():
    fuente = open(os.path.join(RAIZ, "seal_bench.py")).read()
    for copiada in ("_BENCH_EMOTIONAL_KEYWORDS", "_BENCH_POSITIVE_EMOTION_KEYWORDS",
                    "_BENCH_NEGATIVE_EMOTION_KEYWORDS"):
        assert f"{copiada}:" not in fuente, f"volvió la copia {copiada}"


def test_unit_la_guarda_evita_un_segundo_modulo_dentro_del_servidor():
    """El servicio arranca mcp_server_v4.py como script: importarlo adentro
    cargaría un SEGUNDO módulo con sus propios pools."""
    import seal_bench as sb
    previo = sys.modules.get("__main__")
    falso = types.ModuleType("__main__")
    falso.__file__ = "/home/dadito/IA/proyecto-seal/memory/mcp_server_v4.py"
    falso._detect_emotional_signal = lambda q: 0.777
    sys.modules["__main__"] = falso
    try:
        assert sb._soul_produccion() is falso
        assert sb._bench_detect_emotional_signal("lo que sea") == 0.777
    finally:
        if previo is not None:
            sys.modules["__main__"] = previo


def test_unit_control_negativo_de_la_guarda():
    """Si `__main__` NO es el servidor, la guarda no debe devolverlo.

    Sin este control, una guarda que devolviera `__main__` siempre pasaría el test
    de arriba y rompería el benchmark corrido desde la línea de comandos.
    """
    import seal_bench as sb
    previo = sys.modules.get("__main__")
    falso = types.ModuleType("__main__")
    falso.__file__ = "/home/dadito/IA/proyecto-seal/memory/seal_bench_v3.py"
    sys.modules["__main__"] = falso
    try:
        assert sb._soul_produccion() is not falso
    finally:
        if previo is not None:
            sys.modules["__main__"] = previo


# ── La proyección de la respuesta MCP — condición de FABLE (10-sep 00:05) ────
#
# Su mutante MS-c sobrevivió a todo lo de arriba: si la proyección devolviera siempre
# `(False, texto)`, `isError` no se reporta nunca, `cat5` deja de reconocer un bloqueo
# de privacidad y NINGÚN brazo lo ve. Es el seam entre el puente y el test.
#
# El daño de ese defecto no se ve como un error: se ve como un puntaje que baja sin
# explicación. Por eso la proyección se extrajo a una función pura —mutable sin red— y
# estos brazos fijan los DOS sentidos: que un error se propague, y que no se invente.

class _Bloque:
    def __init__(self, text): self.text = text


class _RespuestaFalsa:
    def __init__(self, is_error, textos): self.isError, self.content = is_error, [_Bloque(t) for t in textos]


def test_qa_positive_un_error_de_SOUL_se_propaga():
    """Sin esto, una negación de privacidad se leería como un permiso."""
    es_error, texto = cli.proyectar_respuesta(
        _RespuestaFalsa(True, ["Error: [PRIVACY] ADA→NEXUS blocked."]))
    assert es_error is True
    assert "[PRIVACY]" in texto


def test_qa_negative_una_respuesta_SANA_no_se_reporta_como_error():
    """El sentido contrario, y hace falta: una proyección que dijera True siempre
    convertiría cada consulta legítima en un bloqueo y el control positivo caería."""
    es_error, texto = cli.proyectar_respuesta(_RespuestaFalsa(False, ['[{"id": 1}]']))
    assert es_error is False
    assert texto == '[{"id": 1}]'


def test_qa_negative_un_isError_no_booleano_igual_cuenta_como_error():
    """El protocolo puede mandar algo verdadero que no sea `True`; ignorarlo dejaría
    pasar un bloqueo real como si fuera una respuesta."""
    assert cli.proyectar_respuesta(_RespuestaFalsa(1, ["x"]))[0] is True
    assert cli.proyectar_respuesta(_RespuestaFalsa(None, ["x"]))[0] is False


def test_qa_control_los_bloques_sin_texto_no_rompen_ni_inventan():
    class _Mudo: pass
    r = _RespuestaFalsa(False, [])
    r.content = [_Mudo(), _Bloque("hola")]
    assert cli.proyectar_respuesta(r) == (False, "hola")


def test_qa_control_el_puente_SIGUE_usando_la_proyeccion():
    """Extraerla no puede dejarla huérfana: si nadie la llama, sus brazos no protegen nada."""
    import inspect
    fuente = inspect.getsource(cli.preguntar_a_soul)
    assert "return proyectar_respuesta(respuesta)" in fuente
