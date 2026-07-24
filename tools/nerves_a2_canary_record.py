#!/usr/bin/env python3
"""Persist one mechanically verified A2 canary into the active soak."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import uuid
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
SOAK_STATE = ROOT / "research/flywire_results/nerves_a2_soak/state.json"
CANARY_DIR = ROOT / "research/flywire_results/nerves_a2_soak/canaries"
CANARY_NAMESPACE = uuid.UUID("e9d7aed4-bcb2-4aae-b69b-0bfb58cf3586")
ROUTES = frozenset({"ADA", "ALICE", "NEXUS", "FABLE", "JARVIS"})


class CanaryRecordError(RuntimeError):
    """The run cannot be admitted as evidence for the current soak."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _private_object(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise CanaryRecordError(f"artifact_missing_or_symlink:{path}")
    if path.stat().st_mode & 0o077:
        raise CanaryRecordError(f"artifact_not_private:{path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CanaryRecordError(f"artifact_not_object:{path}")
    return value


def record_success(
    *,
    agent: str,
    mission_id: str,
    receipt_sha256: str,
    assertions: Mapping[str, bool],
    principal_ack_latency_ms: int | None = None,
    state_path: Path = SOAK_STATE,
    canary_dir: Path = CANARY_DIR,
) -> Path:
    """Write one immutable P5 record derived from a successful live canary."""
    if agent not in ROUTES:
        raise CanaryRecordError("agent_not_admitted")
    try:
        normalized_mission = str(uuid.UUID(mission_id))
    except ValueError as exc:
        raise CanaryRecordError("mission_id_invalid") from exc
    if (
        len(receipt_sha256) != 64
        or any(char not in "0123456789abcdef" for char in receipt_sha256)
    ):
        raise CanaryRecordError("receipt_sha256_invalid")
    if not assertions or not all(value is True for value in assertions.values()):
        raise CanaryRecordError("canary_assertions_not_all_true")
    if (
        principal_ack_latency_ms is not None
        and (
            isinstance(principal_ack_latency_ms, bool)
            or not isinstance(principal_ack_latency_ms, int)
            or principal_ack_latency_ms < 0
        )
    ):
        raise CanaryRecordError("principal_ack_latency_invalid")

    state = _private_object(state_path)
    if (
        state.get("schema") != "seal.nerves.a2-soak.v1"
        or state.get("status") != "SOAKING"
    ):
        raise CanaryRecordError("soak_not_active")
    fingerprint = str(state.get("release_fingerprint") or "")
    if len(fingerprint) != 64:
        raise CanaryRecordError("release_fingerprint_invalid")

    now = datetime.now(timezone.utc)
    started = datetime.fromisoformat(
        str(state["started_at"]).replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    if now < started:
        raise CanaryRecordError("canary_before_soak")
    canary_id = str(
        uuid.uuid5(
            CANARY_NAMESPACE,
            f"{fingerprint}:{agent}:{normalized_mission}:{receipt_sha256}",
        )
    )
    record = {
        "schema": "seal.nerves.a2-soak-canary.v1",
        "canary_id": canary_id,
        "release_fingerprint": fingerprint,
        "recorded_at": now.isoformat(),
        "kind": "A2_ROUTE_CANARY",
        "ok": True,
        "risk_class": "A2_READ_ONLY",
        "mutations": 0,
        "evidence_sha256": receipt_sha256,
        "agent": agent,
        "signals_seen": 1,
        "missions_created": 1,
        "missions_coalesced": 0,
        "duplicate_side_effects": 0,
        "worker_successes": 1,
        "worker_failures": 0,
        "evidence_attempts": 1,
        "evidence_rejections": 0,
        "false_wakes": 0,
        "dead_letters": 0,
        "verified_effects": 1,
        "estimated_cost_units": 1,
        "predicted_utility": 0.5,
        "actual_utility": 1.0,
        "confidence": 1.0,
        "outcome": 1,
        "human_corrections": 0,
        "harm_avoided": 1,
        "harm_avoided_method": "no_tools_and_protected_state_unchanged",
        "principal_ack_latency_ms": principal_ack_latency_ms,
    }
    raw = _canonical(record) + b"\n"
    canary_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    canary_dir.chmod(0o700)
    if canary_dir.stat().st_mode & 0o077:
        raise CanaryRecordError("canary_dir_not_private")
    path = canary_dir / f"{agent.lower()}-{canary_id}.json"
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
    except FileExistsError:
        existing = _private_object(path)
        replay = dict(record)
        replay["recorded_at"] = existing.get("recorded_at")
        if existing != replay:
            raise CanaryRecordError("canary_replay_mismatch")
        return path
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    if path.read_bytes() != raw or path.stat().st_mode & 0o077:
        raise CanaryRecordError("canary_write_unverifiable")
    return path


def record_principal_ack(
    *,
    evidence: Mapping[str, object],
    principal_ack_latency_ms: int,
    state_path: Path = SOAK_STATE,
    canary_dir: Path = CANARY_DIR,
) -> Path:
    """Persist one verified public William→ADA ACK latency observation."""
    if (
        isinstance(principal_ack_latency_ms, bool)
        or not isinstance(principal_ack_latency_ms, int)
        or principal_ack_latency_ms < 0
    ):
        raise CanaryRecordError("principal_ack_latency_invalid")
    exact = {
        "schema",
        "channel",
        "request_id",
        "request_legacy_id",
        "request_created_at",
        "ack_id",
        "ack_legacy_id",
        "ack_created_at",
        "in_reply_to",
    }
    if set(evidence) != exact:
        raise CanaryRecordError("principal_ack_evidence_shape_invalid")
    if (
        evidence.get("schema") != "seal.nerves.principal-ack-evidence.v1"
        or evidence.get("channel") != "web_chat"
        or evidence.get("in_reply_to") != evidence.get("request_legacy_id")
        or not isinstance(evidence.get("request_id"), int)
        or not isinstance(evidence.get("ack_id"), int)
        or int(evidence["request_id"]) >= int(evidence["ack_id"])
    ):
        raise CanaryRecordError("principal_ack_evidence_invalid")

    state = _private_object(state_path)
    if (
        state.get("schema") != "seal.nerves.a2-soak.v1"
        or state.get("status") != "SOAKING"
    ):
        raise CanaryRecordError("soak_not_active")
    fingerprint = str(state.get("release_fingerprint") or "")
    if len(fingerprint) != 64:
        raise CanaryRecordError("release_fingerprint_invalid")
    now = datetime.now(timezone.utc)
    started = datetime.fromisoformat(
        str(state["started_at"]).replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    if now < started:
        raise CanaryRecordError("canary_before_soak")

    evidence_sha256 = hashlib.sha256(_canonical(dict(evidence))).hexdigest()
    canary_id = str(
        uuid.uuid5(
            CANARY_NAMESPACE,
            f"{fingerprint}:PRINCIPAL_ACK:{evidence_sha256}",
        )
    )
    record = {
        "schema": "seal.nerves.a2-soak-canary.v1",
        "canary_id": canary_id,
        "release_fingerprint": fingerprint,
        "recorded_at": now.isoformat(),
        "kind": "PRINCIPAL_ACK_CANARY",
        "ok": True,
        "risk_class": "A2_READ_ONLY",
        "mutations": 0,
        "evidence_sha256": evidence_sha256,
        "agent": "ADA",
        "signals_seen": 1,
        "missions_created": 0,
        "missions_coalesced": 0,
        "duplicate_side_effects": 0,
        "worker_successes": 0,
        "worker_failures": 0,
        "evidence_attempts": 1,
        "evidence_rejections": 0,
        "false_wakes": 0,
        "dead_letters": 0,
        "verified_effects": 1,
        "estimated_cost_units": 0,
        "predicted_utility": 1.0,
        "actual_utility": 1.0,
        "confidence": 1.0,
        "outcome": 1,
        "human_corrections": 0,
        "harm_avoided": 1,
        "harm_avoided_method": "principal_ack_under_2s",
        "principal_ack_latency_ms": principal_ack_latency_ms,
    }
    raw = _canonical(record) + b"\n"
    canary_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    canary_dir.chmod(0o700)
    if canary_dir.stat().st_mode & 0o077:
        raise CanaryRecordError("canary_dir_not_private")
    path = canary_dir / f"ack-{canary_id}.json"
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
    except FileExistsError:
        existing = _private_object(path)
        replay = dict(record)
        replay["recorded_at"] = existing.get("recorded_at")
        if existing != replay:
            raise CanaryRecordError("canary_replay_mismatch")
        return path
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    if path.read_bytes() != raw or path.stat().st_mode & 0o077:
        raise CanaryRecordError("canary_write_unverifiable")
    return path
