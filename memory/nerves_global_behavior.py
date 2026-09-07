#!/usr/bin/env python3
"""Protocol validator for the twelve mandatory GLOBAL nerve envelopes.

This module proves shape validation, fail-closed semantics, private append-only
recording, and replay idempotency.  It does *not* prove that a live producer
caused or a live consumer observed any GLOBAL behavior.  Production behavior
evidence must come from independently verified adapters and is gated
separately.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

from nerves_global_catalog import (
    CATALOG_PATH,
    REQUIRED_AGENTS,
    REQUIRED_GLOBAL_NERVES,
    validate_catalog,
)


class GlobalBehaviorError(ValueError):
    """A GLOBAL event is malformed, unsafe, or does not prove its effect."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _require(signal: dict[str, Any], **expected: Any) -> None:
    for key, value in expected.items():
        if signal.get(key) != value:
            raise GlobalBehaviorError(f"behavior_proof_failed:{key}")


def _care(signal: dict[str, Any], _: str) -> None:
    if signal.get("risk_class") not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
        raise GlobalBehaviorError("behavior_proof_failed:risk_class")
    _require(signal, protective_route=True, mutation_count=0)


def _continuity(signal: dict[str, Any], _: str) -> None:
    before = signal.get("checkpoint_sha256")
    after = signal.get("restored_sha256")
    if not _is_sha256(before) or before != after:
        raise GlobalBehaviorError("behavior_proof_failed:restored_sha256")
    _require(signal, duplicate_effects=0)


def _communication(signal: dict[str, Any], event_id: str) -> None:
    _require(
        signal,
        channel_exact=True,
        delivered=True,
        ack_for_event=event_id,
        duplicate_deliveries=0,
    )


def _human_priority(signal: dict[str, Any], _: str) -> None:
    if signal.get("sender") not in {"William", "Henry", "Kinger"}:
        raise GlobalBehaviorError("behavior_proof_failed:sender")
    ack_ms = signal.get("ack_ms")
    if not isinstance(ack_ms, int) or ack_ms < 0 or ack_ms > 2000:
        raise GlobalBehaviorError("behavior_proof_failed:ack_ms")
    _require(signal, main_free=True, background_delegated=True)


def _identity(signal: dict[str, Any], agent: str) -> None:
    _require(
        signal,
        session_agent=agent,
        tenant_source="session_user",
        forgery_ignored=True,
    )


def _privacy(signal: dict[str, Any], agent: str) -> None:
    _require(
        signal,
        requested_scope=f"dm:{agent.lower()}:william",
        foreign_scope_denied=True,
        foreign_payload_exposed=False,
    )


def _runtime_health(signal: dict[str, Any], _: str) -> None:
    if signal.get("probe_state") not in {"FAILED", "DEGRADED"}:
        raise GlobalBehaviorError("behavior_proof_failed:probe_state")
    _require(signal, independently_confirmed=True, classification="FINDING")


def _coordination(signal: dict[str, Any], _: str) -> None:
    _require(signal, claim_count=1, writer_count=1, duplicate_effects=0)


def _truthfulness(signal: dict[str, Any], _: str) -> None:
    builder = signal.get("builder")
    verifier = signal.get("verifier")
    if not builder or not verifier or builder == verifier:
        raise GlobalBehaviorError("behavior_proof_failed:independent_verifier")
    _require(signal, post_probe=True, fresh_evidence=True)


def _stop_hold(signal: dict[str, Any], _: str) -> None:
    if signal.get("command") not in {"STOP", "HOLD"}:
        raise GlobalBehaviorError("behavior_proof_failed:command")
    _require(signal, lease_released=True, effects_after_command=0)


def _initiative(signal: dict[str, Any], _: str) -> None:
    _require(
        signal,
        mission_risk="A2_READ_ONLY",
        useful=True,
        bounded=True,
        human_pending=False,
        incident_pending=False,
    )


def _learning(signal: dict[str, Any], _: str) -> None:
    outcomes = signal.get("verified_outcomes")
    if not isinstance(outcomes, int) or outcomes < 3:
        raise GlobalBehaviorError("behavior_proof_failed:verified_outcomes")
    _require(
        signal,
        authority_before="A2_READ_ONLY",
        authority_after="A2_READ_ONLY",
        baseline_only=True,
    )


VALIDATORS: dict[str, Callable[[dict[str, Any], str], None]] = {
    "care_protection": _care,
    "continuity": _continuity,
    "communication_delivery": _communication,
    "human_priority_response": _human_priority,
    "identity_integrity": _identity,
    "privacy_boundary": _privacy,
    "runtime_health": _runtime_health,
    "coordination_non_collision": _coordination,
    "effect_truthfulness": _truthfulness,
    "stop_hold_preemption": _stop_hold,
    "productive_initiative": _initiative,
    "verified_learning": _learning,
}


@dataclass(frozen=True)
class BehaviorResult:
    event_id: str
    agent: str
    nerve_id: str
    status: str
    joined: bool
    record_sha256: str


class GlobalBehaviorEngine:
    def __init__(
        self,
        ledger_path: Path,
        *,
        catalog_path: Path = CATALOG_PATH,
    ) -> None:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        validate_catalog(catalog)
        self.catalog = catalog
        self.ledger_path = ledger_path
        self.lock_path = ledger_path.with_suffix(ledger_path.suffix + ".lock")
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.ledger_path.parent, 0o700)

    def _validate_event(self, event: dict[str, Any]) -> None:
        required = {
            "schema",
            "event_id",
            "agent",
            "nerve_id",
            "risk_class",
            "requested_action",
            "evidence_sha256",
            "signal",
        }
        if set(event) != required:
            raise GlobalBehaviorError("event_shape_invalid")
        if event["schema"] != "seal.nerves.global_behavior_event.v1":
            raise GlobalBehaviorError("event_schema_invalid")
        if event["agent"] not in REQUIRED_AGENTS:
            raise GlobalBehaviorError("event_agent_invalid")
        if event["nerve_id"] not in REQUIRED_GLOBAL_NERVES:
            raise GlobalBehaviorError("event_nerve_invalid")
        binding = next(
            item for item in self.catalog["bindings"]
            if item["agent"] == event["agent"]
        )
        if event["nerve_id"] not in binding["inherits"]:
            raise GlobalBehaviorError("event_nerve_not_inherited")
        if event["risk_class"] != "A2_READ_ONLY":
            raise GlobalBehaviorError("event_authority_invalid")
        if event["requested_action"] != "observe_and_record":
            raise GlobalBehaviorError("event_action_invalid")
        if not _is_sha256(event["evidence_sha256"]):
            raise GlobalBehaviorError("event_evidence_hash_invalid")
        if not isinstance(event["signal"], dict):
            raise GlobalBehaviorError("event_signal_invalid")

    def _load_records(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        if self.ledger_path.stat().st_mode & 0o077:
            raise GlobalBehaviorError("ledger_permissions_not_private")
        records: list[dict[str, Any]] = []
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records

    def process(self, event: dict[str, Any]) -> BehaviorResult:
        self._validate_event(event)
        validator = VALIDATORS[event["nerve_id"]]
        validator(event["signal"], event["agent"] if event["nerve_id"] in {
            "identity_integrity",
            "privacy_boundary",
        } else event["event_id"])

        self.lock_path.touch(mode=0o600, exist_ok=True)
        os.chmod(self.lock_path, 0o600)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            records = self._load_records()
            prior = next(
                (record for record in records if record["event_id"] == event["event_id"]),
                None,
            )
            if prior is not None:
                if prior["event_sha256"] != hashlib.sha256(
                    _canonical_bytes(event)
                ).hexdigest():
                    raise GlobalBehaviorError("event_id_replay_drift")
                return BehaviorResult(
                    event_id=prior["event_id"],
                    agent=prior["agent"],
                    nerve_id=prior["nerve_id"],
                    status=prior["status"],
                    joined=True,
                    record_sha256=prior["record_sha256"],
                )
            record = {
                "schema": "seal.nerves.global_behavior_record.v1",
                "event_id": event["event_id"],
                "agent": event["agent"],
                "nerve_id": event["nerve_id"],
                "status": "PROTOCOL_VALIDATED",
                "event_sha256": hashlib.sha256(_canonical_bytes(event)).hexdigest(),
                "evidence_sha256": event["evidence_sha256"],
                "effect": "typed_protocol_envelope_recorded",
            }
            record["record_sha256"] = hashlib.sha256(
                _canonical_bytes(record)
            ).hexdigest()
            payload = _canonical_bytes(record) + b"\n"
            fd = os.open(
                self.ledger_path,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                0o600,
            )
            with os.fdopen(fd, "ab") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(self.ledger_path, 0o600)
            return BehaviorResult(
                event_id=record["event_id"],
                agent=record["agent"],
                nerve_id=record["nerve_id"],
                status=record["status"],
                joined=False,
                record_sha256=record["record_sha256"],
            )


def positive_signal(nerve_id: str, agent: str, event_id: str) -> dict[str, Any]:
    """Return a synthetic fixture for protocol tests, never effect evidence."""
    checkpoint = hashlib.sha256(f"{agent}:checkpoint".encode()).hexdigest()
    signals: dict[str, dict[str, Any]] = {
        "care_protection": {
            "risk_class": "HIGH",
            "protective_route": True,
            "mutation_count": 0,
        },
        "continuity": {
            "checkpoint_sha256": checkpoint,
            "restored_sha256": checkpoint,
            "duplicate_effects": 0,
        },
        "communication_delivery": {
            "channel_exact": True,
            "delivered": True,
            "ack_for_event": event_id,
            "duplicate_deliveries": 0,
        },
        "human_priority_response": {
            "sender": "William",
            "ack_ms": 250,
            "main_free": True,
            "background_delegated": True,
        },
        "identity_integrity": {
            "session_agent": agent,
            "tenant_source": "session_user",
            "forgery_ignored": True,
        },
        "privacy_boundary": {
            "requested_scope": f"dm:{agent.lower()}:william",
            "foreign_scope_denied": True,
            "foreign_payload_exposed": False,
        },
        "runtime_health": {
            "probe_state": "DEGRADED",
            "independently_confirmed": True,
            "classification": "FINDING",
        },
        "coordination_non_collision": {
            "claim_count": 1,
            "writer_count": 1,
            "duplicate_effects": 0,
        },
        "effect_truthfulness": {
            "builder": agent,
            "verifier": "NEXUS" if agent != "NEXUS" else "FABLE",
            "post_probe": True,
            "fresh_evidence": True,
        },
        "stop_hold_preemption": {
            "command": "HOLD",
            "lease_released": True,
            "effects_after_command": 0,
        },
        "productive_initiative": {
            "mission_risk": "A2_READ_ONLY",
            "useful": True,
            "bounded": True,
            "human_pending": False,
            "incident_pending": False,
        },
        "verified_learning": {
            "verified_outcomes": 3,
            "authority_before": "A2_READ_ONLY",
            "authority_after": "A2_READ_ONLY",
            "baseline_only": True,
        },
    }
    return signals[nerve_id]


def make_event(agent: str, nerve_id: str, *, suffix: str = "positive") -> dict[str, Any]:
    event_id = f"{agent.lower()}:{nerve_id}:{suffix}"
    return {
        "schema": "seal.nerves.global_behavior_event.v1",
        "event_id": event_id,
        "agent": agent,
        "nerve_id": nerve_id,
        "risk_class": "A2_READ_ONLY",
        "requested_action": "observe_and_record",
        "evidence_sha256": hashlib.sha256(event_id.encode()).hexdigest(),
        "signal": positive_signal(nerve_id, agent, event_id),
    }


def run_behavior_matrix(output_dir: Path) -> dict[str, Any]:
    """Exercise all protocol validators with synthetic fixtures.

    ``behavior_claims_proven`` remains zero by construction.
    """
    engine = GlobalBehaviorEngine(output_dir / "behavior_ledger.jsonl")
    accepted = 0
    joined = 0
    for agent in sorted(REQUIRED_AGENTS):
        for nerve_id in sorted(REQUIRED_GLOBAL_NERVES):
            event = make_event(agent, nerve_id)
            first = engine.process(event)
            replay = engine.process(event)
            if first.joined or not replay.joined:
                raise GlobalBehaviorError("dedup_effect_invalid")
            accepted += 1
            joined += 1
    return {
        "schema": "seal.nerves.global_behavior_protocol_matrix.v1",
        "ok": accepted == 72 and joined == 72,
        "agents": 6,
        "nerves": 12,
        "protocol_cells": accepted,
        "idempotent_replays": joined,
        "behavior_claims_proven": 0,
        "evidence_level": "synthetic_protocol_fixture",
        "production_mutations": 0,
        "ledger": str(engine.ledger_path),
    }
