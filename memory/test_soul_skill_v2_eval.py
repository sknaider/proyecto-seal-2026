from pathlib import Path
import importlib.util


PATH = Path(__file__).parents[1] / "tools" / "soul_skill_v2_eval.py"
SPEC = importlib.util.spec_from_file_location("soul_skill_v2_eval", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def task():
    return {
        "question": "Evalúa: (K12345 * 3).",
        "memory": [{"key": "K12345", "value": 7, "trusted": True}],
        "expected": 21,
    }


def test_guarded_fallback_recovers_only_observable_failure():
    failed = {"abstained": True, "answer": None, "tool_used": False, "success": False,
              "latency_ms": 10.0, "cost_proxy": 100, "response": "ABSTAIN"}
    recovered = MODULE.apply_candidate(task(), failed)
    assert recovered["success"] is True
    assert recovered["answer"] == 21
    assert recovered["fallback_triggered"] is True


def test_guard_never_overwrites_valid_baseline():
    good = {"abstained": False, "answer": 21, "tool_used": True, "success": True,
            "latency_ms": 10.0, "cost_proxy": 100, "response": "FINAL: 21"}
    result = MODULE.apply_candidate(task(), good)
    assert result["fallback_triggered"] is False
    assert result["response"] == good["response"]
    assert result["cost_proxy"] == good["cost_proxy"]


def test_untrusted_or_missing_keys_cannot_enter_fallback():
    case = task()
    case["memory"][0]["trusted"] = False
    try:
        MODULE.extract_numeric_expression(case)
    except ValueError as exc:
        assert "unresolved" in str(exc)
    else:
        raise AssertionError("untrusted key accepted")


def test_wilson_interval_is_bounded():
    low, high = MODULE.wilson(30, 40)
    assert 0 < low < 0.75 < high < 1
