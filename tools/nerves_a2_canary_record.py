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
ATTESTATION_DIR = (
    ROOT / "research/flywire_results/nerves_a2_soak/attestations"
)
JARVIS_STATE = (
    ROOT
    / "research/flywire_results/nerves_orchestrator_inbox/JARVIS.state.json"
)
JARVIS_PROFILE = ROOT / ".claude/agents/nerves-jarvis-reasoner.md"
SOAK_SCHEMA = "seal.nerves.a2-soak.v2"
CANARY_SCHEMA = "seal.nerves.a2-soak-canary.v2"
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


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_time(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise CanaryRecordError(f"{label}_missing")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError as exc:
        raise CanaryRecordError(f"{label}_invalid") from exc


def _workspace_private_file(
    path: Path,
    label: str,
    *,
    workspace_root: Path = ROOT,
) -> tuple[Path, bytes, str]:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise CanaryRecordError(f"{label}_unreadable") from exc
    if not resolved.is_relative_to(workspace_root.resolve()):
        raise CanaryRecordError(f"{label}_outside_workspace")
    if not resolved.is_file() or resolved.is_symlink():
        raise CanaryRecordError(f"{label}_not_regular")
    if resolved.stat().st_mode & 0o077:
        raise CanaryRecordError(f"{label}_not_private")
    raw = resolved.read_bytes()
    return resolved, raw, hashlib.sha256(raw).hexdigest()


def _workspace_private_object(
    path: Path,
    label: str,
    *,
    workspace_root: Path = ROOT,
) -> tuple[Path, dict, str]:
    resolved, raw, digest = _workspace_private_file(
        path,
        label,
        workspace_root=workspace_root,
    )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CanaryRecordError(f"{label}_invalid_json") from exc
    if not isinstance(value, dict):
        raise CanaryRecordError(f"{label}_not_object")
    return resolved, value, digest


def _validate_transcript_snapshot(
    path_value: object,
    *,
    expected_sha256: object,
    expected_bytes: object,
    label: str,
    workspace_root: Path = ROOT,
) -> None:
    if not isinstance(path_value, str):
        raise CanaryRecordError(f"{label}_path_missing")
    path, _, digest = _workspace_private_file(
        Path(path_value),
        label,
        workspace_root=workspace_root,
    )
    if digest != expected_sha256 or path.stat().st_size != expected_bytes:
        raise CanaryRecordError(f"{label}_custody_mismatch")


def _validate_route_attestation(
    *,
    agent: str,
    mission_id: str,
    receipt_path: Path,
    soak_started_at: datetime,
    workspace_root: Path = ROOT,
    jarvis_state_path: Path = JARVIS_STATE,
    jarvis_profile_path: Path = JARVIS_PROFILE,
) -> tuple[Path, str, str, str, str]:
    path, receipt, digest = _workspace_private_object(
        receipt_path,
        "route_receipt",
        workspace_root=workspace_root,
    )
    if (
        receipt.get("mission_id") != mission_id
        or receipt.get("status") != "completed"
    ):
        raise CanaryRecordError("route_receipt_not_completed_or_bound")
    started = _parse_time(receipt.get("started_at"), "receipt_started_at")
    finished = _parse_time(receipt.get("finished_at"), "receipt_finished_at")
    if started < soak_started_at or finished < started:
        raise CanaryRecordError("route_receipt_outside_soak_window")
    runtime = receipt.get("runtime_attestation")
    if not isinstance(runtime, dict):
        raise CanaryRecordError("route_runtime_attestation_missing")

    if agent == "JARVIS":
        if (
            receipt.get("schema") != "seal.nerves.orchestrator-receipt.v1"
            or receipt.get("worker_kind") != "native_subagent"
            or receipt.get("tools_used") != ["SendMessage"]
            or receipt.get("verifier_verdict", {}).get("verdict")
            != "accepted"
            or runtime.get("platform") != "claude_code_agent_tool"
            or runtime.get("profile") != "nerves-jarvis-reasoner"
            or runtime.get("tools_configured") != ["SendMessage"]
            or runtime.get("tool_events") != ["SendMessage:main"]
            or runtime.get("skills_configured") != []
            or runtime.get("mcp_servers_configured") != []
            or runtime.get("max_turns") != 1
            or runtime.get("profile_sha256") != _sha256_file(jarvis_profile_path)
        ):
            raise CanaryRecordError("jarvis_native_boundary_invalid")
        state = _private_object(jarvis_state_path)
        delivery = state.get("deliveries", {}).get(mission_id)
        if not isinstance(delivery, dict):
            raise CanaryRecordError("jarvis_delivery_missing")
        state_receipt = delivery.get("receipt")
        if (
            delivery.get("status") != "completed"
            or not isinstance(state_receipt, dict)
            or state_receipt.get("receipt_sha256") != digest
            or Path(str(delivery.get("receipt_path", ""))).resolve() != path
            or delivery.get("claim", {}).get("claimed_at") is None
            or delivery.get("platform_binding", {}).get("bound_at") is None
        ):
            raise CanaryRecordError("jarvis_delivery_terminal_mismatch")
        for label, timestamp in (
            ("jarvis_delivery_created_at", delivery.get("created_at")),
            ("jarvis_claimed_at", delivery["claim"].get("claimed_at")),
            (
                "jarvis_platform_bound_at",
                delivery["platform_binding"].get("bound_at"),
            ),
            ("jarvis_receipt_submitted_at", state_receipt.get("submitted_at")),
        ):
            if _parse_time(timestamp, label) < soak_started_at:
                raise CanaryRecordError(f"{label}_before_soak")
        handoff_path, _, handoff_digest = _workspace_private_object(
            Path(str(delivery.get("inbox_path", ""))),
            "jarvis_handoff",
            workspace_root=workspace_root,
        )
        if (
            handoff_digest != delivery.get("handoff_sha256")
            or handoff_path.name != f"{mission_id}.handoff.json"
        ):
            raise CanaryRecordError("jarvis_handoff_custody_mismatch")
        _validate_transcript_snapshot(
            runtime.get("parent_transcript_snapshot_path"),
            expected_sha256=runtime.get("parent_transcript_prefix_sha256"),
            expected_bytes=runtime.get("parent_transcript_prefix_bytes"),
            label="jarvis_parent_transcript",
            workspace_root=workspace_root,
        )
        _validate_transcript_snapshot(
            runtime.get("child_transcript_snapshot_path"),
            expected_sha256=runtime.get("child_transcript_prefix_sha256"),
            expected_bytes=runtime.get("child_transcript_prefix_bytes"),
            label="jarvis_child_transcript",
            workspace_root=workspace_root,
        )
        kind = "jarvis_native_receipt"
    else:
        if (
            receipt.get("schema") != "seal.nerves.orchestrator-receipt.v3"
            or receipt.get("worker_kind") != "local_ollama_subagent"
            or runtime.get("platform") != "ollama_generate_json"
            or runtime.get("isolation") != "no_tool_api"
            or runtime.get("tool_events") != []
            or runtime.get("endpoint")
            != "http://127.0.0.1:11434/api/generate"
        ):
            raise CanaryRecordError("local_ollama_boundary_invalid")
        kind = "local_ollama_receipt"
    return path, digest, kind, started.isoformat(), finished.isoformat()


def _write_private_once(path: Path, raw: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
    except FileExistsError:
        if path.read_bytes() != raw or path.stat().st_mode & 0o077:
            raise CanaryRecordError("attestation_replay_mismatch")
        return path
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    if path.read_bytes() != raw or path.stat().st_mode & 0o077:
        raise CanaryRecordError("attestation_write_unverifiable")
    return path


def record_success(
    *,
    agent: str,
    mission_id: str,
    receipt_path: Path,
    assertions: Mapping[str, bool],
    principal_ack_latency_ms: int | None = None,
    state_path: Path = SOAK_STATE,
    canary_dir: Path = CANARY_DIR,
    workspace_root: Path = ROOT,
    jarvis_state_path: Path = JARVIS_STATE,
    jarvis_profile_path: Path = JARVIS_PROFILE,
) -> Path:
    """Write one immutable P5 record derived from a successful live canary."""
    if agent not in ROUTES:
        raise CanaryRecordError("agent_not_admitted")
    try:
        normalized_mission = str(uuid.UUID(mission_id))
    except ValueError as exc:
        raise CanaryRecordError("mission_id_invalid") from exc
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
        state.get("schema") != SOAK_SCHEMA
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
    (
        resolved_receipt,
        receipt_sha256,
        attestation_kind,
        attested_started_at,
        attested_finished_at,
    ) = _validate_route_attestation(
        agent=agent,
        mission_id=normalized_mission,
        receipt_path=receipt_path,
        soak_started_at=started,
        workspace_root=workspace_root,
        jarvis_state_path=jarvis_state_path,
        jarvis_profile_path=jarvis_profile_path,
    )
    canary_id = str(
        uuid.uuid5(
            CANARY_NAMESPACE,
            f"{fingerprint}:{agent}:{normalized_mission}:{receipt_sha256}",
        )
    )
    record = {
        "schema": CANARY_SCHEMA,
        "canary_id": canary_id,
        "release_fingerprint": fingerprint,
        "recorded_at": now.isoformat(),
        "kind": "A2_ROUTE_CANARY",
        "ok": True,
        "risk_class": "A2_READ_ONLY",
        "mutations": 0,
        "evidence_sha256": receipt_sha256,
        "mission_id": normalized_mission,
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
        "utility_basis": "controlled_route_completion",
        "confidence": 1.0,
        "outcome": 1,
        "human_corrections": 0,
        "harm_avoided": 0,
        "harm_caused": 0,
        "harm_avoided_method": "none_claimed_controlled_canary",
        "principal_ack_latency_ms": principal_ack_latency_ms,
        "attestation_kind": attestation_kind,
        "attestation_path": str(resolved_receipt),
        "attestation_sha256": receipt_sha256,
        "attested_started_at": attested_started_at,
        "attested_finished_at": attested_finished_at,
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
    attestation_dir: Path = ATTESTATION_DIR,
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
        state.get("schema") != SOAK_SCHEMA
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

    evidence_raw = _canonical(dict(evidence)) + b"\n"
    evidence_sha256 = hashlib.sha256(evidence_raw).hexdigest()
    evidence_path = _write_private_once(
        attestation_dir / f"principal-ack-{evidence_sha256}.json",
        evidence_raw,
    )
    attested_started_at = _parse_time(
        evidence["request_created_at"], "ack_request_created_at"
    )
    attested_finished_at = _parse_time(
        evidence["ack_created_at"], "ack_created_at"
    )
    if (
        attested_started_at < started
        or attested_finished_at < attested_started_at
        or now < attested_finished_at
    ):
        raise CanaryRecordError("principal_ack_outside_soak_window")
    canary_id = str(
        uuid.uuid5(
            CANARY_NAMESPACE,
            f"{fingerprint}:PRINCIPAL_ACK:{evidence_sha256}",
        )
    )
    record = {
        "schema": CANARY_SCHEMA,
        "canary_id": canary_id,
        "release_fingerprint": fingerprint,
        "recorded_at": now.isoformat(),
        "kind": "PRINCIPAL_ACK_CANARY",
        "ok": True,
        "risk_class": "A2_READ_ONLY",
        "mutations": 0,
        "evidence_sha256": evidence_sha256,
        "mission_id": None,
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
        "utility_basis": "measured_principal_ack_latency",
        "confidence": 1.0,
        "outcome": 1,
        "human_corrections": 0,
        "harm_avoided": 0,
        "harm_caused": 0,
        "harm_avoided_method": "principal_ack_under_2s",
        "principal_ack_latency_ms": principal_ack_latency_ms,
        "attestation_kind": "principal_ack_evidence",
        "attestation_path": str(evidence_path.resolve()),
        "attestation_sha256": evidence_sha256,
        "attested_started_at": attested_started_at.isoformat(),
        "attested_finished_at": attested_finished_at.isoformat(),
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
