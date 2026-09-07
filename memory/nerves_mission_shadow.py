#!/usr/bin/env python3
"""Shadow-only mission compiler and tamper-evident ledger for NERVES v4.

This module creates no workers and executes no actions. It converts a fresh
JARVIS integrity artifact into a schema-valid A2_READ_ONLY MissionEnvelope and
records exactly one ``mission_created`` event per integrity episode.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator

try:
    from memory.nerves_integrity_evidence_bundle import (
        MISSION_COMPILER_REV,
        skill_bundle_digest,
    )
    from memory.ssai_shadow.ledger import LedgerIntegrityError, ShadowLedger
except ModuleNotFoundError:  # direct execution from memory/ by systemd
    from nerves_integrity_evidence_bundle import (
        MISSION_COMPILER_REV,
        skill_bundle_digest,
    )
    from ssai_shadow.ledger import LedgerIntegrityError, ShadowLedger


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "docs/schemas/nerves_mission_envelope_v1.schema.json"
DEFAULT_LEDGER = ROOT / "research/flywire_results/nerves_missions_shadow_v4.jsonl"
DEFAULT_MANIFEST_DIR = (
    ROOT / "research/flywire_results/nerves_missions_shadow_manifests"
)
DEFAULT_SKILL = ROOT / "skills/seal-nerves-integrity-audit/SKILL.md"
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000000"
MISSION_NAMESPACE = uuid.UUID("ac10b91a-fac3-4dc0-9245-148b1af1d482")
ACTIONABLE_STATES = frozenset({"FINDING", "BROKEN"})


class MissionCompileError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OpenMissionResult:
    mission: dict[str, Any]
    created: bool
    sequence: int


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_finding(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _episode_key(record: Mapping[str, Any], *, episode_anchor: str) -> str:
    findings = sorted(_normalize_finding(str(item)) for item in record.get("findings", []))
    material = {
        "agent": record.get("agent"),
        "action": record.get("action"),
        "state": record.get("state"),
        "findings": findings,
        "episode_anchor": episode_anchor,
        "compiler_rev": MISSION_COMPILER_REV,
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_artifact_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MissionCompileError(f"artifact_invalid_json_line_{line_number}") from exc
        if not isinstance(value, dict):
            raise MissionCompileError(f"artifact_line_{line_number}_must_be_object")
        records.append(value)
    return records


def latest_actionable_episode(
    records: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], str] | None:
    last_green = "genesis"
    candidate: tuple[dict[str, Any], str] | None = None
    for raw in records:
        record = dict(raw)
        if record.get("state") == "GREEN":
            last_green = str(record.get("ts") or "green_without_timestamp")
            candidate = None
        elif record.get("state") in ACTIONABLE_STATES:
            candidate = (record, last_green)
    return candidate


def compile_jarvis_integrity_mission(
    record: Mapping[str, Any],
    *,
    artifact_path: Path,
    episode_anchor: str,
    validation_canary: bool = False,
    schema_path: Path = DEFAULT_SCHEMA,
    skill_path: Path = DEFAULT_SKILL,
    workspace_root: Path = ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    if record.get("agent") != "JARVIS":
        raise MissionCompileError("artifact_agent_must_be_JARVIS")
    if record.get("action") != "integrity_pulse":
        raise MissionCompileError("artifact_action_must_be_integrity_pulse")
    state = record.get("state")
    if validation_canary:
        if state != "GREEN":
            raise MissionCompileError("validation_canary_requires_GREEN")
    elif state not in ACTIONABLE_STATES:
        raise MissionCompileError("artifact_state_not_actionable")
    findings = [str(item).strip() for item in record.get("findings", []) if str(item).strip()]
    if not validation_canary and not findings:
        raise MissionCompileError("actionable_artifact_requires_findings")
    if validation_canary and findings:
        raise MissionCompileError("validation_canary_requires_no_findings")
    if not artifact_path.is_file():
        raise MissionCompileError("artifact_path_must_be_regular_file")
    if not skill_path.is_file():
        raise MissionCompileError("skill_path_missing")
    resolved_workspace = workspace_root.resolve(strict=True)
    resolved_artifact = artifact_path.resolve(strict=True)
    if not resolved_artifact.is_relative_to(resolved_workspace):
        raise MissionCompileError("artifact_path_outside_workspace")
    artifact_relative = resolved_artifact.relative_to(resolved_workspace)
    record_sha256 = hashlib.sha256(
        json.dumps(
            dict(record),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    episode_key = _episode_key(record, episode_anchor=episode_anchor)
    mission_uuid = uuid.uuid5(MISSION_NAMESPACE, episode_key)
    observed_at = str(record.get("ts") or (now or datetime.now(timezone.utc)).isoformat())
    mission = {
        "schema": "seal.nerves.mission.v1",
        "mission_id": str(mission_uuid),
        "idempotency_key": f"jarvis-integrity-{episode_key}",
        "agent": "JARVIS",
        "tenant_id": DEFAULT_TENANT_ID,
        "nerve_fire_id": f"jarvis-integrity:{observed_at}:{episode_key[:16]}",
        "correlation_id": episode_key,
        "nerve_layer": "AGENT_ROLE",
        "drive": "proactive" if validation_canary else "reactive",
        "specialty": "architecture_integrity_audit",
        "objective": (
            "Independently validate the fresh healthy JARVIS integrity baseline "
            "without mutation."
            if validation_canary
            else "Classify and reproduce the JARVIS integrity drift without mutation."
        ),
        "risk_class": "A2_READ_ONLY",
        "source_refs": [
            f"file:{artifact_relative}",
            f"record_sha256:{record_sha256}",
            f"episode_anchor:{episode_anchor}",
        ],
        "initiation_conditions": [
            f"integrity_pulse state={record['state']}",
            (
                "explicit P5 validation canary over a fresh real collector result"
                if validation_canary
                else "at least one fresh finding with provenance"
            ),
        ],
        "scope": {
            "workspace": str(resolved_workspace),
            "paths": [
                "tools/dependency_inventory.py",
                "tools/jarvis_nerves_watch.py",
                str(artifact_relative),
            ],
            "services": [],
            "network": "none",
        },
        "skills": [
            {
                "id": "seal-nerves-integrity-audit",
                "version": "1",
                "sha256": skill_bundle_digest(skill_path.parent),
            }
        ],
        "allowed_tools": [
            "filesystem_read",
            "git_read",
            "systemctl_read",
            "journalctl_read",
            "subprocess_allowlisted_read_only",
        ],
        "budgets": {"wall_seconds": 180, "max_attempts": 1, "token_budget": 12000},
        "expected_evidence": [
            "classification enum",
            "allowlisted command argv and return code",
            "stdout/stderr sha256 and bounded tails",
            "independent verifier verdict",
        ],
        "termination_conditions": [
            "typed evidence submitted",
            "scope invalid and worker abstains",
            "budget exhausted and mission fails closed",
            "William STOP or HOLD",
        ],
        "rollback": {
            "required": False,
            "plan": "No mutation is authorized; cancel worker and retain evidence.",
        },
        "builder": "JARVIS@mission",
        "verifier": "ADA+FABLE_OR_NEXUS",
        "confidence_prior": 0.5,
    }
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(mission),
        key=lambda error: list(error.path),
    )
    if errors:
        rendered = "; ".join(error.message for error in errors[:5])
        raise MissionCompileError(f"mission_schema_invalid:{rendered}")
    return mission


class ShadowMissionLedger:
    def __init__(self, path: Path = DEFAULT_LEDGER) -> None:
        self.path = path
        self._ledger = ShadowLedger(path)

    def _find(self, idempotency_key: str) -> tuple[dict[str, Any], int] | None:
        snapshot = self._ledger.snapshot()
        if not snapshot.verification.ok:
            raise LedgerIntegrityError(
                "shadow mission ledger invalid: "
                + "; ".join(snapshot.verification.errors)
            )
        for record in snapshot.records:
            event = record.event
            mission = event.get("mission")
            if (
                event.get("event_type") == "mission_created"
                and isinstance(mission, dict)
                and mission.get("idempotency_key") == idempotency_key
            ):
                return dict(mission), record.sequence
        return None

    def open_or_join(self, mission: Mapping[str, Any]) -> OpenMissionResult:
        idempotency_key = str(mission.get("idempotency_key") or "")
        if not idempotency_key:
            raise MissionCompileError("idempotency_key_required")
        existing = self._find(idempotency_key)
        if existing:
            return OpenMissionResult(existing[0], False, existing[1])
        event = {
            "event_type": "mission_created",
            "state": "mission_created",
            "mission": dict(mission),
        }
        try:
            record = self._ledger.append(event)
        except LedgerIntegrityError:
            existing = self._find(idempotency_key)
            if existing:
                return OpenMissionResult(existing[0], False, existing[1])
            raise
        os.chmod(self.path, 0o600)
        return OpenMissionResult(dict(mission), True, record.sequence)

    def verify(self):
        return self._ledger.verify()


def write_shadow_manifest(
    mission: Mapping[str, Any],
    directory: Path = DEFAULT_MANIFEST_DIR,
) -> Path:
    """Persist one immutable owner-only manifest for a future isolated worker."""

    mission_id = str(mission.get("mission_id") or "")
    try:
        uuid.UUID(mission_id)
    except ValueError as exc:
        raise MissionCompileError("mission_id_must_be_uuid") from exc
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / f"{mission_id}.json"
    encoded = (
        json.dumps(
            dict(mission),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        if path.is_symlink() or not path.is_file():
            raise MissionCompileError("existing_manifest_not_regular")
        if path.read_bytes() != encoded:
            raise MissionCompileError("existing_manifest_content_mismatch")
        if path.stat().st_mode & 0o077:
            raise MissionCompileError("existing_manifest_permissions_too_open")
        return path
    try:
        offset = 0
        while offset < len(encoded):
            written = os.write(fd, encoded[offset:])
            if written <= 0:
                raise OSError(f"short manifest write {offset}/{len(encoded)}")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return path
