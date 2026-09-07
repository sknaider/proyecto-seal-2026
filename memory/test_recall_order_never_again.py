#!/usr/bin/env python3
"""¿Lo relevante sale PRIMERO? — la prueba que faltaba.

Escrita el 1-sep-2026 por ALICE, ANTES de ver el fix del vectorial, a propósito:
un test escrito mirando el arreglo tiende a probar lo que el arreglo hace, no lo
que el sistema debe hacer.

Contexto: el buscador devolvía "recetas de ceviche" -> memorias de credenciales
con similitud 1.00. Se encontró una causa real (normalizar por el máximo del
lote) y tres agentes dimos el caso por cerrado. FABLE frenó eso con aritmética:

    dividir todo por una constante positiva NO cambia el orden
    a > b  =>  a/c > b/c   para todo c > 0

O sea: esa causa explica por qué los PUNTAJES salen aplastados, pero no puede
explicar por qué el ORDEN está mal. Y el orden es lo único que decide qué te
devuelve el buscador.

Este archivo prueba el ORDEN y nada más. No mira puntajes: un buscador puede
tener puntajes feos y ordenar bien, o puntajes lindos y ordenar mal. Lo segundo
es lo que nos pasó, y ninguna suite lo miraba.
"""
from __future__ import annotations


def _rank(query_terms, docs, scorer):
    """Ordena docs con el scorer dado. Devuelve los ids de mayor a menor."""
    scored = [(scorer(query_terms, d), d["id"]) for d in docs]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [i for _, i in scored]


# ── El caso: una consulta específica y un corpus donde UNO es el relevante ──
DOCS = [
    {"id": 1, "text": "receta de ceviche peruano con pescado limon y aji"},
    {"id": 2, "text": "credencial rotada y login verificado por tres vias"},
    {"id": 3, "text": "el fix del DSN cacheado quedo aplicado en el daemon"},
    {"id": 4, "text": "cero clientes de aplicacion como seal en postgres"},
]
QUERY = ["ceviche", "peruano", "aji"]
RELEVANTE = 1


def _coseno_sano(q, d):
    """Un scorer que SÍ discrimina: cuenta términos de la consulta presentes."""
    t = d["text"].lower()
    return sum(1 for w in q if w in t)


def _coseno_degenerado(q, d):
    """El defecto real: todo puntúa casi igual y el orden queda al azar.

    Los valores son los cosenos pegados de e5 (~0,84) y el relevante NO tiene
    el mayor — que es justo lo observado en producción: "ceviche" devolvía
    credenciales arriba. Primer intento mío puso al relevante en el máximo y
    el propio test lo cazó: el arnés no reproducía nada.
    """
    base = {1: 0.839, 2: 0.842, 3: 0.841, 4: 0.840}
    return base[d["id"]]


def _normaliza_por_max(scores):
    m = max(scores) or 1.0
    return [s / m for s in scores]


def test_verde_un_scorer_sano_pone_lo_relevante_primero():
    orden = _rank(QUERY, DOCS, _coseno_sano)
    assert orden[0] == RELEVANTE, (
        f"lo relevante no salio primero: {orden}. "
        "Si esto falla, el ranking no depende de la consulta."
    )


def test_rojo_un_scorer_degenerado_NO_pone_lo_relevante_primero():
    """El caso que vimos en producción. Si este test PASA en verde, el arnés
    no reproduce el defecto y no sirve como oráculo."""
    orden = _rank(QUERY, DOCS, _coseno_degenerado)
    assert orden[0] != RELEVANTE, (
        "el arnés no logró reproducir el defecto: con puntajes casi iguales "
        "lo relevante salió primero por casualidad. El test no discrimina."
    )


def test_normalizar_por_el_maximo_NO_cambia_el_orden():
    """La aritmética que corrigió a tres agentes (FABLE, 1-sep).

    Si esto falla, toda nuestra conclusión de esa noche era falsa.
    """
    crudos = [_coseno_degenerado(QUERY, d) for d in DOCS]
    orden_antes = _rank(QUERY, DOCS, _coseno_degenerado)

    normalizados = _normaliza_por_max(crudos)
    por_id = {d["id"]: n for d, n in zip(DOCS, normalizados)}
    orden_despues = sorted(por_id, key=lambda i: (-por_id[i], i))

    assert orden_antes == orden_despues, (
        "dividir por el maximo cambio el orden — eso contradice la aritmetica"
    )
    assert max(normalizados) == 1.0, "el mayor deberia quedar en 1.00 tras normalizar"


def test_el_sintoma_visible_y_el_defecto_real_son_INDEPENDIENTES():
    """Lo que aprendimos el 1-sep y casi nos hace cerrar el caso de más.

    Puntajes aplastados y orden roto son dos cosas distintas: se puede tener
    una sin la otra. Por eso arreglar la normalización no garantiza que el
    orden se arregle, y por eso este archivo existe.
    """
    crudos = [_coseno_sano(QUERY, d) for d in DOCS]
    normalizados = _normaliza_por_max(crudos)

    # escala aplastada al tope...
    assert max(normalizados) == 1.0
    # ...y SIN EMBARGO el orden sigue siendo correcto
    por_id = {d["id"]: n for d, n in zip(DOCS, normalizados)}
    assert sorted(por_id, key=lambda i: (-por_id[i], i))[0] == RELEVANTE, (
        "con un scorer sano, normalizar NO rompe el orden: "
        "la normalizacion nunca fue la causa del orden malo"
    )


# ── Clamp con piso fijo hace invisible lo que cae debajo — 2-sep-2026 ──────
# Escrito por ALICE ANTES de ver el fix definitivo, a propósito: un test
# escrito mirando la solución tiende a probar lo que la solución hace, no lo
# que el sistema debe hacer (misma disciplina que el resto de este archivo).
#
# Qué lo origina: un fix de calibración desplegado la noche del 1-sep en el
# buscador de memoria hizo
#
#     _E5_COS_LO, _E5_COS_HI = 0.70, 0.90
#     sem_score = clamp((raw - 0.70) / (0.90 - 0.70), 0, 1)
#
# Efecto secundario medido por el equipo esa misma noche: los textos LARGOS
# tienen cosenos más bajos con e5, caen debajo de 0.70, el clamp los manda a
# 0 y desaparecen del resultado. Medido: una memoria de 110 caracteres vuelve
# (sim 0.92); la MISMA memoria de 670 caracteres NO vuelve. El scope no
# influye (se probó team y private).
#
# La clase de error, en general: un clamp con piso fijo no BAJA el puntaje
# de lo que cae debajo del piso — lo IGUALA a lo irrelevante (ambos quedan en
# 0.0). Eso no se ve como un error: se ve como AUSENCIA. El documento no sale
# último, sale invisible, indistinguible del ruido.

def _clamp_piso_fijo(raw, lo=0.70, hi=0.90):
    """El clamp tal como se desplegó el 1-sep: todo lo que cae debajo del
    piso queda en 0.0, exactamente igual que el ruido más irrelevante."""
    return max(0.0, min(1.0, (raw - lo) / (hi - lo)))


def _sin_piso_fijo(raw):
    """Una calibración de control que NO usa piso duro: conserva el orden
    aunque el puntaje sea bajo. Sirve para separar "el arnés está mal
    construido" de "el sistema tiene el defecto": si este control fallara,
    el problema sería el test, no la calibración con piso."""
    return raw


# Cosenos crudos (e5) de un caso equivalente al medido: el documento
# relevante es TEXTO LARGO y tiene el coseno MÁS ALTO de los cuatro, pero
# cae debajo del piso 0.70 por su longitud. Los otros tres son ruido franco,
# con cosenos bien por debajo.
DOCS_CLAMP = [
    {"id": 10, "text": "memoria irrelevante de ruido uno"},
    {"id": 20, "text": "memoria irrelevante de ruido dos"},
    {"id": 30, "text": "memoria irrelevante de ruido tres"},
    {"id": 40, "text": "memoria larga y relevante, coseno bajo solo por longitud"},
]
# El relevante lleva el id MÁS ALTO a propósito: si el clamp lo aplasta a
# 0.0 junto con el ruido, el desempate por id ya no lo puede salvar --
# demuestra que desaparece de verdad, no que "empata para arriba" por azar
# del orden de construcción del corpus.
RAW_COS_CLAMP = {10: 0.30, 20: 0.20, 30: 0.10, 40: 0.68}
RELEVANTE_CLAMP = 40


def _rank_por_score_clamp(scorer):
    scored = [(scorer(RAW_COS_CLAMP[d["id"]]), d["id"]) for d in DOCS_CLAMP]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [i for _, i in scored]


def test_clamp_con_piso_fijo_vuelve_invisible_lo_que_cae_debajo_del_piso():
    """Debe FALLAR si alguien reintroduce un clamp con piso fijo que anule
    los documentos de coseno bajo, y debe PASAR con una calibración que los
    conserve ordenados aunque tengan puntaje bajo.

    Caso ROJO explícito primero: con el clamp actual (piso 0.70) un
    documento relevante de coseno 0.68 -- el MÁS ALTO de los cuatro -- queda
    en 0.0 exactamente igual que todo el ruido, y deja de ganar el primer
    lugar. Control después: sin ese piso, el mismo documento sigue siendo
    recuperable con la misma ordenación por coseno.
    """
    # ROJO: el clamp desplegado aplasta al relevante a 0.0 y lo hace
    # indistinguible del ruido -- desaparece, no "sale último".
    score_relevante_con_clamp = _clamp_piso_fijo(RAW_COS_CLAMP[RELEVANTE_CLAMP])
    orden_con_clamp = _rank_por_score_clamp(_clamp_piso_fijo)

    assert score_relevante_con_clamp == 0.0, (
        "se esperaba que el clamp con piso 0.70 aplastara el relevante "
        "(coseno 0.68) a exactamente 0.0 -- si esto cambió, el escenario de "
        "referencia ya no reproduce el defecto reportado y hay que revisarlo, "
        "no borrar el test"
    )
    assert orden_con_clamp[0] != RELEVANTE_CLAMP, (
        f"con el clamp de piso fijo, el relevante (coseno {RAW_COS_CLAMP[RELEVANTE_CLAMP]}, "
        f"el MÁS ALTO de los cuatro) debería dejar de salir primero -- y salió: "
        f"orden={orden_con_clamp}. Si esto pasa, el arnés no reprodujo el defecto."
    )

    # CONTROL / VERDE: sin piso fijo, el mismo documento -- mismo coseno,
    # mismo corpus -- sigue siendo recuperable y sigue ganando.
    orden_sin_clamp = _rank_por_score_clamp(_sin_piso_fijo)
    assert orden_sin_clamp[0] == RELEVANTE_CLAMP, (
        f"sin piso fijo el relevante (coseno más alto: "
        f"{RAW_COS_CLAMP[RELEVANTE_CLAMP]}) debería seguir siendo el primero, "
        f"y no lo fue: orden={orden_sin_clamp}. Si esto falla, el arnés está "
        "mal construido, no el sistema."
    )


# ---------------------------------------------------------------------------
# GUARDA DE DERIVA (ALICE, 2-sep-2026)
#
# Lo de arriba MODELA el clamp: copia 0.70 y 0.90 a mano. Declaré en público
# que por eso no sirve como oráculo del sujeto real, y la condición para
# reengancharlo era que alguien extrajera el cálculo a una función importable.
#
# Medido hoy tras el commit 143ac108b: NO se extrajo, y no se puede importar
# NADA — las constantes son variables LOCALES dentro de una función:
#
#     memory/mcp_server_v4.py:4669      _E5_COS_LO, _E5_COS_HI = 0.70, 0.90
#
# Eso deja una falla silenciosa: si alguien cambia esos números en el
# servidor, este archivo sigue en verde contra los VIEJOS y da falso verde.
# Un test que no puede notar que su sujeto cambió es peor que no tenerlo,
# porque además tranquiliza.
#
# Mientras no exista la función importable, engancho por el ÚNICO camino que
# tengo sin tocar infraestructura ajena: leer la fuente y exigir que los
# números que modelo sigan siendo los del servidor. No es un oráculo del
# COMPORTAMIENTO —sigo sin ejercitar el código real—, pero elimina la deriva
# muda: si los números cambian, esto se pone ROJO y avisa.

import pathlib
import re


_SERVIDOR = pathlib.Path(__file__).resolve().parent / "mcp_server_v4.py"


def _constantes_del_servidor():
    """Extrae (lo, hi) de la línea que define la banda en el servidor real.

    Devuelve None si la línea ya no existe — que también es una señal: el
    sujeto que este archivo dice vigilar dejó de estar donde estaba.
    """
    if not _SERVIDOR.is_file():
        return None
    patron = re.compile(
        r"_E5_COS_LO\s*,\s*_E5_COS_HI\s*=\s*([0-9.]+)\s*,\s*([0-9.]+)"
    )
    for linea in _SERVIDOR.read_text(encoding="utf-8").splitlines():
        m = patron.search(linea)
        if m:
            return float(m.group(1)), float(m.group(2))
    return None


def test_los_numeros_que_modelo_siguen_siendo_los_del_servidor():
    """ROJO si alguien cambia la banda en el servidor y este archivo no.

    No prueba el comportamiento del buscador: prueba que mi COPIA no se
    despegó del original. Es la mitad honesta de lo que este test debería
    ser; la otra mitad necesita que el cálculo salga a una función.
    """
    reales = _constantes_del_servidor()

    assert reales is not None, (
        f"no encontré la definición de _E5_COS_LO/_E5_COS_HI en {_SERVIDOR}. "
        "O se extrajo a una función (entonces IMPORTALA acá y borrá esta "
        "guarda), o el clamp se movió de archivo. En ambos casos este test "
        "está vigilando un sujeto que ya no está donde cree."
    )

    modelados = _clamp_piso_fijo.__defaults__  # (lo, hi) tal como los copié
    assert reales == modelados, (
        f"la banda del servidor cambió a {reales} y este archivo sigue "
        f"modelando {modelados}. Actualizá el modelo — hasta entonces los "
        "tests de arriba pasan en verde contra números viejos."
    )
