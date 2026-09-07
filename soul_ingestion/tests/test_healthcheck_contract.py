from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_healthcheck_is_read_only_and_validates_capacity_contract() -> None:
    source = (REPO_ROOT / "scripts" / "soul_ingestion_healthcheck.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "pr_ingestion_processor" in source
    assert "svc_soul_ingestion" in source
    assert '"memory_write": False' in source
    assert not any(
        isinstance(node, ast.Attribute) and node.attr in {"POST", "PUT", "DELETE", "PATCH"}
        for node in ast.walk(tree)
    )
    assert 'method="GET"' in source
