from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE = Path(__file__).parents[1] / "tools" / "validate_skill_registry.py"
SPEC = importlib.util.spec_from_file_location("validate_skill_registry", MODULE)
registry = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = registry
SPEC.loader.exec_module(registry)


def test_parse_rows_preserves_paths() -> None:
    rows = registry.parse_rows("12\tTEAM\tverify\t/home/dadito/skill/SKILL.md\n")
    assert rows == [(12, "TEAM", "verify", "/home/dadito/skill/SKILL.md")]
