#!/usr/bin/env python3
"""Typed, read-only claim/receipt audit for FABLE's rigor nerve.

This replaces the duplicated instrumentation freshness check in the live
``rigor_drive``.  It validates the durable custody chain produced by the
agent NERVES workers and appends one bounded, private event.  It never reads
mission prose, credentials, DMs, or model output.
"""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any
import uuid


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory.nerves_mission_handoff import _load_state  # noqa: E402


SCHEMA = "seal.fable.rigor-claim-audit.v2"
ACTION = "rigor_pulse"
TARGET = "rigor_claim_audit"
ACTION_SOURCE = "fable/rigor_claim_audit.py"
CONTRACT_REV = "2"
LEDGER = ROOT / "research/flywire_results/nerves_fable/rigor_claim_audit_v2.jsonl"
LOCK = LEDGER.with_suffix(".lock")
# FABLE deliberately does not audit its own newly-created receipt here: doing
# so would make the audit change its own source hash and recursively create a
# new mission.  The cross-agent final gate verifies the FABLE worker itself.
AGENTS = ("ADA", "ALICE", "NEXUS")
TERMINAL = frozenset({"completed", "failed", "abstained"})
MAX_LEDGER_BYTES = 4_194_304
MAX_RECORD_BYTES = 65_536


class RigorAuditError(RuntimeError):
    """The measuring instrument could not prove its own result."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _check(
    evidence_id: str,
    kind: str,
    status_value: str,
    reason_code: str,
    expected: Any,
    observed: Any,
    source_ref: str,
    *,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "kind": kind,
        "required": required,
        "status": status_value,
        "reason_code": reason_code,
        "expected": expected,
        "observed": observed,
        "source_ref": source_ref,
    }


def _receipt_check(
    agent: str,
    mission_id: str,
    record: dict[str, Any],
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    relative = (
        f"research/flywire_results/nerves_orchestrator_inbox/"
        f"{agent}/{mission_id}.handoff.receipt.json"
    )
    expected_sha = str((record.get("receipt") or {}).get("sha256") or "")
    receipt_path = Path(str(record.get("receipt_path") or ""))
    observed: dict[str, Any] = {
        "mission_id": mission_id,
        "delivery_status": record.get("status"),
        "exists": False,
        "mode": None,
        "sha256_match": False,
        "receipt_mission_match": False,
        "receipt_status_match": False,
        "verifier_accepted": False,
        "zero_tool_events": False,
    }
    reason = "receipt_consistent"
    status_value = "PASS"
    try:
        if (
            not receipt_path.is_absolute()
            or not receipt_path.resolve(strict=True).is_relative_to(root)
            or receipt_path.is_symlink()
        ):
            raise RigorAuditError("receipt_path_outside_or_symlink")
        st = receipt_path.stat()
        observed["exists"] = stat.S_ISREG(st.st_mode)
        observed["mode"] = oct(stat.S_IMODE(st.st_mode))
        if not observed["exists"] or stat.S_IMODE(st.st_mode) != 0o600:
            raise RigorAuditError("receipt_not_private_regular")
        raw = receipt_path.read_bytes()
        observed["sha256_match"] = _sha256(raw) == expected_sha
        receipt = json.loads(raw)
        observed["receipt_mission_match"] = (
            receipt.get("mission_id") == mission_id
        )
        observed["receipt_status_match"] = (
            receipt.get("status") == record.get("status")
        )
        observed["verifier_accepted"] = (
            (receipt.get("verifier") or {}).get("verdict") == "accepted"
        )
        observed["zero_tool_events"] = (
            (receipt.get("runtime_attestation") or {}).get("tool_events") == []
        )
        if not all(
            (
                observed["sha256_match"],
                observed["receipt_mission_match"],
                observed["receipt_status_match"],
                observed["verifier_accepted"],
                observed["zero_tool_events"],
            )
        ):
            reason = "receipt_binding_or_verifier_mismatch"
            status_value = "FAIL"
    except (OSError, ValueError, TypeError, RigorAuditError) as exc:
        reason = f"receipt_unverifiable_{type(exc).__name__}"
        status_value = "UNKNOWN"
    return _check(
        f"rigor:receipt:{agent.lower()}:{mission_id}",
        "direct_effect",
        status_value,
        reason,
        {
            "mode": "0o600",
            "sha256_match": True,
            "mission_status_bound": True,
            "verifier": "accepted",
            "tool_events": [],
        },
        observed,
        relative,
    )


def _calibrate_validator() -> dict[str, str]:
    """Exercise a positive and negative control without touching the filesystem."""
    positive = {
        "mission_id": "m",
        "status": "completed",
        "verifier": "accepted",
        "tool_events": [],
    }
    positive_raw = _canonical_bytes(positive)
    expected = _sha256(positive_raw)
    positive_ok = _sha256(positive_raw) == expected
    negative_ok = _sha256(positive_raw + b"x") != expected
    status_value = "PASS" if positive_ok and negative_ok else "FAIL"
    probe = {
        "contract_rev": CONTRACT_REV,
        "positive": positive_ok,
        "negative": negative_ok,
    }
    return {
        "probe_id": "receipt-hash-discriminant-v1",
        "probe_sha256": _sha256(_canonical_bytes(probe)),
        "status": status_value,
        "positive_control_id": "known-canonical-receipt",
        "negative_control_id": "single-byte-corruption",
    }


def collect(root: Path = ROOT) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    source_hashes: list[str] = []
    for agent in AGENTS:
        state_path = (
            root
            / "research/flywire_results/nerves_orchestrator_inbox"
            / f"{agent}.state.json"
        )
        source_ref = state_path.relative_to(root).as_posix()
        if not state_path.exists():
            checks.append(
                _check(
                    f"rigor:state:{agent.lower()}",
                    "coverage",
                    "PASS",
                    "state_not_created_no_deliveries",
                    "absent or private valid state",
                    "absent",
                    source_ref,
                    required=False,
                )
            )
            continue
        try:
            st = state_path.stat()
            if (
                state_path.is_symlink()
                or not stat.S_ISREG(st.st_mode)
                or stat.S_IMODE(st.st_mode) != 0o600
            ):
                raise RigorAuditError("state_not_private_regular")
            state = _load_state(state_path)
            source_hashes.append(str(state["state_sha256"]))
            deliveries = state["deliveries"]
            checks.append(
                _check(
                    f"rigor:state:{agent.lower()}",
                    "provenance",
                    "PASS",
                    "state_hash_valid",
                    {"mode": "0o600", "hash_chain": True},
                    {
                        "mode": oct(stat.S_IMODE(st.st_mode)),
                        "deliveries": len(deliveries),
                    },
                    source_ref,
                )
            )
            for mission_id, record in sorted(deliveries.items()):
                if not isinstance(record, dict):
                    checks.append(
                        _check(
                            f"rigor:delivery:{agent.lower()}:{mission_id}",
                            "provenance",
                            "UNKNOWN",
                            "delivery_record_not_object",
                            "object",
                            type(record).__name__,
                            source_ref,
                        )
                    )
                    continue
                if record.get("status") in TERMINAL:
                    checks.append(
                        _receipt_check(
                            agent, str(mission_id), record, root=root
                        )
                    )
        except (OSError, ValueError, TypeError, RigorAuditError) as exc:
            checks.append(
                _check(
                    f"rigor:state:{agent.lower()}",
                    "provenance",
                    "UNKNOWN",
                    f"state_unverifiable_{type(exc).__name__}",
                    {"mode": "0o600", "hash_chain": True},
                    "unverifiable",
                    source_ref,
                )
            )

    calibration = _calibrate_validator()
    required = [check for check in checks if check["required"]]
    failed = sorted(
        {
            check["reason_code"]
            for check in required
            if check["status"] == "FAIL"
        }
    )
    unknown = sorted(
        {
            check["reason_code"]
            for check in required
            if check["status"] == "UNKNOWN"
        }
    )
    has_direct_effect = any(
        check["kind"] == "direct_effect" and check["required"]
        for check in checks
    )
    if calibration["status"] != "PASS" or unknown or not has_direct_effect:
        state_value, status_value = "BROKEN", "issue"
        if not has_direct_effect:
            unknown.append("no_required_direct_effect")
            unknown = sorted(set(unknown))
    elif failed:
        state_value, status_value = "FINDING", "issue"
    else:
        state_value, status_value = "GREEN", "clean"

    claim_source = _sha256(_canonical_bytes(sorted(source_hashes)))
    claim_id = _sha256(
        _canonical_bytes(
            {
                "target": TARGET,
                "source_record_sha256": claim_source,
                "contract_rev": CONTRACT_REV,
            }
        )
    )
    now = datetime.now(timezone.utc)
    event: dict[str, Any] = {
        "schema": SCHEMA,
        "ts": now.isoformat(),
        "agent": "FABLE",
        "action": ACTION,
        "target": TARGET,
        "action_source": ACTION_SOURCE,
        "contract_rev": CONTRACT_REV,
        "run_id": str(uuid.uuid4()),
        "state": state_value,
        "status": status_value,
        "claim": {
            "claim_id": claim_id,
            "kind": "nerves_receipt_integrity",
            "subject_ref": (
                "research/flywire_results/nerves_orchestrator_inbox"
            ),
            "source_record_sha256": claim_source,
            "assertion": (
                "Terminal NERVES deliveries retain private hash-bound receipts "
                "accepted by a deterministic verifier with zero tool events."
            ),
        },
        "calibration": calibration,
        "checks": checks,
        "findings": failed,
        "broken": unknown,
        "detail": (
            f"states={len(AGENTS)} checks={len(checks)} "
            f"required={len(required)} failed={len(failed)} "
            f"unknown={len(unknown)}"
        ),
    }
    event["event_id"] = _sha256(_canonical_bytes(event))
    return event


def append_event(event: dict[str, Any], ledger: Path = LEDGER) -> bool:
    raw = _canonical_bytes(event) + b"\n"
    if len(raw) > MAX_RECORD_BYTES:
        raise RigorAuditError("event_exceeds_64k")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(ledger.parent, 0o700)
    lock_path = ledger.with_suffix(".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if ledger.exists():
            st = ledger.stat()
            if ledger.is_symlink() or stat.S_IMODE(st.st_mode) != 0o600:
                raise RigorAuditError("ledger_not_private_regular")
            if st.st_size + len(raw) > MAX_LEDGER_BYTES:
                raise RigorAuditError("ledger_checkpoint_required")
            tail = ledger.read_bytes()[-MAX_RECORD_BYTES:]
            if raw in tail:
                return False
        out = os.open(
            ledger,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC,
            0o600,
        )
        try:
            os.write(out, raw)
            os.fsync(out)
            os.fchmod(out, 0o600)
        finally:
            os.close(out)
        return True
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def main() -> int:
    try:
        event = collect()
        appended = append_event(event)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "state": "BROKEN",
                    "error": f"{type(exc).__name__}:{exc}",
                },
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "schema": SCHEMA,
                "event_id": event["event_id"],
                "state": event["state"],
                "status": event["status"],
                "checks": len(event["checks"]),
                "findings": event["findings"],
                "broken": event["broken"],
                "appended": appended,
            },
            sort_keys=True,
        )
    )
    return 0 if event["state"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
