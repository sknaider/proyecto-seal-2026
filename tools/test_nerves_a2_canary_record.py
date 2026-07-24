from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat

import pytest

from tools.nerves_a2_canary_record import (
    CANARY_SCHEMA,
    SOAK_SCHEMA,
    CanaryRecordError,
    record_principal_ack,
    record_success,
)


MISSION_ID = "11111111-1111-4111-8111-111111111111"


def _write_private(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _state(path: Path, *, status: str = "SOAKING") -> None:
    _write_private(
        path,
        {
            "schema": SOAK_SCHEMA,
            "status": status,
            "release_fingerprint": "a" * 64,
            "started_at": "2026-07-24T00:00:00+00:00",
        },
    )


def _local_receipt(workspace: Path, *, started_at: str = "2026-07-24T19:00:00+00:00") -> Path:
    return _write_private(
        workspace / "receipts" / "local.json",
        {
            "schema": "seal.nerves.orchestrator-receipt.v3",
            "mission_id": MISSION_ID,
            "status": "completed",
            "started_at": started_at,
            "finished_at": "2026-07-24T19:00:01+00:00",
            "worker_kind": "local_ollama_subagent",
            "runtime_attestation": {
                "platform": "ollama_generate_json",
                "isolation": "no_tool_api",
                "tool_events": [],
                "endpoint": "http://127.0.0.1:11434/api/generate",
            },
        },
    )


def test_records_private_immutable_success(tmp_path: Path):
    workspace = tmp_path / "workspace"
    state = workspace / "state.json"
    canaries = workspace / "canaries"
    _state(state)
    arguments = {
        "agent": "ADA",
        "mission_id": MISSION_ID,
        "receipt_path": _local_receipt(workspace),
        "assertions": {"completed": True, "zero_tools": True},
        "state_path": state,
        "canary_dir": canaries,
        "workspace_root": workspace,
    }
    first = record_success(**arguments)
    second = record_success(**arguments)
    assert first == second
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    value = json.loads(first.read_text(encoding="utf-8"))
    assert value["schema"] == CANARY_SCHEMA
    assert value["agent"] == "ADA"
    assert value["mutations"] == 0
    assert value["worker_successes"] == 1
    assert value["harm_avoided"] == 0
    assert value["utility_basis"] == "controlled_route_completion"
    assert value["attestation_sha256"] == hashlib.sha256(
        arguments["receipt_path"].read_bytes()
    ).hexdigest()


def test_rejects_failed_assertion_inactive_soak_and_stale_receipt(tmp_path: Path):
    workspace = tmp_path / "workspace"
    state = workspace / "state.json"
    _state(state)
    common = {
        "agent": "ADA",
        "mission_id": MISSION_ID,
        "receipt_path": _local_receipt(workspace),
        "state_path": state,
        "canary_dir": workspace / "canaries",
        "workspace_root": workspace,
    }
    with pytest.raises(CanaryRecordError, match="assertions"):
        record_success(assertions={"completed": False}, **common)
    _state(state, status="FAILED")
    with pytest.raises(CanaryRecordError, match="soak_not_active"):
        record_success(assertions={"completed": True}, **common)
    _state(state)
    common["receipt_path"] = _local_receipt(
        workspace,
        started_at="2026-07-23T19:00:00+00:00",
    )
    with pytest.raises(CanaryRecordError, match="outside_soak_window"):
        record_success(assertions={"completed": True}, **common)


def test_rejects_hash_only_or_outside_workspace_receipt(tmp_path: Path):
    workspace = tmp_path / "workspace"
    state = workspace / "state.json"
    _state(state)
    outside = _local_receipt(tmp_path / "outside")
    with pytest.raises(CanaryRecordError, match="outside_workspace"):
        record_success(
            agent="ADA",
            mission_id=MISSION_ID,
            receipt_path=outside,
            assertions={"completed": True},
            state_path=state,
            canary_dir=workspace / "canaries",
            workspace_root=workspace,
        )


def test_records_content_free_principal_ack(tmp_path: Path):
    workspace = tmp_path / "workspace"
    state = workspace / "state.json"
    canaries = workspace / "canaries"
    attestations = workspace / "attestations"
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
    arguments = {
        "evidence": evidence,
        "principal_ack_latency_ms": 474,
        "state_path": state,
        "canary_dir": canaries,
        "attestation_dir": attestations,
    }
    first = record_principal_ack(**arguments)
    second = record_principal_ack(**arguments)
    assert first == second
    value = json.loads(first.read_text(encoding="utf-8"))
    attestation = Path(value["attestation_path"])
    assert value["kind"] == "PRINCIPAL_ACK_CANARY"
    assert value["principal_ack_latency_ms"] == 474
    assert value["missions_created"] == 0
    assert value["estimated_cost_units"] == 0
    assert value["mutations"] == 0
    assert value["harm_avoided"] == 0
    assert value["utility_basis"] == "measured_principal_ack_latency"
    assert value["attestation_sha256"] == hashlib.sha256(
        attestation.read_bytes()
    ).hexdigest()


def test_principal_ack_rejects_private_or_unbound_evidence(tmp_path: Path):
    workspace = tmp_path / "workspace"
    state = workspace / "state.json"
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
            canary_dir=workspace / "canaries",
            attestation_dir=workspace / "attestations",
        )
