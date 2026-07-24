from __future__ import annotations

import hashlib
import json
from pathlib import Path

import rigor_claim_audit as audit


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _state(deliveries: dict) -> dict:
    body = {"schema": "seal.nerves.handoff-state.v2", "deliveries": deliveries}
    return {
        **body,
        "state_sha256": hashlib.sha256(_canonical(body)).hexdigest(),
    }


def _fixture(root: Path, *, corrupt_receipt: bool = False) -> None:
    base = root / "research/flywire_results/nerves_orchestrator_inbox"
    for agent in ("ADA", "ALICE", "NEXUS"):
        agent_dir = base / agent
        agent_dir.mkdir(parents=True, mode=0o700)
        mission_id = f"11111111-1111-4111-8111-{agent.lower():0<12}"[:36]
        receipt_path = agent_dir / f"{mission_id}.handoff.receipt.json"
        receipt = {
            "mission_id": mission_id,
            "status": "completed",
            "runtime_attestation": {"tool_events": []},
            "verifier": {"verdict": "accepted"},
        }
        receipt_raw = _canonical(receipt) + b"\n"
        receipt_path.write_bytes(receipt_raw)
        receipt_path.chmod(0o600)
        digest = hashlib.sha256(receipt_raw).hexdigest()
        if corrupt_receipt and agent == "ADA":
            digest = "0" * 64
        state = _state(
            {
                mission_id: {
                    "mission_id": mission_id,
                    "status": "completed",
                    "receipt_path": str(receipt_path),
                    "receipt": {"sha256": digest},
                }
            }
        )
        state_path = base / f"{agent}.state.json"
        state_path.write_bytes(_canonical(state) + b"\n")
        state_path.chmod(0o600)


def test_clean_claim_audit_has_calibration_and_direct_effect(tmp_path):
    _fixture(tmp_path)
    event = audit.collect(tmp_path)
    assert event["state"] == "GREEN"
    assert event["calibration"]["status"] == "PASS"
    assert event["findings"] == []
    assert any(
        check["kind"] == "direct_effect"
        for check in event["checks"]
    )


def test_receipt_hash_mismatch_is_typed_finding(tmp_path):
    _fixture(tmp_path, corrupt_receipt=True)
    event = audit.collect(tmp_path)
    assert event["state"] == "FINDING"
    assert event["findings"] == [
        "receipt_binding_or_verifier_mismatch"
    ]
    assert event["broken"] == []


def test_private_append_is_idempotent_for_exact_event(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    event = {"event_id": "a" * 64, "state": "GREEN"}
    assert audit.append_event(event, ledger) is True
    assert audit.append_event(event, ledger) is False
    assert ledger.stat().st_mode & 0o777 == 0o600
    assert len(ledger.read_text().splitlines()) == 1
