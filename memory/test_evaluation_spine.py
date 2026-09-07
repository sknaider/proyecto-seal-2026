from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluation_spine import PLAN_PHASES, THRESHOLDS, EvalResult, _decode_jsonb_fields, _passed, _result, build_parser, summarize_results


def test_threshold_contract() -> None:
    assert _passed("soul_db_integrity", THRESHOLDS["soul_db_integrity"])
    assert not _passed("memory_preservation", THRESHOLDS["memory_preservation"] - 1)
    assert not _passed("reflex_layer", THRESHOLDS["reflex_layer"] - 1)
    assert not _passed("memory_outcome", THRESHOLDS["memory_outcome"] - 1)
    assert not _passed("causal_chain", THRESHOLDS["causal_chain"] - 1)
    assert not _passed("cognitive_governance", THRESHOLDS["cognitive_governance"] - 1)
    assert not _passed("long_horizon_bench", THRESHOLDS["long_horizon_bench"] - 1)
    assert not _passed("final_audit_readiness", THRESHOLDS["final_audit_readiness"] - 1)
    assert not _passed("task_lifecycle", THRESHOLDS["task_lifecycle"] - 1)
    assert not _passed("task_closure_gate", THRESHOLDS["task_closure_gate"] - 1)
    assert not _passed("working_state_journal", THRESHOLDS["working_state_journal"] - 1)
    assert not _passed("working_state_hook", THRESHOLDS["working_state_hook"] - 1)
    assert not _passed("working_state_hook_activation", THRESHOLDS["working_state_hook_activation"] - 1)
    assert not _passed("working_state_hook_soak", THRESHOLDS["working_state_hook_soak"] - 1)
    assert not _passed("working_state_production_readiness", THRESHOLDS["working_state_production_readiness"] - 1)
    assert not _passed("working_state_live_capture", THRESHOLDS["working_state_live_capture"] - 1)
    assert not _passed("bridge_natural_journal_observer", THRESHOLDS["bridge_natural_journal_observer"] - 1)
    assert not _passed("bridge_natural_close_watcher", THRESHOLDS["bridge_natural_close_watcher"] - 1)
    assert not _passed("daily_evidence_dashboard", THRESHOLDS["daily_evidence_dashboard"] - 1)
    assert not _passed("awareness_dashboard_3005", THRESHOLDS["awareness_dashboard_3005"] - 1)
    assert not _passed("soul_app_awareness_5173", THRESHOLDS["soul_app_awareness_5173"] - 1)
    assert not _passed("auxiliary_secrets_debt", THRESHOLDS["auxiliary_secrets_debt"] - 1)
    assert not _passed("kernel_forge", THRESHOLDS["kernel_forge"] - 1)
    assert not _passed("awareness_event_collector", THRESHOLDS["awareness_event_collector"] - 1)
    assert not _passed("attention_governor", THRESHOLDS["attention_governor"] - 1)
    assert not _passed("awareness_tick_ledger", THRESHOLDS["awareness_tick_ledger"] - 1)
    assert not _passed("awareness_reflex_actions", THRESHOLDS["awareness_reflex_actions"] - 1)
    assert not _passed("local_runtime_contract", THRESHOLDS["local_runtime_contract"] - 1)
    assert not _passed("awareness_loop_shadow", THRESHOLDS["awareness_loop_shadow"] - 1)
    assert not _passed("awareness_experience_dataset", THRESHOLDS["awareness_experience_dataset"] - 1)
    assert not _passed("awareness_closed_loop", THRESHOLDS["awareness_closed_loop"] - 1)
    assert not _passed("latent_graphmem_phase2", THRESHOLDS["latent_graphmem_phase2"] - 1)
    assert not _passed("awareness_247_process", THRESHOLDS["awareness_247_process"] - 1)
    assert not _passed("agi_gap_ledger", THRESHOLDS["agi_gap_ledger"] - 1)
    assert not _passed("autonomous_lifecycle", THRESHOLDS["autonomous_lifecycle"] - 1)
    assert not _passed("autonomous_lifecycle_persistence", THRESHOLDS["autonomous_lifecycle_persistence"] - 1)
    assert not _passed("autonomous_lifecycle_delegation", THRESHOLDS["autonomous_lifecycle_delegation"] - 1)
    assert not _passed("autonomous_lifecycle_review_gate", THRESHOLDS["autonomous_lifecycle_review_gate"] - 1)
    assert not _passed("nexus_review_queue", THRESHOLDS["nexus_review_queue"] - 1)
    assert not _passed("nexus_review_actions", THRESHOLDS["nexus_review_actions"] - 1)
    assert not _passed("soul_autonomy_pipeline", THRESHOLDS["soul_autonomy_pipeline"] - 1)


def test_plan_phases_include_sprint_11_awaiter() -> None:
    phase = PLAN_PHASES["S11"]

    assert phase["step"] == 11
    assert phase["total_steps"] == 11
    assert phase["task_name"] == "Sprint 11 — First Natural Bridge Event Awaiter"


def test_result_clamps_score_and_sets_passed() -> None:
    result = _result("agent_handoff", 120, "ok", {"x": 1})
    assert result.score == 100
    assert result.passed is True

    low = _result("bridge_health", -10, "bad", {})
    assert low.score == 0
    assert low.passed is False


def test_eval_result_details_are_json_serializable() -> None:
    result = EvalResult(
        suite_name="bridge_health",
        score=80,
        passed=True,
        evidence="services_active=6/6",
        details={"services": {"seal-chat.service": "active"}},
    )
    encoded = json.dumps(result.details, sort_keys=True)
    assert "seal-chat.service" in encoded


def test_cli_parser_accepts_run_all() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-all", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-all"
    assert args.no_persist is True


def test_summarize_results_reports_failed_suites_and_run_ids() -> None:
    ok = _result("soul_db_integrity", 100, "ok", {"evaluation_run_id": 10})
    bad = _result("memory_preservation", 20, "bad", {"evaluation_run_id": 11})
    summary = summarize_results([ok, bad])

    assert summary["ok"] is False
    assert summary["passed"] == 1
    assert summary["total"] == 2
    assert summary["failed_suites"] == ["memory_preservation"]
    assert summary["evaluation_run_ids"] == {"soul_db_integrity": 10, "memory_preservation": 11}


def test_cli_parser_accepts_plan_status() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "plan-status"])
    assert args.agent == "ADA"
    assert args.command == "plan-status"


def test_cli_parser_accepts_reflex_layer_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "reflex_layer", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "reflex_layer"


def test_cli_parser_accepts_memory_outcome_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "memory_outcome", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "memory_outcome"


def test_cli_parser_accepts_causal_chain_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "causal_chain", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "causal_chain"


def test_cli_parser_accepts_cognitive_governance_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "cognitive_governance", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "cognitive_governance"


def test_cli_parser_accepts_long_horizon_bench_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "long_horizon_bench", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "long_horizon_bench"


def test_cli_parser_accepts_final_audit_readiness_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "final_audit_readiness", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "final_audit_readiness"


def test_cli_parser_accepts_task_lifecycle_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "task_lifecycle", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "task_lifecycle"


def test_cli_parser_accepts_task_closure_gate_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "task_closure_gate", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "task_closure_gate"


def test_cli_parser_accepts_working_state_journal_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "working_state_journal", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "working_state_journal"


def test_cli_parser_accepts_working_state_hook_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "working_state_hook", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "working_state_hook"


def test_cli_parser_accepts_working_state_hook_activation_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "working_state_hook_activation", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "working_state_hook_activation"


def test_cli_parser_accepts_working_state_hook_soak_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "working_state_hook_soak", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "working_state_hook_soak"


def test_cli_parser_accepts_working_state_production_readiness_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "working_state_production_readiness", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "working_state_production_readiness"


def test_cli_parser_accepts_working_state_live_capture_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "working_state_live_capture", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "working_state_live_capture"


def test_cli_parser_accepts_bridge_natural_journal_observer_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "bridge_natural_journal_observer", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "bridge_natural_journal_observer"


def test_cli_parser_accepts_bridge_natural_close_watcher_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "bridge_natural_close_watcher", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "bridge_natural_close_watcher"


def test_cli_parser_accepts_agi_gap_ledger_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "agi_gap_ledger", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "agi_gap_ledger"


def test_cli_parser_accepts_daily_evidence_dashboard_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "daily_evidence_dashboard", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "daily_evidence_dashboard"


def test_cli_parser_accepts_awareness_dashboard_3005_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "awareness_dashboard_3005", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "awareness_dashboard_3005"


def test_cli_parser_accepts_soul_app_awareness_5173_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "soul_app_awareness_5173", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "soul_app_awareness_5173"


def test_cli_parser_accepts_auxiliary_secrets_debt_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "auxiliary_secrets_debt", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "auxiliary_secrets_debt"


def test_cli_parser_accepts_kernel_forge_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "kernel_forge", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "kernel_forge"


def test_cli_parser_accepts_awareness_suites() -> None:
    for suite in [
        "awareness_event_collector",
        "attention_governor",
        "awareness_tick_ledger",
        "awareness_reflex_actions",
        "local_runtime_contract",
        "awareness_loop_shadow",
        "awareness_experience_dataset",
        "awareness_closed_loop",
        "latent_graphmem_phase2",
        "awareness_247_process",
    ]:
        args = build_parser().parse_args(["--agent", "ADA", "run-suite", suite, "--no-persist"])
        assert args.agent == "ADA"
        assert args.command == "run-suite"
        assert args.name == suite


def test_cli_parser_accepts_autonomous_lifecycle_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "autonomous_lifecycle", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "autonomous_lifecycle"


def test_cli_parser_accepts_autonomous_lifecycle_persistence_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "autonomous_lifecycle_persistence", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "autonomous_lifecycle_persistence"


def test_cli_parser_accepts_autonomous_lifecycle_delegation_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "autonomous_lifecycle_delegation", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "autonomous_lifecycle_delegation"


def test_cli_parser_accepts_autonomous_lifecycle_review_gate_suite() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "run-suite", "autonomous_lifecycle_review_gate", "--no-persist"])
    assert args.agent == "ADA"
    assert args.command == "run-suite"
    assert args.name == "autonomous_lifecycle_review_gate"


def test_decode_jsonb_fields_leaves_invalid_json_unchanged() -> None:
    decoded = _decode_jsonb_fields({"details": "{\"ok\": true}"}, ("details",))
    unchanged = _decode_jsonb_fields({"details": "not-json"}, ("details",))

    assert decoded["details"] == {"ok": True}
    assert unchanged["details"] == "not-json"
