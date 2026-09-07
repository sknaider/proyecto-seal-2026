"""Carril de lecciones de active_recall (JARVIS, 2-sep-2026). Contrato:
  1. consulta SOLO el corpus metadata.source_kind='claude_memory_file', scope team/public;
  2. excluye ids ya traídos por el carril semántico;
  3. descarta por umbral de similitud; 4. formatea una sección propia con archivo y autor;
  5. sin lecciones -> sección vacía (no se agrega nada)."""
import asyncio
import json
import os
import sys
from pathlib import Path

# El servidor exige un principal de DB al importar (db.resolve_db_url, fail-closed). Para que el
# argv del manifest corra sin conjuros (H1 de NEXUS, 2-sep), si no hay SEAL_DB_URL* usamos el DSN
# de runtime MCP del agente (mínimo privilegio, nunca superusuario). Igual que el arnés de mutantes.
if not os.environ.get("SEAL_DB_URL") and not os.environ.get("SEAL_DB_URL_FILE"):
    _agent = os.environ.get("SEAL_AGENT", "jarvis").lower()
    os.environ["SEAL_DB_URL_FILE"] = str(Path.home() / ".config" / "seal" / "mcp_agents" / f"{_agent}.dsn")
try:
    import mcp_server_v4 as server
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import mcp_server_v4 as server


class FakePool:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self.rows


def _row(i, score, desc="lección", author="unknown", file="reference_x_20260902.md", md_as_str=False):
    md = {"source_kind": "claude_memory_file", "type": "reference", "author": author, "file": file, "description": desc}
    return {"id": i, "agent": "JARVIS", "category": "insight", "importance": 7,
            "metadata": json.dumps(md) if md_as_str else md, "score": score}


def test_sql_targets_only_the_lessons_corpus_and_excludes_given_ids():
    pool = FakePool([_row(1, 0.9)])
    asyncio.run(server._recall_lessons_lane(pool, [0.1, 0.2], exclude_ids=[5, 6], limit=3))
    sql, args = pool.calls[0]
    assert "metadata->>'source_kind' = 'claude_memory_file'" in sql
    assert "scope IN ('team', 'public')" in sql and "invalid_at IS NULL" in sql
    assert args[1] == [5, 6] and args[2] == 3
    assert args[0].startswith("[") and args[0].endswith("]")


def test_threshold_filters_low_similarity_and_keeps_order():
    pool = FakePool([_row(1, 0.91, "alta"), _row(2, 0.79, "baja"), _row(3, 0.85, "media", md_as_str=True)])
    out = asyncio.run(server._recall_lessons_lane(pool, [0.0], min_score=0.80))
    assert [l["id"] for l in out] == [1, 3], "0.79 queda fuera; metadata como str también se parsea"
    assert out[1]["description"] == "media"
    # Conductual sobre el umbral POR DEFECTO (ALICE 2-sep): una lección a 0.83 NO debe salir sin pasar
    # min_score (0.83 = p50-p90 de la similitud NULA con e5, medido n=900). Un mutante 0.85->0.80 la
    # dejaría pasar y muere por EFECTO, no por leer la constante.
    pool2 = FakePool([_row(4, 0.83, "azar")])
    assert asyncio.run(server._recall_lessons_lane(pool2, [0.0])) == []
    pool3 = FakePool([_row(5, 0.86, "real")])
    assert [l["id"] for l in asyncio.run(server._recall_lessons_lane(pool3, [0.0]))] == [5]


def test_section_format_has_file_author_and_similarity():
    sec = server._format_lessons_section([{"id": 7, "score": 0.873, "type": "reference", "author": "ALICE",
                                           "file": "reference_a_20260901.md", "description": "el artefacto que leés no es el que corre"}])
    assert sec.startswith("## Lecciones del equipo")
    assert "[lesson #7, reference, autor=ALICE, sim=0.87]" in sec
    assert "→ `reference_a_20260901.md`" in sec


def test_empty_lessons_produce_no_section():
    assert server._format_lessons_section([]) == ""
    pool = FakePool([])
    assert asyncio.run(server._recall_lessons_lane(pool, [0.0])) == []


def test_control_flag_and_limits_have_sane_defaults():
    assert server._LESSONS_LANE_LIMIT == 3
    assert 0.7 <= server._LESSONS_LANE_MIN_SCORE <= 0.9   # rango de cordura; el valor lo vigila el test conductual
    assert isinstance(server._LESSONS_LANE_ENABLED, bool)
