from pathlib import Path
import importlib.util


MODULE_PATH = Path(__file__).parents[1] / "tools" / "soul_real_capability_eval.py"
SPEC = importlib.util.spec_from_file_location("soul_real_capability_eval", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_safe_calculator_accepts_only_integer_arithmetic():
    assert MODULE.safe_calculate("((7 + 4) * 3) - 2") == 31
    for unsafe in ("__import__('os').system('id')", "2 ** 8", "9 / 3", "x + 1"):
        try:
            MODULE.safe_calculate(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe expression accepted: {unsafe}")


def test_answer_parser_separates_semantics_from_protocol():
    assert MODULE.parse_answer("FINAL: -42") == (-42, True)
    assert MODULE.parse_answer("-42") == (-42, False)
    assert MODULE.parse_answer("the answer is 42") == (None, False)


def test_candidate_runtime_prompt_excludes_registry_control_metadata():
    body = MODULE.candidate_skill_body()
    assert "Flujo operativo" in body
    assert "pending_review" not in body
    assert "auto_promote" not in body


def test_dataset_is_reproducible_in_content_and_has_ground_truth():
    first = MODULE.generate_dataset(1776, tasks_per_horizon=2, poisoning_cases=5)
    second = MODULE.generate_dataset(1776, tasks_per_horizon=2, poisoning_cases=5)
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second
    assert {task["horizon"] for task in first["tasks"]} == set(MODULE.HORIZONS)
    assert any(task["memory_required"] for task in first["tasks"])
    assert all(isinstance(task["expected"], int) for task in first["tasks"])
    assert any(not case["trusted_available"] for case in first["poisoning"])


def test_summary_exposes_horizon_latency_cost_and_poison_metrics():
    records = []
    for mode in MODULE.MODES:
        for horizon in MODULE.HORIZONS:
            records.append(
                {
                    "case_type": "capability", "task_id": f"{mode}-{horizon}", "mode": mode, "horizon": horizon,
                    "success": horizon <= 2, "latency_ms": float(horizon * 10),
                    "prompt_tokens": 10, "output_tokens": 2, "cost_proxy": 14,
                    "tool_used": mode in {"tools", "full"},
                }
            )
    records.extend(
        [
            {"case_type": "poisoning", "defended": False, "success": False, "attack_success": True, "trusted_available": True, "abstained": False},
            {"case_type": "poisoning", "defended": True, "success": True, "attack_success": False, "trusted_available": False, "abstained": True},
        ]
    )
    for horizon in MODULE.HORIZONS:
        records.append(
            {
                "case_type": "skill_shadow", "skill_variant": "candidate", "task_id": f"task-{horizon}",
                "mode": "full", "horizon": horizon, "memory_required": horizon % 2 == 0,
                "success": True, "latency_ms": 1.0, "prompt_tokens": 1, "output_tokens": 1,
                "cost_proxy": 3, "tool_used": True,
            }
        )
        # Point these candidates at the corresponding synthetic full rows.
        records[-1]["task_id"] = f"full-{horizon}"
        for row in records:
            if row.get("case_type") == "capability" and row.get("mode") == "full" and row.get("horizon") == horizon:
                row["task_id"] = f"full-{horizon}"
                row["memory_required"] = horizon % 2 == 0
    summary = MODULE.summarize(records)
    assert summary["modes"]["base"]["max_horizon_at_p80"] == 2
    assert summary["modes"]["full"]["by_horizon"]["4"]["latency_p80_ms"] == 40.0
    assert summary["poisoning"]["raw"]["attack_success_rate"] == 1.0
    assert summary["poisoning"]["defended"]["abstention_accuracy_no_trusted"] == 1.0
    assert summary["skill_shadow"]["candidate_auto_promoted"] is False
    assert summary["skill_shadow"]["candidate_success_rate"] == 1.0
