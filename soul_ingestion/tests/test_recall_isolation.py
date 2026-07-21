from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INGESTION_TABLE_PREFIX = "soul_v3.ingestion_"


def _registered_recall_tables() -> list[str]:
    tree = ast.parse((REPO_ROOT / "memory" / "recall_router.py").read_text(encoding="utf-8"))
    tables: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function_name = getattr(node.func, "id", None)
        if function_name != "RecallSourceRegistration":
            continue
        for keyword in node.keywords:
            if keyword.arg == "table" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    tables.append(keyword.value.value)
    return tables


def test_ingestion_staging_is_not_a_registered_recall_source() -> None:
    tables = _registered_recall_tables()
    assert tables, "recall registry extraction returned no tables"
    assert not any(table.startswith(INGESTION_TABLE_PREFIX) for table in tables)


def test_ingestion_runtime_does_not_import_or_register_recall_sources() -> None:
    forbidden = ("REGISTERED_RECALL_SOURCES", "register_recall_source", "memory.recall_router")
    runtime_files = [
        path
        for path in (REPO_ROOT / "soul_ingestion").rglob("*.py")
        if "tests" not in path.parts
    ]
    for path in runtime_files:
        source = path.read_text(encoding="utf-8")
        assert not any(marker in source for marker in forbidden), path
