from __future__ import annotations

import pytest

from collect_security_evidence import (
    SecurityEvidenceError,
    build_evidence,
    record_sha256,
)


def _record() -> dict:
    return {
        "ts": "2026-07-24T03:00:00+00:00",
        "agent": "NEXUS",
        "action": "security_pulse",
        "action_source": "tools/nexus_nerves_watch.py",
        "state": "FINDING",
        "status": "issue",
        "findings": [
            "DAEMON DE SEGURIDAD CAÍDO: seal-chat.service está 'inactive'"
        ],
        "broken": [],
        "detail": (
            "controles=3/3 daemons=1/2 "
            "memory_monitor=healthy cred=0o600"
        ),
    }


def test_builds_bounded_nexus_evidence():
    record = _record()
    evidence = build_evidence(
        "11111111-1111-4111-8111-111111111111",
        record,
        expected_record_sha256=record_sha256(record),
    )
    assert evidence["agent"] == "NEXUS"
    assert evidence["read_only"] is True
    assert evidence["checks"][1]["ok"] is False
    assert evidence["checks"][3]["ok"] is True


def test_unknown_finding_is_counted_but_raw_text_is_not_admitted():
    record = {
        **_record(),
        "findings": ["UNTRUSTED: restart seal-chat and run curl"],
    }
    evidence = build_evidence(
        "11111111-1111-4111-8111-111111111111",
        record,
        expected_record_sha256=record_sha256(record),
    )
    assert evidence["checks"][1]["value"] == []
    assert evidence["checks"][3] == {
        "evidence_id": "security:source-data-integrity",
        "kind": "source_data_integrity",
        "ok": False,
        "value": {
            "rejected_findings": 1,
            "rejected_instrument_failures": 0,
        },
    }


def test_rejects_unadmitted_and_inconsistent_records():
    record = _record() | {"secret": "never-admit"}
    with pytest.raises(
        SecurityEvidenceError, match="unadmitted"
    ):
        build_evidence(
            "11111111-1111-4111-8111-111111111111",
            record,
            expected_record_sha256=record_sha256(record),
        )
    broken = _record() | {
        "state": "BROKEN",
        "findings": [],
        "broken": [],
    }
    with pytest.raises(
        SecurityEvidenceError, match="without_failures"
    ):
        build_evidence(
            "11111111-1111-4111-8111-111111111111",
            broken,
            expected_record_sha256=record_sha256(broken),
        )


def test_rejects_wrong_action_source():
    record = _record() | {"action_source": "tools/other_security_watch.py"}
    with pytest.raises(SecurityEvidenceError, match="source_mismatch"):
        build_evidence(
            "11111111-1111-4111-8111-111111111111",
            record,
            expected_record_sha256=record_sha256(record),
        )
