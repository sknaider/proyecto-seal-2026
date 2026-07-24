from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).with_name("collect_orion_evidence.py")
SPEC = importlib.util.spec_from_file_location("collect_orion_evidence", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _record() -> dict:
    return {
        "ts": "2026-07-24T03:00:00+00:00",
        "status": "FAIL",
        "service": "inactive",
        "login_http": 0,
        "backup_age_min": 12.0,
        "db_user": "unknown",
        "counts": {},
        "baseline": {},
        "fails": ["servicio orion-exam no activo (inactive)"],
    }


def test_builds_typed_alice_orion_evidence():
    record = _record()
    digest = hashlib.sha256(MODULE.canonical_bytes(record)).hexdigest()
    evidence = MODULE.build_evidence(
        "11111111-1111-4111-8111-111111111111",
        record,
        expected_record_sha256=digest,
    )
    assert evidence["agent"] == "ALICE"
    assert evidence["action"] == "orion_product_pulse"
    assert evidence["checks"][1]["ok"] is False


def test_rejects_unknown_fields_and_non_actionable_status():
    record = _record()
    record["secret"] = "no"
    digest = hashlib.sha256(MODULE.canonical_bytes(record)).hexdigest()
    with pytest.raises(MODULE.OrionEvidenceError):
        MODULE.build_evidence("m", record, expected_record_sha256=digest)
    record = _record() | {"status": "OK"}
    digest = hashlib.sha256(MODULE.canonical_bytes(record)).hexdigest()
    with pytest.raises(MODULE.OrionEvidenceError):
        MODULE.build_evidence("m", record, expected_record_sha256=digest)
