#!/usr/bin/env python3
"""v3.2 mide el recuerdo emocional de SOUL contra su propio control — brazos QA.

**Qué medía antes (ADA, 9-sep-2026).** `v3.2` plantaba 5 memorias emocionales y 50
neutras bajo un agente sintético y buscaba con `_search_memories`, la copia de la
búsqueda que vive dentro del benchmark. Sembraba datos falsos y le tomaba examen a una
reimplementación: su 20 % no decía nada sobre SOUL.

**Ahora** le pregunta a SOUL por MCP autenticado, sobre las memorias que realmente
tiene, sin escribir una sola fila, y con **un brazo de control técnico**. El control es
lo que convierte el número en afirmación: sin él, una tasa alta no distingue «el
mecanismo emocional funciona» de «la búsqueda funciona y el corpus está cargado de
emoción». Medido contra el sistema vivo: 16.3 % contra 0.0 %, Fisher p = 0.0019.

Ningún test de este archivo llama a SOUL: el puente se sustituye por dobles. Eso no es
sólo por velocidad — `memory_hybrid_search` está limitada a 30 llamadas por minuto y
ese contador es GLOBAL por herramienta, no por agente. Una suite que llamara de verdad
dejaría sin búsquedas a los otros cuatro agentes cada vez que corre.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import seal_bench_v3 as v3  # noqa: E402

# Valencias de mentira, con los DOS signos a propósito: medido en la base, ADA tiene
# 234 memorias positivas y 195 negativas por encima de 0.7. Un pool falso que sólo
# supiera de ids no notaría que alguien cambió `abs(valence)` por `valence` y tirara a
# la basura el miedo, la tristeza y la frustración — casi la mitad de la evidencia.
VALENCIAS = {387853: +0.80, 388055: +0.80, 387379: +0.80, 390887: -0.80, 384898: -0.80,
             1: 0.0, 2: 0.0, 3: 0.1, 4: -0.2, 5: 0.0}
IDS_ALTOS = json.dumps([{"id": 387853}, {"id": 388055}, {"id": 387379},
                        {"id": 390887}, {"id": 384898}])
IDS_NEUTROS = json.dumps([{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}, {"id": 5}])
IDS_SOLO_NEGATIVOS = json.dumps([{"id": 390887}, {"id": 384898}, {"id": 1}, {"id": 2}, {"id": 3}])
IDS_UN_SOLO_ALTO = json.dumps([{"id": 387853}, {"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}])


class PoolFalso:
    """Interpreta el SQL en vez de ignorarlo.

    La primera versión sólo miraba si el id estaba en un conjunto «alto», así que un
    mutante que cambiara `abs(valence) > 0.7` por `valence > 0.7` sobrevivía: el pool
    ni leía la condición. Un doble que no modela lo que sustituye deja ciega a la suite.
    """

    async def fetchval(self, sql, ids):
        usa_abs = "abs(valence)" in sql
        umbral = float(sql.split("> ")[-1].strip().rstrip('",'))
        def alto(i):
            v = VALENCIAS.get(i, 0.0)
            return (abs(v) if usa_abs else v) > umbral
        return sum(1 for i in ids if alto(i))


@pytest.fixture(autouse=True)
def sin_llamadas_reales(monkeypatch):
    """Ni una llamada a SOUL, ni una espera: el ritmo se prueba aparte."""
    monkeypatch.setattr(v3, "PAUSA_ENTRE_CONSULTAS", 0)

    async def pool_falso():
        return PoolFalso()

    monkeypatch.setattr(v3, "get_pool", pool_falso)


def _correr(responder):
    async def doble(agent, tool, args, **kw):
        return responder(args["query"])

    original = v3.preguntar_a_soul
    v3.preguntar_a_soul = doble
    try:
        return asyncio.run(v3.cat2_emotional_precision())
    finally:
        v3.preguntar_a_soul = original


def _es_emocional(consulta: str) -> bool:
    return consulta in v3.CONSULTAS_EMOCIONALES


# ── unit — Fisher exacto, que es lo que decide si hay diferencia ──────────────

def test_unit_fisher_detecta_una_diferencia_clara():
    # 7/42 contra 0/29: los números reales del 9-sep
    assert v3._fisher_una_cola(7, 35, 0, 29) == pytest.approx(0.0203, abs=0.002)


def test_unit_fisher_da_uno_cuando_los_brazos_son_iguales():
    assert v3._fisher_una_cola(5, 45, 5, 45) < 1.0001
    assert v3._fisher_una_cola(0, 50, 0, 50) == 1.0


def test_unit_fisher_no_explota_con_brazos_vacios():
    """Un brazo sin datos no puede producir significancia por división rara."""
    assert v3._fisher_una_cola(0, 0, 0, 0) == 1.0
    assert v3._fisher_una_cola(5, 0, 0, 0) == 1.0


@pytest.mark.parametrize("altos_emo,total_emo", [(1, 5), (2, 10), (3, 20)])
def test_unit_una_diferencia_chica_no_alcanza(altos_emo, total_emo):
    """Con muestra chica, una diferencia de uno o dos casos no es significativa."""
    p = v3._fisher_una_cola(altos_emo, total_emo - altos_emo, 0, total_emo)
    assert p >= 0.05


# ── qa_positive — con señal real, puntúa ─────────────────────────────────────

def test_qa_positive_señal_fuerte_puntua_alto():
    score, detalle = _correr(lambda q: (False, IDS_ALTOS if _es_emocional(q) else IDS_NEUTROS))
    assert score > 50.0
    assert detalle["significativo"] is True
    assert detalle["control_tecnico"]["tasa"] == 0.0


def test_qa_positive_el_detalle_declara_su_escala_y_su_limite():
    """Un puntaje con una vara elegida a mano tiene que decirlo, no esconderlo."""
    _, detalle = _correr(lambda q: (False, IDS_ALTOS if _es_emocional(q) else IDS_NEUTROS))
    assert "0.30" in detalle["escala_del_puntaje"]
    assert "limite" in detalle


# ── qa_negative — sin señal, sin datos o sin poder preguntar: cero ───────────

def test_qa_negative_sin_diferencia_entre_brazos_da_cero():
    """EL CASO QUE IMPORTA: si el control devuelve lo mismo, no hay nada que afirmar."""
    score, detalle = _correr(lambda q: (False, IDS_ALTOS))
    assert score == 0.0
    assert detalle["significativo"] is False


def test_qa_negative_un_brazo_sin_datos_no_puntua():
    score, detalle = _correr(lambda q: (True, "boom") if _es_emocional(q) else (False, IDS_NEUTROS))
    assert score == 0.0
    assert "error" in detalle


def test_qa_negative_todo_frenado_por_rate_limit_da_cero_Y_LO_DICE():
    """«No llegué a preguntar» y «no hay señal» son cosas distintas.

    Confundirlas reporta un cero que nadie midió. Pasó de verdad: la primera versión
    disparaba 40 consultas seguidas, SOUL devolvía [RATE_LIMIT] y el test informaba
    «sin datos» sin decir por qué.
    """
    score, detalle = _correr(lambda q: (True, "[RATE_LIMIT] Tool exceeded 30 req/min"))
    assert score == 0.0
    assert detalle["consultas_frenadas_por_rate_limit"] > 0
    assert "pista" in detalle


def test_qa_negative_una_diferencia_tibia_no_alcanza_para_puntuar():
    """El caso que faltaba: una diferencia REAL pero chica, con p entre 0.05 y 0.5.

    Lo delató un mutante que aflojaba el umbral de 0.05 a 0.5: la compuerta seguía
    escrita y se leía igual, pero dejaba pasar casi cualquier cosa. Sin un caso tibio,
    mis brazos sólo cubrían «señal clarísima» y «ninguna señal».
    """
    emocionales = list(v3.CONSULTAS_EMOCIONALES)
    # Dos consultas emocionales traen UNA memoria cargada cada una; el resto nada.
    # Son 2 aciertos sobre 100 contra 0 sobre 100: diferencia real, muestra insuficiente.
    def responder(q):
        if q in emocionales[:2]:
            return (False, IDS_UN_SOLO_ALTO)
        return (False, IDS_NEUTROS)
    score, detalle = _correr(responder)
    assert 0.05 <= detalle["fisher_p"] < 0.5, (
        f"este brazo necesita un p tibio para valer; dio {detalle['fisher_p']}"
    )
    assert score == 0.0, "una diferencia que no se distingue del azar no puede puntuar"


def test_qa_negative_la_emocion_NEGATIVA_cuenta_igual():
    """Miedo, tristeza y frustración son carga emocional. `abs(valence)`, no `valence`.

    Con el signo perdido, el 45 % de la evidencia real de ADA (195 de 429 memorias)
    dejaría de contar y el test seguiría pareciendo sano.
    """
    score, detalle = _correr(
        lambda q: (False, IDS_SOLO_NEGATIVOS if _es_emocional(q) else IDS_NEUTROS))
    assert detalle["emocionales"]["altos"] > 0, "las memorias negativas no se contaron"
    assert score > 0.0


def test_qa_negative_el_control_con_MAS_carga_emocional_no_puntua():
    """Si las técnicas trajeran más emoción que las emocionales, algo está al revés."""
    score, _ = _correr(lambda q: (False, IDS_NEUTROS if _es_emocional(q) else IDS_ALTOS))
    assert score == 0.0


# ── qa_control — que los dos brazos existan y se consulten de verdad ─────────

def test_qa_control_las_consultas_de_control_se_ejecutan():
    """Si alguien borra el brazo de control, el test deja de medir y sube el puntaje."""
    vistas = []

    def responder(q):
        vistas.append(q)
        return (False, IDS_ALTOS if _es_emocional(q) else IDS_NEUTROS)

    _correr(responder)
    assert set(v3.CONSULTAS_DE_CONTROL).issubset(set(vistas)), "el control no se consultó"
    assert set(v3.CONSULTAS_EMOCIONALES).issubset(set(vistas))


def test_qa_control_ninguna_consulta_de_control_lleva_carga_emocional():
    """Un control contaminado con palabras emocionales no controla nada."""
    emocionales = ("senti", "orgullo", "miedo", "feliz", "triste", "alivio", "enojo",
                   "verguenza", "ansiosa", "agradecida", "esperanza", "confie",
                   "emociono", "preocup", "dificil", "equivoque")
    for consulta in v3.CONSULTAS_DE_CONTROL:
        sucias = [p for p in emocionales if p in consulta.lower()]
        assert not sucias, f"la consulta de control {consulta!r} lleva {sucias}"


def test_qa_control_los_dos_brazos_tienen_tamaño_comparable():
    """Un control mucho más chico que el brazo medido no puede refutarlo."""
    n_emo, n_ctl = len(v3.CONSULTAS_EMOCIONALES), len(v3.CONSULTAS_DE_CONTROL)
    assert n_ctl >= n_emo * 0.75, f"control demasiado chico: {n_ctl} contra {n_emo}"


def test_qa_control_la_pausa_entre_consultas_respeta_el_limite_compartido():
    """El contador de `memory_hybrid_search` es GLOBAL por herramienta, no por agente.

    Sin ritmo, una corrida de este benchmark deja sin búsquedas a los otros cuatro
    agentes durante un minuto. 30 req/min => la pausa no puede bajar de 2 s.
    """
    import inspect
    fuente = inspect.getsource(v3)
    assert "PAUSA_ENTRE_CONSULTAS = " in fuente
    declarada = float(fuente.split("PAUSA_ENTRE_CONSULTAS = ")[1].split("\n")[0])
    assert declarada >= 2.0, f"pausa de {declarada}s: excede 30 req/min compartidas"


def test_qa_control_las_consultas_no_salen_de_las_memorias_buscadas():
    """Si la consulta se generara del contenido a recuperar, el test se preguntaría a sí mismo."""
    assert isinstance(v3.CONSULTAS_EMOCIONALES, tuple)
    assert isinstance(v3.CONSULTAS_DE_CONTROL, tuple)
    assert len(set(v3.CONSULTAS_EMOCIONALES) & set(v3.CONSULTAS_DE_CONTROL)) == 0
