import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dual_memory_governance import classify_layer, ensure_layer_metadata


def test_classify_layer_emotional_categories_and_content():
    assert classify_layer("emotion", "episodic", "x") == "emotional"
    assert classify_layer("identity", "episodic", "MEMORIA EMOCIONAL ADA") == "emotional"
    assert classify_layer("misc", "identity_emotional", "x") == "emotional"


def test_classify_layer_operational_categories_and_default():
    assert classify_layer("decision", "semantic", "x") == "operational"
    assert classify_layer("misc", "semantic", "MEMORIA OPERATIVA ADA") == "operational"
    assert classify_layer("misc", "semantic", "plain note") == "operational"


def test_operational_category_wins_when_content_mentions_emotional_layer():
    content = "MEMORIA OPERATIVA ADA v2: usar memoria emocional solo como ancla compacta."

    assert classify_layer("decision", "semantic", content) == "operational"


def test_ensure_layer_metadata_preserves_valid_layer():
    meta = ensure_layer_metadata(
        {"layer": "EMOTIONAL", "source": "unit"},
        category="decision",
        memory_type="semantic",
        content="plain",
        inferred_by="test",
    )
    assert meta["layer"] == "emotional"
    assert meta["source"] == "unit"
    assert "layer_inferred_by" not in meta


def test_ensure_layer_metadata_infers_missing_layer():
    meta = ensure_layer_metadata(
        {"source": "unit"},
        category="decision",
        memory_type="semantic",
        content="plain",
        inferred_by="memory_store",
        inferred_at="2026-05-29T00:00:00+00:00",
    )
    assert meta["layer"] == "operational"
    assert meta["layer_inferred_by"] == "memory_store"
    assert meta["layer_inferred_at"] == "2026-05-29T00:00:00+00:00"


def test_ensure_layer_metadata_replaces_invalid_layer():
    meta = ensure_layer_metadata(
        {"layer": "misc"},
        category="emotion",
        memory_type="core",
        content="MEMORIA EMOCIONAL ADA",
        inferred_by="memory_store",
    )
    assert meta["layer"] == "emotional"
    assert meta["layer_original_value"] == "misc"
