from pathlib import Path
import importlib.util


PATH = Path(__file__).parents[1] / "tools" / "soul_live_retrieval_shadow.py"
SPEC = importlib.util.spec_from_file_location("soul_live_retrieval_shadow", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_query_is_compact_and_does_not_dump_memory():
    content = "Decisión técnica: usar pgvector canónico para recuperación semántica y evitar rutas obsoletas. " * 5
    query = MODULE.make_query(content, "decision")
    assert query.startswith("recupera decision")
    assert len(query) < len(content) / 2
    assert "pgvector" in query


def test_percentile():
    assert MODULE.percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert MODULE.percentile([1, 2, 3, 4, 5], 0.8) == 4.2
