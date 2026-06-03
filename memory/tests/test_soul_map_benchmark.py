from datetime import UTC, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from soul_map_benchmark import SoulMapCase, evaluate_case, rank_rows
from soul_map_exporter import MemoryRow


def make_row(mid: int, content: str, *, layer: str = "operational", importance: int = 10) -> MemoryRow:
    return MemoryRow(
        id=mid,
        agent="ADA",
        layer=layer,
        category="operational_anchor",
        memory_type="semantic",
        importance=importance,
        created_at=datetime(2026, 6, 2, tzinfo=UTC),
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
