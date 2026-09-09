"""
Tests: all SEAL agent cortex.py kernels must use qwen2.5:3b as Ollama fallback.
Regression: before this fix, all 4 kernels fell back to qwen2.5:7b (6.1 GB VRAM).
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORTEX_FILES = [
    REPO / "sandbox-agent/ADA/kernel/cortex.py",
    REPO / "sandbox-agent/ALICE/kernel/cortex.py",
    REPO / "sandbox-agent/JARVIS/kernel/cortex.py",
    REPO / "sandbox-agent/NEXUS/kernel/cortex.py",
]
MODEL_7B = "qwen2.5:7b"
MODEL_3B = "qwen2.5:3b"


def _ollama_get_defaults(src: str) -> list[str]:
    """Return string literals used as defaults in os.environ.get(key, default)."""
    tree = ast.parse(src)
    defaults: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "environ"
            ):
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    defaults.append(str(node.args[1].value))
                for kw in node.keywords:
                    if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                        defaults.append(str(kw.value.value))
    return defaults


def test_qa_positive_all_cortex_use_3b_default():
    for path in CORTEX_FILES:
        src = path.read_text()
        defaults = _ollama_get_defaults(src)
        assert MODEL_3B in defaults, (
            f"{path.name}: expected '{MODEL_3B}' as os.environ.get default, got {defaults}"
        )


def test_qa_negative_no_cortex_has_7b_fallback():
    for path in CORTEX_FILES:
        src = path.read_text()
        defaults = _ollama_get_defaults(src)
        assert MODEL_7B not in defaults, (
            f"{path.name}: found forbidden '{MODEL_7B}' as os.environ.get default"
        )


def test_qa_control_all_cortex_files_exist():
    for path in CORTEX_FILES:
        assert path.exists(), f"cortex.py not found: {path}"


def test_unit_no_7b_literal_anywhere():
    for path in CORTEX_FILES:
        content = path.read_text()
        assert MODEL_7B not in content, (
            f"{path.name}: literal '{MODEL_7B}' still present — must be '{MODEL_3B}'"
        )
