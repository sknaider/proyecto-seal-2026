from datetime import UTC, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soul_cognitive_graph import CompiledSoulFacetGraph, extract_facets, shadow_rank_memories
from soul_map_exporter import MemoryRow


def make_row(mid: int, content: str, *, category: str = "operational_anchor", layer: str = "operational") -> MemoryRow:
    return MemoryRow(
        id=mid,
        agent="ADA",
        layer=layer,
        category=category,
        memory_type="semantic",
        importance=10,
        created_at=datetime(2026, 6, 3, tzinfo=UTC),
        source="test",
        metadata={"layer": layer},
        content=content,
    )


def test_extract_facets_channel_rule_query():
    facets = extract_facets("si te escribo privado no quiero que contestes en publico")
    assert "intent:channel_rule" in facets
    assert "channel:dm:ada:william" in facets
    assert "channel:web_chat" in facets


def test_compiled_graph_ranks_memory_without_expected_ids():
    rows = [
        make_row(
            248403,
            "William fixed ADA channel rule: dm:ada:william responds by DM; web_chat/general uses silence rule.",
        ),
        make_row(233407, "JARVIS private thoughts must not appear in web_chat public.", category="correction"),
    ]
    ranked = CompiledSoulFacetGraph(rows).rank_top(
        "si te escribo privado no quiero que contestes en publico",
        preferred_layer="operational",
        k=2,
    )
    assert [item.id for item in ranked] == [248403, 233407]


def test_shadow_rank_memories_is_read_only_rank_surface():
    rows = [
        make_row(1, "unrelated"),
        make_row(2, "ADA completed SOUL-MAP v0 first deliverable Markdown exporter vault.", category="milestone"),
    ]
    ranked = shadow_rank_memories("que entregaste primero para ver soul como mapa en markdown", rows, k=1)
    assert len(ranked) == 1
    assert ranked[0].id == 2
