"""D2 (auditoría FABLE 2-sep-2026) medido por JARVIS: 40/40 consultas reales de
recall_audit devolvían 0 filas desde _recall_memories porque websearch_to_tsquery
con texto libre es un AND de todos los términos. Contrato nuevo:
  1. si el AND estricto no devuelve nada, se reintenta con OR de términos;
  2. ts_rank usa normalización 32 (rank/(rank+1), acotado a [0,1));
  3. el ORDER BY pondera relevancia por importancia — importancia no es la llave.
"""
import asyncio
from datetime import UTC, datetime

import pytest

try:
    import recall_router as rr
except ModuleNotFoundError:
    # H1 (revisión NEXUS 2-sep): el argv del manifest corre sin PYTHONPATH. Sólo si el
    # módulo NO es importable agregamos memory/ al path — así una copia puesta antes en
    # PYTHONPATH (arnés de mutantes) sigue ganando y no la sombreamos con el archivo vivo.
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import recall_router as rr


class FakePool:
    def __init__(self, and_rows, or_rows):
        self.queries: list[tuple[str, tuple]] = []
        self._and, self._or = and_rows, or_rows

    async def fetch(self, query, *args):
        self.queries.append((query, args))
        q_text = args[0]
        return self._or if " OR " in q_text else self._and


def _row(i, imp, kw):
    return {"id": i, "agent": "JARVIS", "category": "decision", "content": f"mem {i}",
            "importance": imp, "created_at": datetime.now(UTC), "scope": "private", "kw_rank": kw}


def test_or_terms_builder_drops_short_tokens_and_caps():
    # 12 términos útiles distintos: el tope tiene que MORDER (mutante or_cap_removed
    # sobrevivió 2-sep 12:07 con una query de 7 términos — igualdad estricta, no <=)
    q = ("boot — recuperar contexto activo y decisiones recientes: recall router "
         "fallback stopwords timeout reboot spark cluster supervisor")
    terms = rr._bm25_or_terms(q).split(" OR ")
    assert len(terms) == rr._BM25_OR_MAX_TERMS == 8
    assert "y" not in terms                  # tokens de <3 chars fuera
    assert terms[:3] == ["boot", "recuperar", "contexto"]   # orden de aparición


def test_falls_back_to_or_when_and_is_empty():
    pool = FakePool(and_rows=[], or_rows=[_row(1, 7, 0.3)])
    hits = asyncio.run(rr._recall_memories("hermes proyecto agente hermes-agent", "JARVIS", pool, set(), 4))
    assert [h["id"] for h in hits] == ["1"]
    assert len(pool.queries) == 2, "debe consultar AND y luego OR"
    assert " OR " not in pool.queries[0][1][0]
    assert " OR " in pool.queries[1][1][0]


def test_and_hit_does_not_trigger_or():
    pool = FakePool(and_rows=[_row(2, 5, 0.9)], or_rows=[_row(1, 10, 0.1)])
    hits = asyncio.run(rr._recall_memories("test desde DADITOGAMER", "JARVIS", pool, set(), 4))
    assert [h["id"] for h in hits] == ["2"]
    assert len(pool.queries) == 1


def test_sql_ranks_by_relevance_weighted_by_importance():
    pool = FakePool(and_rows=[_row(3, 9, 0.5)], or_rows=[])
    asyncio.run(rr._recall_memories("cluster spark", "JARVIS", pool, set(), 4))
    sql = pool.queries[0][0]
    assert ", 32)" in sql, "ts_rank sin normalización (default 0) no penaliza longitud"
    order = sql.split("ORDER BY", 1)[1]
    assert not order.lstrip().startswith("importance"), "importancia como llave, no como peso"
    assert "importance" in order and "ts_rank" in order


def test_long_query_goes_straight_to_or():
    pool = FakePool(and_rows=[_row(9, 10, 0.9)], or_rows=[_row(4, 6, 0.2)])
    q = "boot tras reinicio por CLI recuperar frentes abiertos y decisiones recientes"
    hits = asyncio.run(rr._recall_memories(q, "JARVIS", pool, set(), 4))
    assert len(pool.queries) == 1, "con >5 tokens el AND es inútil (0/40 medido): una sola consulta"
    assert " OR " in pool.queries[0][1][0]
    assert [h["id"] for h in hits] == ["4"]


def test_memories_lane_timeout_covers_the_or_query():
    assert rr._LABEL_TIMEOUT["memories"] >= 0.5, "0.35 s cortaba la consulta OR (~220-300 ms mediana + red)"


def test_or_terms_builder_drops_stopwords_so_the_or_slots_discriminate():
    # 2-sep 12:00: EXPLAIN bajo mcp_runtime_jarvis — 'del','las','uno','once' entraban en el OR
    # y el bitmap OR traía 35.175 filas de las que RLS descartaba 25.670 (993 ms en frío).
    q = "hallazgo del reboot del Spark a las once cincuenta y uno sin secuencia de apagado"
    terms = rr._bm25_or_terms(q).split(" OR ")
    for sw in ("del", "las", "uno", "sin"):
        assert sw not in terms, f"stopword {sw!r} ocupa un lugar del OR: {terms}"
    assert "hallazgo" in terms and "reboot" in terms and "spark" in terms
    assert len(terms) <= rr._BM25_OR_MAX_TERMS


def test_memories_lane_timeout_covers_the_cold_or_query():
    # Medido 2-sep 11:58: primera consulta OR tras el reboot = 993 ms; con 0.60 el tool
    # devolvió "No relevant context" y 8 min después la misma query trajo 5 memorias.
    assert rr._LABEL_TIMEOUT["memories"] >= 1.0, "el timeout debe cubrir la consulta OR en frío (~1 s)"


# ── brazo qa_control: casos que NO deben cambiar con el contrato nuevo ──────────

def test_control_short_query_keeps_strict_and_single_query():
    # <=5 tokens con hit: una sola consulta AND, ningún OR (control de no-ensanchamiento)
    pool = FakePool(and_rows=[_row(7, 6, 0.4)], or_rows=[_row(8, 10, 0.9)])
    hits = asyncio.run(rr._recall_memories("cluster spark cuda", "JARVIS", pool, set(), 4))
    assert [h["id"] for h in hits] == ["7"]
    assert len(pool.queries) == 1 and " OR " not in pool.queries[0][1][0]


def test_control_or_builder_never_returns_empty_string():
    # sólo stopwords / tokens cortos: el builder devuelve la query original, nunca ''
    for q in ("del las uno", "y o a", ""):
        assert rr._bm25_or_terms(q) == q


def test_control_or_builder_is_idempotent_and_dedups():
    terms = rr._bm25_or_terms("spark spark SPARK cluster cluster")
    assert terms == "spark OR cluster"
    assert rr._bm25_or_terms(terms) == terms
