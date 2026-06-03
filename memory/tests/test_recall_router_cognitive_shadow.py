from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import recall_router


def make_hit(mid: str, content: str, *, source: str = "memories", category: str = "operational_anchor") -> dict:
    return {
        "source": source,
        "id": mid,
        "agent": "ADA",
        "channel": None,
        "created_at": "2026-06-03T00:00:00+00:00",
        "category": category,
        "content": content,
        "summary": None,
        "score_semantic": 0.0,
        "score_keyword": 0.0,
        "score_recency": 1.0,
        "score_importance": 1.0,
        "score_authority": 1.0,
        "score_final": 0.5,
    }


def test_shadow_flag_off_does_nothing(monkeypatch):
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_SHADOW_ENABLED", False)
    result = recall_router._run_cognitive_graph_shadow(
        "ADA",
        "privado publico",
        [make_hit("248403", "dm:ada:william web_chat general silence")],
    )
    assert result is None


def test_shadow_flag_on_returns_payload_without_changing_ranked(monkeypatch, tmp_path):
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_SHADOW_ENABLED", True)
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_SHADOW_LOG_QUERY", False)
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_SHADOW_LOG", tmp_path / "shadow.jsonl")
    ranked = [
        make_hit("233407", "JARVIS private thoughts must not appear in web_chat public.", category="correction"),
        make_hit("248403", "ADA rule: dm:ada:william and web_chat/general silence rule."),
    ]
    before = [hit["id"] for hit in ranked]

    result = recall_router._run_cognitive_graph_shadow("ADA", "si te escribo privado no quiero publico", ranked)

    assert [hit["id"] for hit in ranked] == before
    assert result is not None
    assert result["base_top_ids"][:2] == before
    assert result["shadow_top_ids"][0] == "248403"
    assert result["query_token_count"] > 0
    assert result["query_hash"]
    assert "query_preview" not in result
    assert (tmp_path / "shadow.jsonl").exists()


def test_shadow_query_preview_requires_explicit_debug_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_SHADOW_ENABLED", True)
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_SHADOW_LOG_QUERY", True)
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_SHADOW_LOG", tmp_path / "shadow.jsonl")

    result = recall_router._run_cognitive_graph_shadow(
        "ADA",
        "debug visible query",
        [make_hit("248403", "ADA rule: dm:ada:william and web_chat/general silence rule.")],
    )

    assert result is not None
    assert result["query_preview"] == "debug visible query"


def test_shadow_ignores_non_memory_hits(monkeypatch, tmp_path):
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_SHADOW_ENABLED", True)
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_SHADOW_LOG", tmp_path / "shadow.jsonl")

    result = recall_router._run_cognitive_graph_shadow("ADA", "anything", [make_hit("r1", "rule", source="rules")])

    assert result is None
    assert not (tmp_path / "shadow.jsonl").exists()


def test_shadow_failure_does_not_raise(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("shadow failed")

    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_SHADOW_ENABLED", True)
    monkeypatch.setattr(recall_router, "shadow_rank_memories", boom)

    result = recall_router._run_cognitive_graph_shadow(
        "ADA",
        "privado publico",
        [make_hit("248403", "dm:ada:william web_chat")],
    )

    assert result is None


def test_assist_default_shadow_mode_does_not_reorder(monkeypatch):
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_MODE", "shadow")
    ranked = [
        make_hit("1", "unrelated"),
        make_hit("2", "ADA completed SOUL-MAP v0 first deliverable Markdown exporter vault.", category="milestone"),
    ]

    assisted = recall_router._apply_cognitive_graph_assist("soul map markdown", ranked, limit=2)

    assert [hit["id"] for hit in assisted] == ["1", "2"]


def test_assist_reorders_only_memory_hits_when_enabled(monkeypatch):
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_MODE", "assist")
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_ASSIST_MIN_QUERY_TOKENS", 2)
    ranked = [
        make_hit("rule-1", "active rule stays in place", source="rules"),
        make_hit("1", "unrelated"),
        make_hit("2", "ADA completed SOUL-MAP v0 first deliverable Markdown exporter vault.", category="milestone"),
    ]

    assisted = recall_router._apply_cognitive_graph_assist("soul map markdown", ranked, limit=3)

    assert [hit["source"] for hit in assisted] == ["rules", "memories", "memories"]
    assert assisted[0]["id"] == "rule-1"
    assert [hit["id"] for hit in assisted[1:]] == ["2", "1"]


def test_assist_ignores_short_queries(monkeypatch):
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_MODE", "assist")
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_ASSIST_MIN_QUERY_TOKENS", 2)
    ranked = [
        make_hit("1", "unrelated"),
        make_hit("2", "ADA completed SOUL-MAP v0 first deliverable Markdown exporter vault.", category="milestone"),
    ]

    assisted = recall_router._apply_cognitive_graph_assist("codex", ranked, limit=2)

    assert [hit["id"] for hit in assisted] == ["1", "2"]


def test_assist_does_not_demote_critical_top_memory(monkeypatch):
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_MODE", "assist")
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_ASSIST_MIN_QUERY_TOKENS", 2)
    ranked = [
        make_hit("248403", "ADA critical rule: dm:ada:william and web_chat/general silence rule."),
        make_hit("2", "unrelated dm content that graph might otherwise rank differently", category="milestone"),
    ]

    assisted = recall_router._apply_cognitive_graph_assist("dm regla general", ranked, limit=2)

    assert [hit["id"] for hit in assisted] == ["248403", "2"]


def test_assist_failure_falls_back_to_base(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("assist failed")

    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_MODE", "assist")
    monkeypatch.setattr(recall_router, "COGNITIVE_GRAPH_ASSIST_MIN_QUERY_TOKENS", 2)
    monkeypatch.setattr(recall_router, "shadow_rank_memories", boom)
    ranked = [
        make_hit("1", "unrelated"),
        make_hit("2", "ADA completed SOUL-MAP v0 first deliverable Markdown exporter vault.", category="milestone"),
    ]

    assisted = recall_router._apply_cognitive_graph_assist("soul map markdown", ranked, limit=2)

    assert [hit["id"] for hit in assisted] == ["1", "2"]
