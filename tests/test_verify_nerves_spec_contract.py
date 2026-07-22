from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_nerves_spec_contract", ROOT / "tools/verify_nerves_spec_contract.py"
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_string_keys_extracts_literal_dict_keys():
    node = ast.parse("VALUE = {'b': 1, 'a': 2}").body[0].value
    assert module._string_keys(node) == {"a", "b"}


def test_string_set_supports_annotated_literal_set():
    tree = ast.parse("VALUE: set[str] = {'ADA', 'JARVIS'}")
    node = module._assignment(tree, "VALUE")
    assert module._string_set(node) == {"ADA", "JARVIS"}


def test_static_contract_matches_repository():
    contract = module._load_json(ROOT / "memory/nerves_contract_v3.json")
    checks = module.static_checks(contract)
    failed = [check for check in checks if not check.ok]
    assert not failed, [(check.name, check.detail) for check in failed]
