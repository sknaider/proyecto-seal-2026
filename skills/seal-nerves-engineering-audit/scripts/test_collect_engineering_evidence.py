from __future__ import annotations

import pytest

from collect_engineering_evidence import (
    EngineeringEvidenceError,
    build_evidence,
    record_sha256,
)


def _record() -> dict:
    return {
        "ts": "2026-07-24T01:00:00+00:00",
        "agent": "ADA",
        "action": "engineering_pulse",
        "changed_python_checked": 3,
        "diff_check_ok": False,
        "syntax_failures": ["memory/example.py"],
        "status": "issue",
    }


def test_build_evidence_is_typed_and_bounded():
    record = _record()
    evidence = build_evidence(
        "11111111-1111-4111-8111-111111111111",
        record,
        expected_record_sha256=record_sha256(record),
    )
    assert evidence["agent"] == "ADA"
    assert evidence["read_only"] is True
    assert evidence["checks"][0]["ok"] is False
    assert evidence["checks"][1]["value"] == ["memory/example.py"]


def test_record_hash_mismatch_fails_closed():
    with pytest.raises(EngineeringEvidenceError, match="record_sha256_mismatch"):
        build_evidence(
            "11111111-1111-4111-8111-111111111111",
            _record(),
            expected_record_sha256="0" * 64,
        )


def test_unadmitted_record_key_fails_closed():
    record = {**_record(), "dm_content": "private"}
    with pytest.raises(
        EngineeringEvidenceError, match="record_contains_unadmitted_keys"
    ):
        build_evidence(
            "11111111-1111-4111-8111-111111111111",
            record,
            expected_record_sha256=record_sha256(record),
        )
