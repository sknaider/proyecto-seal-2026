#!/usr/bin/env python3
"""El veneno excluido de la rama vectorial, probado CONTRA LA BASE.

Vive aparte de `test_rescate_exacto_del_ann_filtrado_v1.py` por una razón medida: la
arena de mutación corre en una copia bajo `/tmp` **sin acceso a PostgreSQL**, y un
brazo que necesita la base pone el control en rojo ahí. Separarlos deja que cada uno
corra donde puede: los rápidos en la arena, éste en el gate con `--execute`.

No se saltea si falta la base: levanta. Un test de seguridad que se salta en silencio
es peor que no tenerlo, porque figura en verde.
"""
from __future__ import annotations

import asyncio
import json as _json
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from soul_lite_adapter import PgVectorAdapter  # noqa: E402


def test_qa_negative_una_memoria_envenenada_NO_vuelve_ni_siendo_su_vecina_mas_cercana():
    """Se busca con el embebido de la propia memoria marcada: distancia cero.

    Es el caso más exigente posible — si algo la puede traer, es esta consulta. Con la
    cláusula neutralizada volvería primera; con la cláusula viva no vuelve nunca.
    """
    from db import get_pool

    async def correr():
        pool = await get_pool()
        async with pool.acquire() as conn:
            fila = await conn.fetchrow("""
                SELECT m.id, m.embedding, m.agent
                FROM memories m
                WHERE m.embedding IS NOT NULL AND EXISTS (
                    SELECT 1 FROM soul_v3.memory_poisoning_feedback p
                    WHERE p.memory_id = m.id
                      AND p.content_hash_sha256 = trim(m.content_hash_sha256)
                      AND p.decision IN ('review','quarantine_candidate'))
                LIMIT 1
            """)
        if fila is None:
            pytest.fail(
                "no hay ninguna memoria marcada como envenenada en la base: este brazo "
                "no puede probar nada y no se va a declarar verde por eso"
            )
        vec = fila["embedding"]
        if isinstance(vec, str):
            vec = _json.loads(vec)
        adapter = PgVectorAdapter(get_pool)
        filtro = type("Filter", (), {"must": [], "must_not": [], "should": []})()
        resp = await adapter.query_points("memories", list(vec), limit=5, query_filter=filtro)
        return int(fila["id"]), [p.id for p in resp.points]

    envenenada, devueltos = asyncio.run(correr())
    assert envenenada not in devueltos, (
        f"la memoria marcada {envenenada} volvió en la búsqueda vectorial: "
        f"la cláusula NOT EXISTS está escrita pero no filtra"
    )
    assert devueltos, "la consulta no devolvió NADA: el brazo no probó que se excluya, sólo que falla"
