from datetime import UTC, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from soul_map_benchmark import SoulMapCase, evaluate_case, rank_rows
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
