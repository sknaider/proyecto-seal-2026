from __future__ import annotations

import ast
import json
from pathlib import Path

import t6_graph_reconstruction_probe as probe


def test_positive_control_reconstructs_with_stateless_authorization() -> None:
    result = probe.simulate_expansion(use_guard=False)
    assert result["all_calls_individually_authorized"] is True
    assert result["reconstruction_threshold_reached"] is True
    assert result["edge_coverage"] == 1.0


def test_guard_blocks_accumulation_without_blocking_everything() -> None:
    result = probe.simulate_expansion(use_guard=True)
    assert result["requests_allowed"] > 0
    assert result["requests_blocked"] > 0
    assert result["reconstruction_threshold_reached"] is False
    assert result["edge_coverage"] < probe.RECONSTRUCTION_THRESHOLD


def test_negative_control_allows_benign_navigation() -> None:
    result = probe.simulate_benign()
    assert result["all_allowed"] is True
    assert result["queries"] == 2


def test_evidence_is_deterministic_and_non_vacuous(tmp_path: Path) -> None:
    first = probe.write_evidence(tmp_path / "first.json")
    second = probe.write_evidence(tmp_path / "second.json")
    assert first == second
    evidence = json.loads(first)
    assert evidence["non_vacuous_control"]["passed"] is True
    assert evidence["verdict"]["all_controls_pass"] is True
    assert evidence["verdict"]["production_enforcement_claimed"] is False
    assert evidence["meta"]["not_ssai_t6"] is True
    assert evidence["meta"]["not_t5_mem_extract"] is True


def test_probe_import_surface_is_offline_stdlib_only() -> None:
    tree = ast.parse(Path(probe.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported <= {
        "__future__", "dataclasses", "hashlib", "json", "pathlib", "typing"
    }
