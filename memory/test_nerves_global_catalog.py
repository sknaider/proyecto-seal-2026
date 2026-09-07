from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from nerves_global_catalog import (
    CATALOG_PATH,
    GlobalCatalogError,
    record_global_binding_heartbeat,
    validate_catalog,
)


def _catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def test_canonical_catalog_covers_6_agents_x_12_nerves() -> None:
    result = validate_catalog(_catalog())
    assert result["ok"] is True
    assert result["agents"] == 6
    assert result["global_nerves"] == 12
    assert result["matrix_cells"] == 72


def test_missing_agent_binding_fails_closed() -> None:
    catalog = _catalog()
    catalog["bindings"].pop()
    with pytest.raises(GlobalCatalogError, match="schema_invalid|incomplete"):
        validate_catalog(catalog)


def test_partial_inheritance_fails_closed() -> None:
    catalog = _catalog()
    catalog["bindings"][0]["inherits"].pop()
    with pytest.raises(GlobalCatalogError, match="schema_invalid|incomplete_binding"):
        validate_catalog(catalog)


def test_missing_evidence_fails_closed(tmp_path: Path) -> None:
    catalog = copy.deepcopy(_catalog())
    catalog["global_nerves"][0]["evidence_refs"][0] = "missing/proof.py"
    with pytest.raises(GlobalCatalogError, match="missing_evidence_refs"):
        validate_catalog(catalog, root=tmp_path)


def test_duplicate_nerve_id_fails_closed() -> None:
    catalog = _catalog()
    catalog["global_nerves"][1]["id"] = catalog["global_nerves"][0]["id"]
    with pytest.raises(GlobalCatalogError, match="duplicate_global_nerve_id"):
        validate_catalog(catalog)


def test_live_binding_heartbeat_is_private_and_truthful(tmp_path: Path) -> None:
    record = record_global_binding_heartbeat(
        "ADA",
        runtime_dir=tmp_path / "runtime",
    )
    artifact = tmp_path / "runtime" / "ada.json"
    assert record["global_nerve_count"] == 12
    assert record["claim"] == "binding_loaded_not_behavior_fired"
    assert artifact.stat().st_mode & 0o777 == 0o600
    assert artifact.parent.stat().st_mode & 0o777 == 0o700
