from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from soul_cognitive_core import (
    CoreGap,
    build_knowledge_profile,
    build_objective_summary,
    classify_epistemic_type,
    DEFAULT_SNAPSHOT_PATH,
    derive_gaps,
    is_open_status,
    public_product_surface,
    recommend_next,
    summarize_tasks,
)


def test_status_normalization() -> None:
    assert is_open_status("pending")
    assert is_open_status("in progress")
    assert is_open_status("")
    assert not is_open_status("completed")
    assert not is_open_status("closed")


def test_product_surface_classifier() -> None:
    assert public_product_surface("SOUL Core publico con RAG") == "SOUL_Core_public"
    assert public_product_surface("SEAL Core admin cockpit") == "SEAL_Core_private"
    assert public_product_surface("GTL facturacion SUNAT") == "GTL_vertical"
    assert public_product_surface("memoria causal") == "core_shared"


def test_epistemic_classifier() -> None:
    assert classify_epistemic_type("William dio luz verde al plan") == "decision"
    assert classify_epistemic_type("Regla inmutable: nunca activar creditos") == "rule"
    assert classify_epistemic_type("Creo que puede fallar por el puerto") == "hypothesis"
    assert classify_epistemic_type("pytest 5 passed y fix validado") == "fix"
    assert classify_epistemic_type("error de conexion") == "error"
    assert classify_epistemic_type("prefiero usar SOUL Core") == "preference"
    assert classify_epistemic_type("hecho verificado", source="belief", confidence=0.9) == "fact"
    assert classify_epistemic_type("puede ser", source="belief", confidence=0.4) == "hypothesis"


def test_summarize_tasks_counts_blocked_items() -> None:
    rows = [
        {"id": 1, "agent": "ADA", "status": "pending", "title": "normal", "description": ""},
        {"id": 2, "agent": "NEXUS", "status": "blocked", "title": "x", "description": ""},
        {"id": 3, "agent": "ADA", "status": "completed", "title": "done", "description": ""},
    ]

    summary = summarize_tasks(rows)  # type: ignore[arg-type]

    assert summary["open_count"] == 2
    assert summary["blocked_count"] == 1
    assert summary["by_agent"] == {"ADA": 1, "NEXUS": 1}


def test_objective_summary_separates_surfaces_and_evidence() -> None:
    tasks = {
        "top": [
            {"id": 1, "title": "SOUL Core RAG", "description": "", "priority": 9, "evidence": {}},
            {"id": 2, "title": "GTL facturacion", "description": "", "priority": 4, "evidence": {"cmd": "ok"}},
        ]
    }
    work = {"top": [{"id": 3, "title": "SEAL Core admin", "description": "", "priority": 8, "evidence": None}]}

    summary = build_objective_summary(tasks, work)

    assert summary["active_count"] == 3
    assert summary["by_surface"]["SOUL_Core_public"] == 1
    assert summary["by_surface"]["GTL_vertical"] == 1
    assert summary["by_surface"]["SEAL_Core_private"] == 1
    assert summary["missing_evidence_count"] == 2


def test_derive_gaps_flags_rag_and_services() -> None:
    state = {
        "tasks": {"blocked_count": 0, "open_count": 0},
        "work_ledger": {"blocked_count": 0, "open_count": 0},
        "latest_bench_run": {"id": 7, "failed": 0},
        "rag": {"has_document_index": False},
        "recent_decisions": {"count": 1},
        "objectives": {"missing_evidence_count": 0},
        "services": {"mcp": {"status": "error"}},
    }

    gaps = derive_gaps(state)
    ids = {gap.id for gap in gaps}

    assert "rag_document_index_missing" in ids
    assert "services_unhealthy" in ids


def test_build_knowledge_profile_turns_gaps_into_unknowns() -> None:
    gaps = [CoreGap("rag_missing", "high", "SOUL_Core_public", "no index", "crear index")]

    profile = build_knowledge_profile([], [], [], gaps, limit=5)

    assert profile["item_count"] == 0
    assert profile["unknowns"][0]["id"] == "rag_missing"


def test_recommend_next_orders_by_severity() -> None:
    gaps = [
        CoreGap("medium_gap", "medium", "core_shared", "m", "do medium"),
        CoreGap("high_gap", "high", "SEAL_Core_private", "h", "do high"),
    ]
    state = {"tasks": {"open_count": 1}}

    recs = recommend_next(gaps, state)  # type: ignore[arg-type]

    assert recs[0].priority > recs[1].priority
    assert recs[0].owner == "ADA+NEXUS"


def test_default_snapshot_path_is_diagnostic_json() -> None:
    assert DEFAULT_SNAPSHOT_PATH.name == "soul_cognitive_core_snapshot.json"
    assert "diagnostic" in DEFAULT_SNAPSHOT_PATH.parts
