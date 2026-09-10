#!/usr/bin/env python3
"""El rescate exacto cuando el índice filtrado se vacía — brazos QA complementarios.

**Qué lo generó (ADA, 9-sep-2026).** La búsqueda vectorial usa un índice HNSW. Cuando
además hay un filtro —«sólo memorias de ADA»— el índice recorre un vecindario y el
filtro se lleva puesto todo lo que encontró: devuelve **cero**, no pocos. Medido
aislado, mismo filtro, mismo agente, con 11.302 memorias disponibles:

    pregunta limpia         sin filtro 10   con filtro 5
    con relleno emocional   sin filtro 10   con filtro 0   <- se vacia

En la literatura se llama *recall cliff* del ANN filtrado. Efecto sobre William, que
escribe con carga emocional: 13 de 20 preguntas técnicas se quedaban sin ninguna
respuesta. Y la versión limpia también perdía: traía 58 de 100 posibles.

**ESTE ARCHIVO NO ES LA ESPECIFICACIÓN.** La especificación es
`memory/test_soul_lite_adapter_filtered_fallback.py::test_filtered_ann_starvation_uses_exact_fallback`,
que ya existía, estaba en ROJO y **no fue tocado**: alguien había diagnosticado el bug
y escrito el test antes que yo. Modificarlo para que pasara habría sido firmarme la
tarea a mí misma. Acá van sólo los brazos que ese test no cubre.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from soul_lite_adapter import PgVectorAdapter  # noqa: E402

FILA = {
    "id": 1, "content": "una memoria", "agent": "ADA", "category": "fact",
    "importance": 8, "source": "test", "created_at": None, "valence": None,
    "arousal": None, "dominance": None, "scope": "private",
    "confidence_score": 1.0, "metadata": {}, "score": 0.9,
}


class _Tx:
    def __init__(self, conn, falla_en=None):
        self.conn, self.falla_en = conn, falla_en

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class Conn:
    """Conexión de mentira: cuenta fetches, guarda los SET y puede fallar a pedido."""

    def __init__(self, devoluciones, falla_en_comando=None):
        self.devoluciones = list(devoluciones)   # filas por cada fetch, en orden
        self.falla_en_comando = falla_en_comando
        self.comandos: list[str] = []
        self.fetches = 0
        self.sqls: list[str] = []

    def transaction(self):
        return _Tx(self)

    async def execute(self, sql):
        self.comandos.append(sql)
        if self.falla_en_comando and self.falla_en_comando in sql:
            raise RuntimeError('unrecognized configuration parameter')

    async def fetch(self, sql, *params):
        self.sqls.append(sql)
        self.fetches += 1
        n = self.devoluciones.pop(0) if self.devoluciones else 0
        return [dict(FILA, id=i) for i in range(n)]


class _Acquire:
    def __init__(self, conn): self.conn = conn
    async def __aenter__(self): return self.conn
    async def __aexit__(self, *_): return False


class Pool:
    def __init__(self, conn): self.conn = conn
    def acquire(self): return _Acquire(self.conn)


async def _pool_de(conn):
    return Pool(conn)


def _consultar(conn, limit=5):
    adapter = PgVectorAdapter(lambda: _pool_de(conn))
    filtro = type("Filter", (), {"must": [], "must_not": [], "should": []})()
    return asyncio.run(adapter.query_points("memories", [0.0, 1.0], limit=limit,
                                            query_filter=filtro))


# ── qa_positive — el rescate se dispara y trae lo que el índice no pudo ──────

def test_qa_positive_el_indice_vacio_se_rescata():
    """El caso de William: el filtro deja el plato vacío y el rescate lo llena."""
    conn = Conn([0, 5])
    resp = _consultar(conn)
    assert len(resp.points) == 5
    assert conn.fetches == 2


def test_qa_positive_tambien_rescata_cuando_trae_de_MENOS():
    """La versión limpia también perdía: 58 de 100. No hace falta llegar a cero."""
    conn = Conn([3, 5])
    resp = _consultar(conn)
    assert len(resp.points) == 5
    assert conn.fetches == 2


# ── qa_control — el camino normal no paga el precio del rescate ──────────────

def test_qa_control_si_el_indice_ya_trajo_todo_NO_hay_segunda_busqueda():
    """Sin esto, cada consulta pagaría un barrido exacto y el arreglo sería peor que el bug."""
    conn = Conn([5])
    resp = _consultar(conn)
    assert len(resp.points) == 5
    assert conn.fetches == 1, "se hizo un barrido exacto sin necesidad"
    assert conn.comandos == [], f"se tocaron parámetros sin necesidad: {conn.comandos}"


def test_qa_control_el_rescate_apaga_el_indice_de_verdad():
    conn = Conn([0, 5])
    _consultar(conn)
    juntos = " ".join(conn.comandos)
    assert "enable_indexscan = off" in juntos
    assert "hnsw.iterative_scan" in juntos


# ── qa_negative — el rescate no puede empeorar nada ──────────────────────────

def test_qa_negative_el_rescate_NUNCA_devuelve_menos_que_el_indice():
    """Si el barrido exacto trajera menos, quedarse con lo peor sería un arreglo que rompe."""
    conn = Conn([4, 1])
    resp = _consultar(conn)
    assert len(resp.points) == 4, "el rescate se quedó con el resultado peor"


def test_qa_negative_un_pgvector_viejo_no_deja_el_cero():
    """`hnsw.iterative_scan` no existe antes de pgvector 0.8 y aborta la transacción.

    Sin el segundo intento, un servidor viejo se quedaría con el cero del índice: el
    arreglo no funcionaría justo donde el bug es peor.
    """
    conn = Conn([0, 5], falla_en_comando="hnsw.iterative_scan")
    resp = _consultar(conn)
    assert len(resp.points) == 5
    assert any("enable_indexscan = off" in c for c in conn.comandos)


def test_qa_negative_si_todo_falla_no_se_inventan_resultados():
    conn = Conn([0, 0])
    resp = _consultar(conn)
    assert resp.points == []


# ── unit — la exclusión de memorias envenenadas, que faltaba en esta rama ────

def test_unit_la_busqueda_vectorial_excluye_las_memorias_envenenadas():
    """Medido: 66 memorias que la rama léxica descarta y la vectorial devolvía.

    Dos caminos hacia la misma memoria no pueden tener distinta idea de qué es seguro.
    """
    conn = Conn([5])
    _consultar(conn)
    sql = conn.sqls[0]
    assert "memory_poisoning_feedback" in sql
    assert "quarantine_candidate" in sql
    assert "content_hash_sha256" in sql
    assert "NOT EXISTS" in sql


def test_unit_el_rescate_usa_el_MISMO_sql_que_el_camino_rapido():
    """Si el rescate tuviera su propio SQL, podría perder un filtro de privacidad."""
    conn = Conn([0, 5])
    _consultar(conn)
    assert conn.sqls[0] == conn.sqls[1]


def test_unit_el_patron_de_veneno_es_el_de_la_casa():
    """Se copió de `active_recall_hook.py`, no se inventó uno nuevo."""
    adaptador = open(os.path.join(RAIZ, "soul_lite_adapter.py")).read()
    hook = open(os.path.join(RAIZ, "active_recall_hook.py")).read()
    for pieza in ("poison.memory_id", "poison.content_hash_sha256",
                  "poison.decision IN ('review','quarantine_candidate')"):
        assert pieza in adaptador, f"falta {pieza} en el adaptador"
        assert pieza in hook, f"{pieza} ya no está en el hook: el patrón se separó"


def test_unit_la_clausula_del_veneno_no_esta_neutralizada():
    """Un mutante dejó el bloque `NOT EXISTS` entero y visible con un `WHERE false`.

    Los asserts que sólo buscan los nombres pasaron: la cláusula estaba escrita y no
    filtraba nada. Éste mira que el predicado no sea siempre falso. Es una compuerta
    de forma, no de comportamiento — el brazo que prueba el EFECTO vive en
    `test_veneno_en_la_rama_vectorial_contra_la_base_v1.py`, que necesita base de datos
    y por eso no puede correr en la arena de mutación.
    """
    conn = Conn([5])
    _consultar(conn)
    sub = conn.sqls[0].split("NOT EXISTS")[1].split(")")[0].lower()
    for muerto in ("false", "1=0", "1 = 0"):
        assert muerto not in sub, f"el predicado del veneno esta neutralizado con {muerto!r}"
    assert "poison.memory_id = memories.id" in sub
