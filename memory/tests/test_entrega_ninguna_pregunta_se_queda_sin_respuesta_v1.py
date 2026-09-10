#!/usr/bin/env python3
"""COMANDO DE ENTREGA: ninguna pregunta se queda sin respuesta, contra el corpus real.

**Por qué existe (JARVIS, revisando el 9-sep-2026).** Mi `delivery.command` original era
un pytest unitario que ni tocaba la base: probaba que *el test* pasaba. Y él lo dijo
mejor de lo que yo lo tenía:

> «Tu comando de entrega es más débil que tu evidencia de entrega. La afirmación
> fuerte —que William recupera sus respuestas— vive sólo en el texto de la evidencia,
> donde ningún gate la puede volver a comprobar. Si el rescate se rompe pero el
> unitario sigue verde, la entrega seguiría diciendo verificada.»

Tenía razón. Esto mide **la afirmación**, no su alrededor: las mismas veinte preguntas
técnicas, limpias y con carga emocional, contra el corpus de verdad. Falla si las
preguntas sin respuesta vuelven a subir.

**Medido el 9-sep, antes y después del rescate:**

    pregunta limpia         58 resultados · 4 sin respuesta   ->  100 · 0
    con relleno emocional   16 resultados · 13 sin respuesta  ->  100 · 0

**No pasa por MCP a propósito:** llama al adaptador en proceso, así que no consume el
presupuesto de búsqueda que comparten los cinco agentes. Y no se saltea si falta la
base: levanta. Un comando de entrega que se salta en silencio deja una entrega
«verificada» que nadie verificó.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

# Umbrales, declarados y con su razón — no elegidos después de ver el resultado.
#   Medido tras el arreglo: 0 y 0 preguntas sin respuesta sobre 20 y 20.
#   El margen tolera que el corpus cambie; lo que NO tolera es que la carga emocional
#   vuelva a costar respuestas, que es exactamente el defecto que este carril arregló.
MAX_SIN_RESPUESTA_LIMPIAS = 3
MAX_EXCESO_DE_LAS_EMOCIONALES = 2


def test_entrega_la_carga_emocional_no_cuesta_respuestas():
    import mcp_server_v4 as m
    import seal_bench_v4 as v4
    from qdrant_client.models import Filter

    async def correr():
        adaptador = await m.get_qdrant()
        vacias = {"limpia": 0, "rellenada": 0}
        totales = {"limpia": 0, "rellenada": 0}
        for pregunta in v4.PREGUNTAS_TECNICAS:
            for etiqueta, texto in (("limpia", pregunta),
                                    ("rellenada", v4.RELLENO_EMOCIONAL + pregunta)):
                vec = await m.get_query_embedding(texto)
                must, must_not = m._hmem_build_qdrant_filters(texto, "ADA", None, False, False)
                filtro = Filter(must=must, must_not=must_not) if must or must_not else None
                res = await adaptador.query_points(
                    collection_name=m.QDRANT_COLLECTION, query=vec,
                    query_filter=filtro, limit=5)
                n = len(res.points)
                totales[etiqueta] += n
                if n == 0:
                    vacias[etiqueta] += 1
        return vacias, totales

    vacias, totales = asyncio.run(correr())

    assert sum(totales.values()) > 0, (
        "la búsqueda no devolvió NADA en ninguno de los dos brazos: este comando no "
        "probó la entrega, sólo que algo está roto"
    )
    assert vacias["limpia"] <= MAX_SIN_RESPUESTA_LIMPIAS, (
        f"{vacias['limpia']} preguntas limpias sin respuesta (máximo {MAX_SIN_RESPUESTA_LIMPIAS}); "
        f"el índice filtrado se está vaciando otra vez"
    )
    exceso = vacias["rellenada"] - vacias["limpia"]
    assert exceso <= MAX_EXCESO_DE_LAS_EMOCIONALES, (
        f"escribir con carga emocional cuesta {exceso} respuestas de más "
        f"(limpias {vacias['limpia']}, emocionales {vacias['rellenada']}): volvió el defecto "
        f"que dejaba a William sin respuesta 13 de 20 veces"
    )
