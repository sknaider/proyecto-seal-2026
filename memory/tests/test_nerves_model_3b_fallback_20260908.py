"""Tests: nerves_ollama_runtime_adapter must NOT use qwen2.5:7b as DEFAULT_MODEL."""
from __future__ import annotations
import ast
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "nerves_ollama_runtime_adapter.py"
MODEL_7B = "qwen2.5:7b"
MODEL_3B = "qwen2.5:3b"


def _module_level_string_assignments(src: str) -> dict[str, str]:
    tree = ast.parse(src)
    result = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                    result[target.id] = str(node.value.value)
    return result


def test_unit_default_model_not_7b():
    assignments = _module_level_string_assignments(TARGET.read_text())
    assert "DEFAULT_MODEL" in assignments
    assert assignments["DEFAULT_MODEL"] != MODEL_7B, f"DEFAULT_MODEL must not be '{MODEL_7B}'"


def test_qa_positive_default_model_is_3b():
    assignments = _module_level_string_assignments(TARGET.read_text())
    assert assignments.get("DEFAULT_MODEL") == MODEL_3B, f"DEFAULT_MODEL must be '{MODEL_3B}'"


def test_qa_negative_no_7b_in_default_model():
    assignments = _module_level_string_assignments(TARGET.read_text())
    assert MODEL_7B not in assignments.get("DEFAULT_MODEL", "")


def test_qa_control_file_exists():
    assert TARGET.exists()


if __name__ == "__main__":
    import sys
    tests = [test_unit_default_model_not_7b, test_qa_positive_default_model_is_3b,
             test_qa_negative_no_7b_in_default_model, test_qa_control_file_exists]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
    sys.exit(failed)
