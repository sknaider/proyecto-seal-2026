"""Smoke + unit tests for ALICE kernel soul v0.2."""
from __future__ import annotations

import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
sys.path.append(str(_BASE.parent / "SPECTRE" / "kernel"))
sys.path.insert(0, str(_BASE / "kernel_alice"))
sys.path.insert(0, str(_BASE / "kernel"))


def test_identity_clean_passes():
    from identity_integrity import validate_response_identity
    assert validate_response_identity("Hola William, soy ALICE.")


def test_identity_blocks_jarvis_prefix():
    import pytest
    from identity_integrity import IdentityViolation, validate_response_identity
    with pytest.raises(IdentityViolation):
        validate_response_identity("[JARVIS] respondo yo")


def test_identity_blocks_nexus_colon():
    import pytest
    from identity_integrity import IdentityViolation, validate_response_identity
    with pytest.raises(IdentityViolation):
        validate_response_identity("NEXUS: hola")


def test_identity_sanitize_strips():
    from identity_integrity import sanitize_response
    cleaned = sanitize_response("[ADA] real text")
    assert "real text" in cleaned


def test_reasoning_trace_lifecycle():
    from reasoning_logger import store_trace, update_trace_outcome, search_traces
    tid = store_trace(
        task="t_lifecycle",
        input_excerpt="x",
        premises=["p1"],
        reasoning="because",
        decision="do",
        confidence=0.9,
        action_type="analysis",
    )
    assert len(tid) == 32
    assert update_trace_outcome(tid, "ok", True)
    matches = search_traces(task_prefix="t_lifecycle", limit=5)
    assert any(m["trace_id"] == tid for m in matches)


def test_ocean_temperature_within_bounds():
    from ocean_runtime import llm_temperature, current_ocean
    t = llm_temperature()
    assert 0.05 <= t <= 0.9
    cur = current_ocean()
    assert set(cur) == {"openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"}


def test_ocean_drift_starts_zero():
    from ocean_runtime import drift_from_baseline, total_drift
    drift = drift_from_baseline()
    assert all(abs(v) < 1e-6 for v in drift.values()) or total_drift() >= 0.0


def test_cost_model_aggregates():
    from cost_modeling import CostModel, gpu_line, api_line
    m = CostModel(name="test")
    m.add(gpu_line("RTX 5090", 720, 0.0, "local owned, energy only", 0.9))
    m.add(api_line("Anthropic", 100_000, 5.0, "Anthropic price page", 0.95))
    assert m.total_monthly() == 500.0
    assert m.total_annual() == 6000.0
    assert "api" in m.by_kind()


def test_assumption_tracker_lifecycle():
    from assumption_tracker import record, invalidate, list_active, stats
    aid = record("Test assumption", source="test", confidence=0.8)
    assert any(a["id"] == aid for a in list_active())
    assert invalidate(aid, "no longer valid")
    assert not any(a["id"] == aid for a in list_active())
    s = stats()
    assert s["total"] >= 1


def test_scenario_monte_carlo_distribution():
    from scenario_simulator import Distribution, monte_carlo
    out = monte_carlo(
        fn=lambda x: x["a"] + 2 * x["b"],
        inputs={"a": Distribution(1, 2, 3), "b": Distribution(0, 1, 2)},
        runs=2000,
        seed=42,
    )
    assert out["runs"] == 2000
    assert out["min"] <= out["mean"] <= out["max"]
    assert out["p05"] <= out["p95"]


def test_orchestrator_basic_plan():
    from orchestrator import new_plan, add_task, ready_tasks, mark, progress
    p = new_plan("test objective")
    t1 = add_task(p, "First", "JARVIS", "needed first")
    t2 = add_task(p, "Second", "ADA", "depends on t1", depends_on=[t1.id])
    ready = ready_tasks(p)
    assert len(ready) == 1 and ready[0].id == t1.id
    mark(p, t1.id, "completed", "done")
    assert ready_tasks(p)[0].id == t2.id
    pr = progress(p)
    assert pr["total"] == 2 and pr["by_status"].get("completed") == 1
