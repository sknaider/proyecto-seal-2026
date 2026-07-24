from __future__ import annotations

import json
from pathlib import Path
import stat

import pytest

from tools.nerves_a2_canary_record import (
    CanaryRecordError,
    record_principal_ack,
    record_success,
)


def _state(path: Path, *, status: str = "SOAKING") -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "seal.nerves.a2-soak.v1",
                "status": status,
                "release_fingerprint": "a" * 64,
                "started_at": "2026-07-24T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_records_private_immutable_success(tmp_path: Path):
    state = tmp_path / "state.json"
    canaries = tmp_path / "canaries"
    _state(state)
    arguments = {
        "agent": "ADA",
        "mission_id": "11111111-1111-4111-8111-111111111111",
        "receipt_sha256": "b" * 64,
        "assertions": {"completed": True, "zero_tools": True},
        "state_path": state,
        "canary_dir": canaries,
    }
    first = record_success(**arguments)
    second = record_success(**arguments)
    assert first == second
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    value = json.loads(first.read_text(encoding="utf-8"))
    assert value["agent"] == "ADA"
    assert value["mutations"] == 0
    assert value["worker_successes"] == 1


def test_rejects_failed_assertion_and_inactive_soak(tmp_path: Path):
    state = tmp_path / "state.json"
    _state(state)
    common = {
        "agent": "ADA",
        "mission_id": "11111111-1111-4111-8111-111111111111",
        "receipt_sha256": "b" * 64,
        "state_path": state,
        "canary_dir": tmp_path / "canaries",
    }
    with pytest.raises(CanaryRecordError, match="assertions"):
        record_success(assertions={"completed": False}, **common)
    _state(state, status="FAILED")
    with pytest.raises(CanaryRecordError, match="soak_not_active"):
        record_success(assertions={"completed": True}, **common)


def test_records_content_free_principal_ack(tmp_path: Path):
    state = tmp_path / "state.json"
    canaries = tmp_path / "canaries"
    _state(state)
    evidence = {
        "schema": "seal.nerves.principal-ack-evidence.v1",
        "channel": "web_chat",
        "request_id": 117574,
        "request_legacy_id": "api_william_request",
        "request_created_at": "2026-07-24T19:30:44.454127+00:00",
        "ack_id": 117575,
        "ack_legacy_id": "api_ada_ack",
        "ack_created_at": "2026-07-24T19:30:44.927763+00:00",
        "in_reply_to": "api_william_request",
    }
    first = record_principal_ack(
        evidence=evidence,
        principal_ack_latency_ms=474,
        state_path=state,
        canary_dir=canaries,
    )
    second = record_principal_ack(
        evidence=evidence,
        principal_ack_latency_ms=474,
        state_path=state,
        canary_dir=canaries,
    )
    assert first == second
    value = json.loads(first.read_text(encoding="utf-8"))
    assert value["kind"] == "PRINCIPAL_ACK_CANARY"
    assert value["principal_ack_latency_ms"] == 474
    assert value["missions_created"] == 0
    assert value["estimated_cost_units"] == 0
    assert value["mutations"] == 0


def test_principal_ack_rejects_private_or_unbound_evidence(tmp_path: Path):
    state = tmp_path / "state.json"
    _state(state)
    evidence = {
        "schema": "seal.nerves.principal-ack-evidence.v1",
        "channel": "dm:ada:william",
        "request_id": 117574,
        "request_legacy_id": "api_william_request",
        "request_created_at": "2026-07-24T19:30:44.454127+00:00",
        "ack_id": 117575,
        "ack_legacy_id": "api_ada_ack",
        "ack_created_at": "2026-07-24T19:30:44.927763+00:00",
        "in_reply_to": "wrong",
    }
    with pytest.raises(CanaryRecordError, match="evidence_invalid"):
        record_principal_ack(
            evidence=evidence,
            principal_ack_latency_ms=474,
            state_path=state,
            canary_dir=tmp_path / "canaries",
        )
