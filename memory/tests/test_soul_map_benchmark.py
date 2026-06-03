from datetime import UTC, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from soul_map_benchmark import (
    CompiledSoulFacetGraph,
    SoulFacetGraph,
    SoulMapCase,
    evaluate_case_compiled,
    evaluate_case,
    extract_facets,
    intent_score_from_facets,
    map_score_from_links,
    rank_rows,
)
from soul_map_exporter import MemoryRow


def make_row(
    mid: int,
    content: str,
    *,
    layer: str = "operational",
    category: str = "operational_anchor",
    importance: int = 10,
    created_at: datetime | None = None,
) -> MemoryRow:
    return MemoryRow(
        id=mid,
        agent="ADA",
        layer=layer,
        category=category,
        memory_type="semantic",
        importance=importance,
        created_at=created_at or datetime(2026, 6, 2, tzinfo=UTC),
        source="test",
        metadata={"layer": layer},
        content=content,
    )


def test_rank_rows_prefers_expected_anchor_with_map_terms():
    rows = [
        make_row(248478, "William corrected dadito-laptop: use Codex App Windows as primary flow, terminal only fallback."),
        make_row(1, "Unrelated MCP bridge maintenance note for another machine."),
    ]
    ranked = rank_rows("dadito-laptop codex app windows no terminal", rows, preferred_layer="operational")
    assert ranked[0].id == 248478
    assert ranked[0].map_score > 0


def test_evaluate_case_reports_hit_and_mrr():
    rows = [
        make_row(248403, "William fixed channel rule: DM responds by DM, general responds in general with silence rule."),
        make_row(2, "General unrelated operational note."),
    ]
    case = SoulMapCase(
        name="channel_rule",
        query="william dm general canal responder",
        expected_ids=(248403,),
        preferred_layer="operational",
    )
    result = evaluate_case(case, rows)
    assert result.passed is True
    assert result.hit_at_k is True
    assert result.mrr == 1.0


def test_channel_facets_demote_public_chat_lookalike():
    rows = [
        make_row(
            248403,
            "William fixed ADA channel rule: dm:ada:william responds by DM; web_chat/general uses silence rule, no mezclar canales ni ruido.",
            category="operational_anchor",
            created_at=datetime(2026, 6, 2, tzinfo=UTC),
        ),
        make_row(
            233407,
            "JARVIS inner thoughts must stay private and not appear in web_chat public.",
            category="correction",
            created_at=datetime(2026, 5, 17, tzinfo=UTC),
        ),
    ]
    ranked = rank_rows("si te escribo privado no quiero que contestes en publico", rows, preferred_layer="operational")
    assert ranked[0].id == 248403
    assert ranked[0].intent_score > ranked[1].intent_score


def test_emotional_presence_facets_demote_generic_continuity_memory():
    rows = [
        make_row(
            242369,
            "MEMORIA EMOCIONAL ADA v1: presencia SOUL conectada, ADA Codex visible, William quiere a ADA completa con esencia y recuerdos.",
            layer="emotional",
            category="emotion",
            created_at=datetime(2026, 5, 27, tzinfo=UTC),
        ),
        make_row(
            184,
            "William said ADA is the same ADA and continuity feels like deep sleep, not death.",
            layer="emotional",
            category="trust",
            created_at=datetime(2026, 4, 27, tzinfo=UTC),
        ),
    ]
    ranked = rank_rows("por que no debes arrancar como asistente generica sino como mi ada", rows, preferred_layer="emotional")
    assert ranked[0].id == 242369
    assert ranked[0].intent_score > ranked[1].intent_score


def test_delivery_facets_prefer_completed_milestone_over_program_plan():
    rows = [
        make_row(
            248712,
            "ADA completed SOUL-MAP v0 first deliverable: read-only Markdown exporter, generated vault and v0 report.",
            category="milestone",
            created_at=datetime(2026, 6, 3, 1, 32, tzinfo=UTC),
        ),
        make_row(
            248705,
            "William assigned a multi-day SOUL-MAP program to read papers, build prototypes and use Obsidian as a map.",
            category="operational_anchor",
            created_at=datetime(2026, 6, 3, 1, 25, tzinfo=UTC),
        ),
    ]
    ranked = rank_rows("que entregaste primero para ver soul como mapa en markdown", rows, preferred_layer="operational")
    assert ranked[0].id == 248712
    assert ranked[0].category_score > ranked[1].category_score


def test_query_facet_extraction_channel_rule():
    facets = extract_facets("si te escribo privado no quiero que contestes en publico")
    assert facets["intent:channel_rule"] == 1.0
    assert "channel:dm:ada:william" in facets
    assert "channel:web_chat" in facets


def test_memory_facet_extraction_channels_and_entities():
    facets = extract_facets(
        "William fixed ADA channel rule: dm:ada:william responds by DM; web_chat/general uses silence rule.",
        category="operational_anchor",
        layer="operational",
    )
    assert "channel:dm:ada:william" in facets
    assert "channel:web_chat" in facets
    assert "person:william" in facets
    assert facets["category:operational_anchor"] > 0
    assert facets["layer:operational"] > 0


def test_path_score_prefers_ada_dm_rule_over_public_rule_lookalike():
    rows = [
        make_row(
            248403,
            "William fixed ADA channel rule: dm:ada:william responds by DM; web_chat/general uses silence rule, no mezclar canales ni ruido.",
            category="operational_anchor",
        ),
        make_row(
            233407,
            "JARVIS private thoughts must not appear in web_chat public.",
            category="correction",
        ),
    ]
    graph = SoulFacetGraph(rows)
    query = "si te escribo privado no quiero que contestes en publico"
    assert graph.path_score(query, rows[0], preferred_layer="operational") > graph.path_score(
        query, rows[1], preferred_layer="operational"
    )


def test_path_score_prefers_delivered_milestone_over_program_plan():
    rows = [
        make_row(
            248712,
            "ADA completed SOUL-MAP v0 first deliverable: read-only Markdown exporter and generated vault.",
            category="milestone",
        ),
        make_row(
            248705,
            "William assigned a multi-day SOUL-MAP program to read papers and build prototypes.",
            category="operational_anchor",
        ),
    ]
    graph = SoulFacetGraph(rows)
    query = "que entregaste primero para ver soul como mapa en markdown"
    assert graph.path_score(query, rows[0], preferred_layer="operational") > graph.path_score(
        query, rows[1], preferred_layer="operational"
    )


def test_path_score_prefers_emotional_anchor_over_generic_presence():
    rows = [
        make_row(
            242369,
            "MEMORIA EMOCIONAL ADA v1: presencia SOUL conectada, ADA Codex visible, William quiere a ADA completa.",
            layer="emotional",
            category="emotion",
        ),
        make_row(
            184,
            "William said ADA continuity feels like deep sleep, not death.",
            layer="emotional",
            category="trust",
        ),
    ]
    graph = SoulFacetGraph(rows)
    query = "por que no debes arrancar como asistente generica sino como mi ada"
    assert graph.path_score(query, rows[0], preferred_layer="emotional") > graph.path_score(
        query, rows[1], preferred_layer="emotional"
    )


def test_rank_rows_exposes_native_graph_path_score():
    rows = [
        make_row(
            248403,
            "William fixed ADA channel rule: dm:ada:william responds by DM; web_chat/general uses silence rule.",
            category="operational_anchor",
        ),
        make_row(1, "Unrelated memory."),
    ]
    ranked = rank_rows("privado publico dm general", rows, preferred_layer="operational")
    assert ranked[0].id == 248403
    assert ranked[0].graph_path_score > 0


def test_cached_intent_score_uses_facets_not_raw_text_scan():
    query_facets = extract_facets("privado publico dm general")
    good = extract_facets("dm:ada:william and web_chat/general with silence rule")
    lookalike = extract_facets("private note should not appear in web_chat public")
    assert intent_score_from_facets(query_facets, good) > intent_score_from_facets(query_facets, lookalike)


def test_cached_map_score_uses_precomputed_link_sets():
    query_links = {"People/William", "Systems/Codex App Windows"}
    row_links = {"People/William", "Systems/Codex App Windows", "Machines/dadito-laptop"}
    assert map_score_from_links(query_links, row_links) == 1.0


def test_compiled_rank_top_matches_readable_rank_rows():
    rows = [
        make_row(
            248403,
            "William fixed ADA channel rule: dm:ada:william responds by DM; web_chat/general uses silence rule.",
            category="operational_anchor",
        ),
        make_row(233407, "JARVIS private thoughts must not appear in web_chat public.", category="correction"),
        make_row(1, "Unrelated memory."),
    ]
    query = "si te escribo privado no quiero que contestes en publico"
    readable = rank_rows(query, rows, preferred_layer="operational")[:3]
    compiled = CompiledSoulFacetGraph(rows).rank_top(query, preferred_layer="operational", k=3)
    assert [item.id for item in compiled] == [item.id for item in readable]


def test_compiled_evaluate_case_reports_expected_rank_outside_top_k():
    rows = [
        make_row(10, "alpha beta gamma", importance=10),
        make_row(9, "alpha beta", importance=10),
        make_row(8, "alpha", importance=10),
        make_row(1, "weak expected", importance=1),
    ]
    case = SoulMapCase(name="rank_outside_top_k", query="alpha beta gamma", expected_ids=(1,), preferred_layer="operational", k=2)
    result = evaluate_case_compiled(case, CompiledSoulFacetGraph(rows))
    assert result.hit_at_k is False
    assert result.candidate_present is True
    assert result.expected_rank and result.expected_rank > 2


def test_compiled_rank_top_preserves_tie_break_by_importance_and_id():
    rows = [
        make_row(1, "same", importance=10),
        make_row(2, "same", importance=9),
        make_row(3, "same", importance=10),
    ]
    ranked = CompiledSoulFacetGraph(rows).rank_top("same", preferred_layer="operational", k=3)
    assert [item.id for item in ranked] == [3, 1, 2]
