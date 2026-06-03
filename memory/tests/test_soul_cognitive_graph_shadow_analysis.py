from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyze_soul_cognitive_graph_shadow import analyze_rows, filter_rows_by_query_tokens, load_rows, render_text


def row(
    *,
    query: str = "secret query",
    rank_ms: float = 3.0,
    base: list[str] | None = None,
    shadow: list[str] | None = None,
) -> dict:
    return {
        "ts": "2026-06-03T00:00:00+00:00",
        "agent": "ADA",
        "query_preview": query,
        "rank_ms": rank_ms,
        "candidate_count": 5,
        "base_top_ids": base or ["1", "2", "3"],
        "shadow_top_ids": shadow or ["1", "2", "3"],
    }


def test_shadow_analysis_ready_when_thresholds_pass():
    rows = [row() for _ in range(30)]

    report = analyze_rows(rows)

    assert report["decision"]["ready_for_assist"] is True
    assert report["decision"]["status"] == "ready_for_assist"
    assert report["agreement"]["top1_rate"] == 1.0
    assert report["latency_ms"]["p95"] == 3.0


def test_shadow_analysis_blocks_small_sample_and_degrading_top1():
    rows = [
        row(base=["248478", "233139"], shadow=["246643", "248478"]),
        row(base=["248403", "230586"], shadow=["248403", "230586"]),
    ]

    report = analyze_rows(rows, min_rows=30)

    assert report["decision"]["ready_for_assist"] is False
    assert report["decision"]["status"] == "collect_more_shadow"
    assert "sample_count 2 < 30" in report["decision"]["reason"]
    assert report["agreement"]["top1_hits"] == 1
    assert report["changed_top1"][0]["base_top1"] == "248478"
    assert "query_preview" not in report["changed_top1"][0]


def test_shadow_analysis_blocks_latency_p95_over_threshold():
    rows = [row(rank_ms=5.0) for _ in range(28)] + [row(rank_ms=55.0), row(rank_ms=60.0)]

    report = analyze_rows(rows, max_p95_ms=20.0)

    assert report["decision"]["ready_for_assist"] is False
    assert "p95_ms" in report["decision"]["reason"]


def test_load_rows_filters_since_and_agent(tmp_path):
    path = tmp_path / "shadow.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"ts":"2026-06-03T00:00:00+00:00","agent":"ADA","rank_ms":1,"base_top_ids":["1"],"shadow_top_ids":["1"],"candidate_count":1}',
                '{"ts":"2026-06-03T01:00:00+00:00","agent":"JARVIS","rank_ms":1,"base_top_ids":["2"],"shadow_top_ids":["2"],"candidate_count":1}',
                '{"ts":"2026-06-03T02:00:00+00:00","agent":"ADA","rank_ms":1,"base_top_ids":["3"],"shadow_top_ids":["3"],"candidate_count":1}',
                "{bad json",
            ]
        ),
        encoding="utf-8",
    )

    rows, invalid = load_rows(path, since_ts="2026-06-03T00:30:00+00:00", agent="ADA")

    assert invalid == 1
    assert [item["base_top_ids"][0] for item in rows] == ["3"]


def test_text_report_hides_query_preview_by_default():
    report = analyze_rows(
        [row(query="dm secreto no imprimir", base=["1"], shadow=["2"])],
        min_rows=1,
        min_top1_agreement=0.0,
        min_shadow_top1_in_base_topk=0.0,
    )

    text = render_text(report, Path("shadow.jsonl"), None)

    assert "dm secreto no imprimir" not in text
    assert "base=1 shadow=2" in text


def test_filter_rows_by_min_query_tokens():
    rows = [
        row(query="codex"),
        row(query="codex app windows"),
        row(query="dm regla general"),
        {**row(query=""), "query_preview": None, "query_token_count": 3},
    ]

    filtered = filter_rows_by_query_tokens(rows, 2)

    assert [item.get("query_preview") for item in filtered] == [
        "codex app windows",
        "dm regla general",
        None,
    ]
