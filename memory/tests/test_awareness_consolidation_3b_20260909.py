"""
Tests: jarvis_awareness.py and consolidation_daemon.py must use qwen2.5:3b as OLLAMA_MODEL.
Regression: both had hardcoded qwen2.5:7b — reloaded 6.29 GB VRAM every 30 min / 24h.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TARGET_FILES = [
    REPO / "memory/jarvis_awareness.py",
    REPO / "memory/consolidation_daemon.py",
]
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


def test_unit_no_7b_ollama_model_assignment():
    for path in TARGET_FILES:
        src = path.read_text()
        assignments = _module_level_string_assignments(src)
        val = assignments.get("OLLAMA_MODEL", "")
        assert val != MODEL_7B, (
            f"{path.name}: OLLAMA_MODEL = '{val}' — must not be '{MODEL_7B}'"
        )


def test_qa_positive_ollama_model_is_3b():
    for path in TARGET_FILES:
        src = path.read_text()
        assignments = _module_level_string_assignments(src)
        val = assignments.get("OLLAMA_MODEL", "")
        assert val == MODEL_3B, (
            f"{path.name}: OLLAMA_MODEL = '{val}' — expected '{MODEL_3B}'"
        )


def test_qa_negative_no_7b_in_ollama_model():
    for path in TARGET_FILES:
        src = path.read_text()
        assignments = _module_level_string_assignments(src)
        val = assignments.get("OLLAMA_MODEL", "")
        assert MODEL_7B not in val, (
            f"{path.name}: '{MODEL_7B}' still present in OLLAMA_MODEL"
        )


def test_qa_control_files_exist():
    for path in TARGET_FILES:
        assert path.exists(), f"File not found: {path}"
