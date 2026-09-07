from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE = Path(__file__).parents[1] / "tools" / "validate_skill_resources.py"
SPEC = importlib.util.spec_from_file_location("validate_skill_resources", MODULE)
validator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)


def test_valid_resources_pass(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "ok.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    result = validator.validate([tmp_path])
    assert result["passed"] is True
    assert result["counts"]["python"] == 1
    assert result["counts"]["shell"] == 1


def test_invalid_python_and_shell_fail_closed(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text("if:\n", encoding="utf-8")
    (tmp_path / "bad.sh").write_text("if true; then\n", encoding="utf-8")
    result = validator.validate([tmp_path])
    assert result["passed"] is False
    assert {item["kind"] for item in result["findings"]} == {"python", "shell"}
