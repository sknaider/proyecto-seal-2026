#!/usr/bin/env python3
"""Secure handoff from deterministic NERVES evidence to JARVIS' orchestrator.

The collector has already performed the bounded read-only observations.  This
module never runs those commands again.  It authenticates the mission,
evidence, provenance, and source custody; writes one durable private handoff;
optionally wakes the JARVIS primary through a constant local event; atomically
claims the mission before a native subagent is spawned; and validates the
subagent's typed, tool-free receipt.

It does not read DMs or databases, use network access, publish webchat, spawn a
model, or touch a daemon.  The durable inbox/state are authoritative; the live
feed is a retryable notification only.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any, Mapping
import uuid

from jsonschema import Draft202012Validator, FormatChecker
import yaml

from memory.nerves_integrity_evidence_bundle import (
    ALLOWED_TOOLS,
    EVIDENCE_SCHEMA,
    MAX_MANIFEST_BYTES,
    MAX_OUTPUT_BYTES,
    ROOT,
    SKILL_DIR,
    EvidenceBundleError,
    MissionAdmission,
    admit_mission,
)


DEFAULT_MISSION_SCHEMA = (
    ROOT / "docs/schemas/nerves_mission_envelope_v1.schema.json"
)
DEFAULT_RECEIPT_SCHEMA = (
    ROOT / "docs/schemas/nerves_orchestrator_receipt_v1.schema.json"
)
DEFAULT_INBOX = ROOT / "research/flywire_results/nerves_orchestrator_inbox/JARVIS"
DEFAULT_STATE = (
    ROOT / "research/flywire_results/nerves_orchestrator_inbox/JARVIS.state.json"
)
DEFAULT_LIVE_FEED = Path("/tmp/seal_events_JARVIS.log")

STATE_SCHEMA = "seal.nerves.handoff-state.v2"
HANDOFF_SCHEMA = "seal.nerves.orchestrator-handoff.v2"
RECEIPT_SCHEMA = "seal.nerves.orchestrator-receipt.v1"
MAX_PROVENANCE_BYTES = 1_048_576
MAX_HANDOFF_BYTES = 4_194_304
MAX_LIVE_FEED_SCAN_BYTES = 33_554_432
WORKER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:@/+~-]{1,160}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
TERMINAL_STATES = frozenset({"completed", "failed", "abstained"})
NATIVE_PLATFORM = "claude_code_agent_tool"
NATIVE_PROFILE = "nerves-jarvis-reasoner"
NATIVE_PROFILE_RELATIVE = Path(".claude/agents/nerves-jarvis-reasoner.md")
NATIVE_CONTROL_PLANE_TOOLS = ["SendMessage"]
NATIVE_DISALLOWED_TOOLS = [
    "Bash",
    "Read",
    "Edit",
    "Write",
    "Glob",
    "Grep",
    "Agent",
    "Skill",
    "WebSearch",
    "WebFetch",
    "AskUserQuestion",
    "NotebookEdit",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
    "TaskUpdate",
    "EnterPlanMode",
    "ExitPlanMode",
    "mcp__*",
]

# These strings are intentionally static.  No objective, finding, path,
# service, or evidence text is interpolated into either prompt surface.
ORCHESTRATOR_INSTRUCTION = (
    "JARVIS PRINCIPAL: valida el handoff autenticado indicado por el evento y "
    "reclámalo atómicamente antes de iniciar trabajo. Conserva el hilo principal "
    "libre para William y despierta exactamente un subagente nativo. El subagente "
    "no puede crear sub-subagentes; recibe solo SendMessage como transporte de "
    "control para devolver un JSON una vez. No recibe herramientas de datos, no "
    "ejecuta comandos, no usa red y no abre otras fuentes. Solo razona sobre los dos "
    "insumos autenticados evidence+provenance fijados por path y SHA-256 dentro "
    "del handoff. NO copies el prompt: invoca Agent con el sentinel exacto "
    "`SEAL_NERVES_RENDER_V1\\nmission_id=<mission_id>\\n"
    "claim_id=<claim_id>\\n`; el hook PreToolUse autenticado reemplazará ese "
    "sentinel por los bytes canónicos. El spawn debe usar exactamente "
    "run_in_background=true, description="
    "'Nerves JARVIS one-turn reasoner', name='jarvis_nerves_reasoner' y "
    "subagent_type='nerves-jarvis-reasoner'. Si una validación, claim o "
    "binding falla, abstente. "
    "Después del spawn y antes del receipt, vincula el identificador real "
    "devuelto por la plataforma con el claim mediante bind-platform. El receipt "
    "se crea directamente desde los transcripts owner-only del principal y del "
    "child; no copies ni reserialices manualmente el resultado."
)
LIVE_EVENT_MESSAGE = (
    "NERVES JARVIS: handoff autenticado pendiente. Validar, reclamar "
    "atómicamente y delegar a un único subagente nativo con solo SendMessage."
)


class HandoffError(RuntimeError):
    """Raised when custody, authority, idempotency, or state cannot be proven."""


@dataclass(frozen=True, slots=True)
class BundleAdmission:
    admission: MissionAdmission
    evidence: dict[str, Any]
    provenance: dict[str, Any]
    evidence_path: Path
    provenance_path: Path
    evidence_sha256: str
    provenance_sha256: str


@dataclass(frozen=True, slots=True)
class HandoffResult:
    mission_id: str
    idempotency_key: str
    inbox_path: Path
    receipt_path: Path
    handoff_sha256: str
    created: bool
    status: str
    live_notified: bool


@dataclass(frozen=True, slots=True)
class ClaimResult:
    mission_id: str
    claim_id: str
    worker_id: str
    claimed: bool
    status: str
    receipt_path: Path


@dataclass(frozen=True, slots=True)
class PlatformBindingResult:
    mission_id: str
    claim_id: str
    worker_id: str
    platform_worker_id: str
    profile_sha256: str
    bound: bool
    status: str
    receipt_path: Path


@dataclass(frozen=True, slots=True)
class ReceiptResult:
    mission_id: str
    worker_id: str
    status: str
    receipt_sha256: str
    accepted: bool


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HandoffError(f"value_not_canonical_json:{exc}") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_no_duplicates(raw: bytes, *, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HandoffError(f"{label}_invalid_json:{exc}") from exc
    if not isinstance(value, dict):
        raise HandoffError(f"{label}_must_be_object")
    return value


def _secure_read(
    path: Path,
    *,
    label: str,
    max_bytes: int,
    required_mode: int | None = 0o600,
) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise HandoffError(f"{label}_secure_open_failed:{exc.__class__.__name__}") from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise HandoffError(f"{label}_not_regular")
        if opened.st_uid != os.geteuid():
            raise HandoffError(f"{label}_owner_mismatch")
        if (
            required_mode is not None
            and stat.S_IMODE(opened.st_mode) != required_mode
        ):
            raise HandoffError(f"{label}_mode_must_be_{required_mode:o}")
        if opened.st_size > max_bytes:
            raise HandoffError(f"{label}_too_large")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > max_bytes:
            raise HandoffError(f"{label}_too_large")
        return raw, opened
    finally:
        os.close(fd)


def _validate_json_schema(
    value: Mapping[str, Any],
    schema_path: Path,
    *,
    label: str,
) -> str:
    raw = schema_path.read_bytes()
    schema = _json_no_duplicates(raw, label=f"{label}_schema")
    errors = sorted(
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        detail = ";".join(
            f"{'/'.join(map(str, error.absolute_path))}:{error.message}"
            for error in errors[:8]
        )
        raise HandoffError(f"{label}_schema_invalid:{detail}")
    return _sha256(raw)


def _require_canonical_file(raw: bytes, value: Mapping[str, Any], *, label: str) -> None:
    if raw != _canonical_bytes(dict(value)) + b"\n":
        raise HandoffError(f"{label}_must_be_canonical_newline_terminated")


def validate_bundle(
    manifest_path: Path,
    evidence_path: Path,
    provenance_path: Path,
    *,
    workspace: Path = ROOT,
    mission_schema: Path = DEFAULT_MISSION_SCHEMA,
    evidence_schema: Path = EVIDENCE_SCHEMA,
    skill_dir: Path = SKILL_DIR,
) -> BundleAdmission:
    """Bind a fully admitted mission to immutable evidence and provenance."""

    try:
        admission = admit_mission(
            manifest_path,
            workspace=workspace,
            mission_schema=mission_schema,
            skill_dir=skill_dir,
        )
    except EvidenceBundleError as exc:
        raise HandoffError(f"mission_admission_failed:{exc}") from exc
    mission = admission.mission
    if mission["budgets"]["max_attempts"] != 1:
        raise HandoffError("mission_max_attempts_must_be_one")
    if mission["rollback"]["required"] is not False:
        raise HandoffError("mission_must_be_non_mutating")
    if set(mission["allowed_tools"]) != ALLOWED_TOOLS:
        raise HandoffError("mission_A2_allowlist_mismatch")

    mission_id = str(mission["mission_id"])
    if evidence_path.name != f"{mission_id}.evidence.json":
        raise HandoffError("evidence_filename_mission_id_mismatch")
    if provenance_path.name != f"{mission_id}.provenance.json":
        raise HandoffError("provenance_filename_mission_id_mismatch")

    evidence_raw, _ = _secure_read(
        evidence_path, label="evidence", max_bytes=MAX_OUTPUT_BYTES
    )
    provenance_raw, _ = _secure_read(
        provenance_path, label="provenance", max_bytes=MAX_PROVENANCE_BYTES
    )
    evidence = _json_no_duplicates(evidence_raw, label="evidence")
    provenance = _json_no_duplicates(provenance_raw, label="provenance")
    _require_canonical_file(evidence_raw, evidence, label="evidence")
    _require_canonical_file(provenance_raw, provenance, label="provenance")
    _validate_json_schema(evidence, evidence_schema, label="evidence")

    if evidence.get("mission_id") != mission_id:
        raise HandoffError("evidence_mission_id_mismatch")
    expected_provenance_keys = {
        "schema",
        "mission_id",
        "manifest_sha256",
        "skill_bundle_sha256",
        "source",
        "evidence_sha256",
    }
    if set(provenance) != expected_provenance_keys:
        raise HandoffError("provenance_shape_invalid")
    source = provenance.get("source")
    if not isinstance(source, dict) or set(source) != {
        "path",
        "episode_witness_sha256",
        "record_sha256",
        "timestamp",
        "correlation_id",
        "nerve_fire_id",
    }:
        raise HandoffError("provenance_source_shape_invalid")

    evidence_sha256 = _sha256(evidence_raw)
    workspace_root = workspace.resolve(strict=True)
    expected_source_path = admission.source_path.relative_to(workspace_root).as_posix()
    bindings = {
        "schema": provenance["schema"] == "seal.nerves.integrity-provenance.v1",
        "mission_id": provenance["mission_id"] == mission_id,
        "manifest_sha256": (
            provenance["manifest_sha256"] == admission.manifest_sha256
        ),
        "skill_bundle_sha256": (
            provenance["skill_bundle_sha256"] == admission.skill_bundle_sha256
        ),
        "source_path": source["path"] == expected_source_path,
        "source_witness": (
            source["episode_witness_sha256"]
            == admission.source_witness_sha256
        ),
        "source_record": (
            source["record_sha256"] == admission.source_record_sha256
        ),
        "source_timestamp": (
            source["timestamp"] == admission.source_record.get("ts")
        ),
        "correlation_id": (
            source["correlation_id"] == mission.get("correlation_id")
        ),
        "nerve_fire_id": source["nerve_fire_id"] == mission["nerve_fire_id"],
        "evidence_sha256": provenance["evidence_sha256"] == evidence_sha256,
    }
    failed = [name for name, valid in bindings.items() if not valid]
    if failed:
        raise HandoffError("bundle_binding_mismatch:" + ",".join(failed))
    return BundleAdmission(
        admission=admission,
        evidence=evidence,
        provenance=provenance,
        evidence_path=evidence_path.resolve(strict=True),
        provenance_path=provenance_path.resolve(strict=True),
        evidence_sha256=evidence_sha256,
        provenance_sha256=_sha256(provenance_raw),
    )


def _ensure_private_directory(path: Path) -> None:
    try:
        current = path.lstat()
    except FileNotFoundError:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        current = path.lstat()
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
        raise HandoffError("inbox_must_be_directory_not_symlink")
    if current.st_uid != os.geteuid():
        raise HandoffError("inbox_owner_mismatch")
    os.chmod(path, 0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise HandoffError("inbox_mode_must_be_0700")


def _handoff_document(
    bundle: BundleAdmission,
    *,
    manifest_path: Path,
    inbox_path: Path,
    created_at: str,
) -> dict[str, Any]:
    mission = bundle.admission.mission
    receipt_path = inbox_path.with_suffix(".receipt.json")
    handoff_id = str(
        uuid.uuid5(
            uuid.UUID("a5b0f974-fc0a-4b5f-bf27-bac7960a00d1"),
            f"{mission['mission_id']}:{mission['idempotency_key']}",
        )
    )
    return {
        "schema": HANDOFF_SCHEMA,
        "handoff_id": handoff_id,
        "mission_id": mission["mission_id"],
        "idempotency_key": mission["idempotency_key"],
        "created_at": created_at,
        "authority": {
            "agent": "JARVIS",
            "specialty": "architecture_integrity_audit",
            "risk_class": "A2_READ_ONLY",
            "network": "none",
            "worker_tools": NATIVE_CONTROL_PLANE_TOOLS,
            "max_attempts": 1,
            "native_subagents": 1,
            "sub_subagents": 0,
        },
        "bindings": {
            "manifest_path": str(manifest_path.resolve(strict=True)),
            "manifest_sha256": bundle.admission.manifest_sha256,
            "mission_sha256": _sha256(
                _canonical_bytes(bundle.admission.mission)
            ),
            "mission_source_path": str(bundle.admission.source_path),
            "mission_source_witness_sha256": (
                bundle.admission.source_witness_sha256
            ),
            "mission_source_record_sha256": (
                bundle.admission.source_record_sha256
            ),
            "skill_bundle_sha256": bundle.admission.skill_bundle_sha256,
            "evidence_path": str(bundle.evidence_path),
            "evidence_sha256": bundle.evidence_sha256,
            "provenance_path": str(bundle.provenance_path),
            "provenance_sha256": bundle.provenance_sha256,
        },
        "instruction": ORCHESTRATOR_INSTRUCTION,
        "receipt_contract": {
            "schema": RECEIPT_SCHEMA,
            "schema_path": str(DEFAULT_RECEIPT_SCHEMA),
            "path": str(receipt_path),
            "mode": "0600",
            "worker_kind": "native_subagent",
            "tools_used": NATIVE_CONTROL_PLANE_TOOLS,
        },
    }


def _secure_create(path: Path, raw: bytes, *, mode: int = 0o600) -> bool:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, mode)
    except FileExistsError:
        current_raw, _ = _secure_read(
            path, label="existing_file", max_bytes=max(len(raw), MAX_HANDOFF_BYTES)
        )
        if current_raw != raw:
            raise HandoffError("existing_file_content_mismatch")
        return False
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                raise OSError("short secure write")
            offset += written
        os.fsync(fd)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    return True


def _state_body(deliveries: Mapping[str, Any]) -> dict[str, Any]:
    body = {"schema": STATE_SCHEMA, "deliveries": dict(deliveries)}
    return {**body, "state_sha256": _sha256(_canonical_bytes(body))}


def _load_state(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise HandoffError("state_must_be_regular_not_symlink")
    if not path.exists():
        return _state_body({})
    raw, _ = _secure_read(path, label="state", max_bytes=MAX_HANDOFF_BYTES)
    value = _json_no_duplicates(raw, label="state")
    if set(value) != {"schema", "deliveries", "state_sha256"}:
        raise HandoffError("state_shape_invalid")
    if value["schema"] != STATE_SCHEMA or not isinstance(value["deliveries"], dict):
        raise HandoffError("state_schema_invalid")
    body = {"schema": value["schema"], "deliveries": value["deliveries"]}
    if value["state_sha256"] != _sha256(_canonical_bytes(body)):
        raise HandoffError("state_hash_invalid")
    return value


def _write_state_atomic(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        current = path.lstat()
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
            raise HandoffError("state_must_be_regular_not_symlink")
    raw = _canonical_bytes(dict(state)) + b"\n"
    temp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    _secure_create(temp, raw)
    try:
        os.replace(temp, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(
            path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _open_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise HandoffError(f"state_lock_open_failed:{exc.__class__.__name__}") from exc
    opened = os.fstat(fd)
    if not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid():
        os.close(fd)
        raise HandoffError("state_lock_not_private_regular")
    os.fchmod(fd, 0o600)
    return fd


def _event_id(handoff_id: str) -> str:
    return f"nerves_handoff_{handoff_id}"


def _live_event(
    handoff: Mapping[str, Any], handoff_path: Path, handoff_sha256: str
) -> dict[str, Any]:
    return {
        "id": _event_id(str(handoff["handoff_id"])),
        "from": "NERVES",
        "to": "JARVIS",
        "timestamp": handoff["created_at"],
        "type": "nerves_mission",
        "channel": "internal:nerves:jarvis",
        "message": LIVE_EVENT_MESSAGE,
        "handoff_id": handoff["handoff_id"],
        "handoff_path": str(handoff_path),
        "handoff_sha256": handoff_sha256,
        "mission_id": handoff["mission_id"],
    }


def _feed_contains_event(path: Path, event_id: str) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise HandoffError(f"live_feed_scan_failed:{exc.__class__.__name__}") from exc
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_size > MAX_LIVE_FEED_SCAN_BYTES
        ):
            raise HandoffError("live_feed_not_scannable_owner_regular")
        raw = b""
        while True:
            chunk = os.read(fd, 65_536)
            if not chunk:
                break
            raw += chunk
    finally:
        os.close(fd)
    for line in raw.splitlines():
        if not line:
            continue
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("id") == event_id:
            return True
    return False


def _append_live_feed(path: Path, event: Mapping[str, Any]) -> None:
    raw = _canonical_bytes(dict(event)) + b"\n"
    flags = (
        os.O_WRONLY
        | os.O_APPEND
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
    )
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid():
            raise HandoffError("live_feed_not_owner_regular")
        written = os.write(fd, raw)
        if written != len(raw):
            raise OSError(f"short live feed append {written}/{len(raw)}")
        os.fsync(fd)
    finally:
        os.close(fd)


def _notify_pending(
    record: dict[str, Any],
    handoff: Mapping[str, Any],
    *,
    inbox_path: Path,
    live_feed: Path | None,
    now: datetime,
) -> tuple[dict[str, Any], bool]:
    if live_feed is None:
        return record, False
    record = dict(record)
    record["live_notification_attempts"] = (
        int(record.get("live_notification_attempts", 0)) + 1
    )
    event_id = _event_id(str(handoff["handoff_id"]))
    try:
        if not _feed_contains_event(live_feed, event_id):
            event = _live_event(handoff, inbox_path, record["handoff_sha256"])
            _append_live_feed(live_feed, event)
        record["status"] = "live_notified"
        record["live_notified_at"] = now.isoformat()
        record["live_error"] = None
        return record, True
    except (OSError, HandoffError) as exc:
        record["status"] = "pending"
        record["live_error"] = f"{exc.__class__.__name__}:{str(exc)[:160]}"
        return record, False


def _validate_existing_handoff(
    inbox_path: Path, expected_sha256: str
) -> tuple[dict[str, Any], bytes]:
    raw, _ = _secure_read(
        inbox_path, label="handoff", max_bytes=MAX_HANDOFF_BYTES
    )
    if _sha256(raw) != expected_sha256:
        raise HandoffError("durable_handoff_hash_mismatch")
    handoff = _json_no_duplicates(raw, label="handoff")
    _require_canonical_file(raw, handoff, label="handoff")
    if handoff.get("schema") != HANDOFF_SCHEMA:
        raise HandoffError("handoff_schema_invalid")
    return handoff, raw


def deliver_jarvis_handoff(
    manifest_path: Path,
    evidence_path: Path,
    provenance_path: Path,
    *,
    inbox_dir: Path = DEFAULT_INBOX,
    state_path: Path = DEFAULT_STATE,
    live_feed: Path | None = DEFAULT_LIVE_FEED,
    workspace: Path = ROOT,
    mission_schema: Path = DEFAULT_MISSION_SCHEMA,
    evidence_schema: Path = EVIDENCE_SCHEMA,
    skill_dir: Path = SKILL_DIR,
    now: datetime | None = None,
) -> HandoffResult:
    """Deliver/retry one authenticated tool-free reasoning handoff."""

    bundle = validate_bundle(
        manifest_path,
        evidence_path,
        provenance_path,
        workspace=workspace,
        mission_schema=mission_schema,
        evidence_schema=evidence_schema,
        skill_dir=skill_dir,
    )
    mission = bundle.admission.mission
    mission_id = str(mission["mission_id"])
    idempotency_key = str(mission["idempotency_key"])
    _ensure_private_directory(inbox_dir)
    inbox_path = inbox_dir / f"{mission_id}.handoff.json"
    receipt_path = inbox_path.with_suffix(".receipt.json")
    current_time = now or datetime.now(timezone.utc)
    created_at = current_time.isoformat()

    lock_fd = _open_lock(state_path.with_suffix(state_path.suffix + ".lock"))
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        state = _load_state(state_path)
        deliveries = dict(state["deliveries"])
        existing = deliveries.get(mission_id)
        for other_id, record in deliveries.items():
            if (
                other_id != mission_id
                and isinstance(record, dict)
                and record.get("idempotency_key") == idempotency_key
            ):
                raise HandoffError("idempotency_key_bound_to_different_mission")

        if existing is not None:
            if not isinstance(existing, dict):
                raise HandoffError("state_delivery_record_invalid")
            exact_bindings = {
                "idempotency_key": idempotency_key,
                "manifest_sha256": bundle.admission.manifest_sha256,
                "mission_sha256": _sha256(_canonical_bytes(mission)),
                "evidence_sha256": bundle.evidence_sha256,
                "provenance_sha256": bundle.provenance_sha256,
            }
            for key, expected in exact_bindings.items():
                if existing.get(key) != expected:
                    raise HandoffError(f"replay_{key}_mismatch")
            handoff, _ = _validate_existing_handoff(
                inbox_path, str(existing.get("handoff_sha256"))
            )
            status = str(existing.get("status"))
            live_notified = status != "pending"
            if status == "pending":
                existing, live_notified = _notify_pending(
                    dict(existing),
                    handoff,
                    inbox_path=inbox_path,
                    live_feed=live_feed,
                    now=current_time,
                )
                deliveries[mission_id] = existing
                _write_state_atomic(state_path, _state_body(deliveries))
                status = str(existing["status"])
            return HandoffResult(
                mission_id,
                idempotency_key,
                inbox_path,
                receipt_path,
                str(existing["handoff_sha256"]),
                False,
                status,
                live_notified,
            )

        handoff = _handoff_document(
            bundle,
            manifest_path=manifest_path,
            inbox_path=inbox_path,
            created_at=created_at,
        )
        handoff_raw = _canonical_bytes(handoff) + b"\n"
        if len(handoff_raw) > MAX_HANDOFF_BYTES:
            raise HandoffError("handoff_too_large")
        if inbox_path.exists() or inbox_path.is_symlink():
            existing_raw, _ = _secure_read(
                inbox_path, label="orphan_handoff", max_bytes=MAX_HANDOFF_BYTES
            )
            existing_doc = _json_no_duplicates(
                existing_raw, label="orphan_handoff"
            )
            orphan_created_at = existing_doc.get("created_at")
            if not isinstance(orphan_created_at, str):
                raise HandoffError("orphan_handoff_created_at_invalid")
            handoff = _handoff_document(
                bundle,
                manifest_path=manifest_path,
                inbox_path=inbox_path,
                created_at=orphan_created_at,
            )
            handoff_raw = _canonical_bytes(handoff) + b"\n"
            if existing_raw != handoff_raw:
                raise HandoffError("orphan_handoff_content_mismatch")
            created_file = False
            created_at = orphan_created_at
        else:
            created_file = _secure_create(inbox_path, handoff_raw)
        handoff_sha256 = _sha256(handoff_raw)
        record = {
            "idempotency_key": idempotency_key,
            "manifest_sha256": bundle.admission.manifest_sha256,
            "mission_sha256": _sha256(_canonical_bytes(mission)),
            "source_witness_sha256": (
                bundle.admission.source_witness_sha256
            ),
            "source_record_sha256": bundle.admission.source_record_sha256,
            "skill_bundle_sha256": bundle.admission.skill_bundle_sha256,
            "evidence_sha256": bundle.evidence_sha256,
            "provenance_sha256": bundle.provenance_sha256,
            "handoff_sha256": handoff_sha256,
            "handoff_id": handoff["handoff_id"],
            "inbox_path": str(inbox_path),
            "receipt_path": str(receipt_path),
            "created_at": created_at,
            "status": "pending",
            "live_notification_attempts": 0,
            "live_error": None,
            "claim": None,
            "receipt": None,
            "provenance": "admitted_mission_plus_authenticated_evidence_v2",
        }
        deliveries[mission_id] = record
        _write_state_atomic(state_path, _state_body(deliveries))

        # If an orphan inbox was recovered, its notification may already have
        # been appended before the state crash.  The event scan is the arbiter.
        record, live_notified = _notify_pending(
            record,
            handoff,
            inbox_path=inbox_path,
            live_feed=live_feed,
            now=current_time,
        )
        deliveries[mission_id] = record
        _write_state_atomic(state_path, _state_body(deliveries))
        return HandoffResult(
            mission_id,
            idempotency_key,
            inbox_path,
            receipt_path,
            handoff_sha256,
            created_file,
            str(record["status"]),
            live_notified,
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def claim_handoff(
    mission_id: str,
    idempotency_key: str,
    handoff_sha256: str,
    worker_id: str,
    *,
    inbox_dir: Path = DEFAULT_INBOX,
    state_path: Path = DEFAULT_STATE,
    now: datetime | None = None,
) -> ClaimResult:
    """Atomically bind exactly one platform-native worker before model spawn."""

    try:
        mission_id = str(uuid.UUID(mission_id))
    except ValueError as exc:
        raise HandoffError("claim_mission_id_invalid") from exc
    if not WORKER_ID_PATTERN.fullmatch(worker_id):
        raise HandoffError("claim_worker_id_invalid")
    if not SHA256_PATTERN.fullmatch(handoff_sha256):
        raise HandoffError("claim_handoff_sha256_invalid")
    inbox_path = inbox_dir / f"{mission_id}.handoff.json"
    lock_fd = _open_lock(state_path.with_suffix(state_path.suffix + ".lock"))
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        state = _load_state(state_path)
        deliveries = dict(state["deliveries"])
        record = deliveries.get(mission_id)
        if not isinstance(record, dict):
            raise HandoffError("claim_unknown_mission")
        if record.get("idempotency_key") != idempotency_key:
            raise HandoffError("claim_idempotency_mismatch")
        if record.get("handoff_sha256") != handoff_sha256:
            raise HandoffError("claim_handoff_hash_mismatch")
        _validate_existing_handoff(inbox_path, handoff_sha256)
        existing_claim = record.get("claim")
        if existing_claim is not None:
            if (
                isinstance(existing_claim, dict)
                and existing_claim.get("worker_id") == worker_id
                and existing_claim.get("handoff_sha256") == handoff_sha256
            ):
                return ClaimResult(
                    mission_id,
                    str(existing_claim["claim_id"]),
                    worker_id,
                    False,
                    str(record["status"]),
                    Path(str(record["receipt_path"])),
                )
            raise HandoffError("handoff_already_claimed")
        if record.get("status") not in {"pending", "live_notified"}:
            raise HandoffError("handoff_not_claimable")
        receipt_path = Path(str(record["receipt_path"]))
        if receipt_path.exists() or receipt_path.is_symlink():
            raise HandoffError("claim_receipt_path_must_be_absent")
        claim_id = str(
            uuid.uuid5(
                uuid.UUID("2173bcab-ae37-4293-89ed-2276e5b237cb"),
                f"{mission_id}:{idempotency_key}:{handoff_sha256}:{worker_id}",
            )
        )
        record = dict(record)
        record["claim"] = {
            "claim_id": claim_id,
            "mission_id": mission_id,
            "idempotency_key": idempotency_key,
            "handoff_sha256": handoff_sha256,
            "worker_id": worker_id,
            "claimed_at": (now or datetime.now(timezone.utc)).isoformat(),
            "worker_kind": "native_subagent",
            "tools": NATIVE_CONTROL_PLANE_TOOLS,
        }
        record["status"] = "claimed"
        deliveries[mission_id] = record
        _write_state_atomic(state_path, _state_body(deliveries))
        return ClaimResult(
            mission_id,
            claim_id,
            worker_id,
            True,
            "claimed",
            Path(str(record["receipt_path"])),
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _native_profile_sha256(
    workspace: Path,
    profile_sha256: str,
) -> str:
    if not SHA256_PATTERN.fullmatch(profile_sha256):
        raise HandoffError("platform_profile_sha256_invalid")
    try:
        workspace_root = workspace.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HandoffError("platform_workspace_invalid") from exc
    profile_path = workspace_root / NATIVE_PROFILE_RELATIVE
    try:
        profile_path.relative_to(workspace_root)
    except ValueError as exc:
        raise HandoffError("platform_profile_outside_workspace") from exc
    try:
        profile_raw, profile_stat = _secure_read(
            profile_path,
            label="native_profile",
            max_bytes=MAX_OUTPUT_BYTES,
            required_mode=None,
        )
    except HandoffError as exc:
        raise HandoffError(f"platform_profile_untrusted:{exc}") from exc
    if stat.S_IMODE(profile_stat.st_mode) & (stat.S_IWGRP | stat.S_IWOTH):
        raise HandoffError("platform_profile_group_or_world_writable")
    try:
        text = profile_raw.decode("utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise ValueError("frontmatter_delimiters")
        frontmatter_text = text[4:].split("\n---\n", 1)[0]
        frontmatter = yaml.safe_load(frontmatter_text)
    except (UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        raise HandoffError("platform_profile_frontmatter_invalid") from exc
    expected_keys = {
        "name",
        "description",
        "tools",
        "disallowedTools",
        "skills",
        "mcpServers",
        "permissionMode",
        "maxTurns",
        "background",
    }
    if not isinstance(frontmatter, dict) or set(frontmatter) != expected_keys:
        raise HandoffError("platform_profile_frontmatter_shape_invalid")
    exact_capabilities = {
        "name": frontmatter.get("name") == NATIVE_PROFILE,
        "description": isinstance(frontmatter.get("description"), str)
        and bool(frontmatter["description"].strip()),
        "tools": frontmatter.get("tools") == "SendMessage",
        "disallowedTools": frontmatter.get("disallowedTools")
        == NATIVE_DISALLOWED_TOOLS,
        "skills": frontmatter.get("skills") == [],
        "mcpServers": frontmatter.get("mcpServers") == [],
        "permissionMode": frontmatter.get("permissionMode") == "dontAsk",
        "maxTurns": frontmatter.get("maxTurns") == 1,
        "background": frontmatter.get("background") is True,
    }
    failed = [name for name, valid in exact_capabilities.items() if not valid]
    if failed:
        raise HandoffError(
            "platform_profile_capability_mismatch:" + ",".join(failed)
        )
    actual = _sha256(profile_raw)
    if actual != profile_sha256:
        raise HandoffError("platform_profile_sha256_mismatch")
    return actual


def bind_platform_worker(
    mission_id: str,
    claim_id: str,
    prebound_worker_id: str,
    platform_worker_id: str,
    profile_sha256: str,
    *,
    workspace: Path = ROOT,
    inbox_dir: Path = DEFAULT_INBOX,
    state_path: Path = DEFAULT_STATE,
    now: datetime | None = None,
) -> PlatformBindingResult:
    """Bind the platform-issued native worker identity to an existing claim."""

    try:
        mission_id = str(uuid.UUID(mission_id))
    except ValueError as exc:
        raise HandoffError("platform_mission_id_invalid") from exc
    try:
        claim_id = str(uuid.UUID(claim_id))
    except ValueError as exc:
        raise HandoffError("platform_claim_id_invalid") from exc
    if not WORKER_ID_PATTERN.fullmatch(prebound_worker_id):
        raise HandoffError("platform_prebound_worker_id_invalid")
    if not WORKER_ID_PATTERN.fullmatch(platform_worker_id):
        raise HandoffError("platform_worker_id_invalid")
    actual_profile_sha256 = _native_profile_sha256(workspace, profile_sha256)

    inbox_path = inbox_dir / f"{mission_id}.handoff.json"
    lock_fd = _open_lock(state_path.with_suffix(state_path.suffix + ".lock"))
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        state = _load_state(state_path)
        deliveries = dict(state["deliveries"])
        record = deliveries.get(mission_id)
        if not isinstance(record, dict):
            raise HandoffError("platform_unknown_mission")
        claim = record.get("claim")
        if not isinstance(claim, dict):
            raise HandoffError("platform_binding_requires_atomic_claim")
        claim_bindings = {
            "claim_id": claim.get("claim_id") == claim_id,
            "worker_id": claim.get("worker_id") == prebound_worker_id,
            "worker_kind": claim.get("worker_kind") == "native_subagent",
            "tools": claim.get("tools") == NATIVE_CONTROL_PLANE_TOOLS,
        }
        failed = [name for name, valid in claim_bindings.items() if not valid]
        if failed:
            raise HandoffError(
                "platform_claim_binding_mismatch:" + ",".join(failed)
            )
        _validate_existing_handoff(
            inbox_path, str(record.get("handoff_sha256"))
        )

        existing_binding = record.get("platform_binding")
        expected_binding = {
            "platform": NATIVE_PLATFORM,
            "profile": NATIVE_PROFILE,
            "profile_sha256": actual_profile_sha256,
            "tools_configured": NATIVE_CONTROL_PLANE_TOOLS,
            "skills_configured": [],
            "mcp_servers_configured": [],
            "max_turns": 1,
            "claim_id": claim_id,
            "worker_id": prebound_worker_id,
            "platform_worker_id": platform_worker_id,
        }
        if existing_binding is not None:
            if (
                isinstance(existing_binding, dict)
                and all(
                    existing_binding.get(key) == expected
                    for key, expected in expected_binding.items()
                )
                and record.get("status") == "running"
            ):
                return PlatformBindingResult(
                    mission_id,
                    claim_id,
                    prebound_worker_id,
                    platform_worker_id,
                    actual_profile_sha256,
                    False,
                    "running",
                    Path(str(record["receipt_path"])),
                )
            raise HandoffError("platform_worker_already_bound")
        if record.get("status") != "claimed":
            raise HandoffError("platform_handoff_not_bindable")

        record = dict(record)
        record["platform_binding"] = {
            **expected_binding,
            "bound_at": (now or datetime.now(timezone.utc)).isoformat(),
        }
        record["status"] = "running"
        deliveries[mission_id] = record
        _write_state_atomic(state_path, _state_body(deliveries))
        return PlatformBindingResult(
            mission_id,
            claim_id,
            prebound_worker_id,
            platform_worker_id,
            actual_profile_sha256,
            True,
            "running",
            Path(str(record["receipt_path"])),
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def submit_receipt(
    receipt_path: Path,
    *,
    parent_transcript_path: Path | None = None,
    child_transcript_path: Path | None = None,
    inbox_dir: Path = DEFAULT_INBOX,
    state_path: Path = DEFAULT_STATE,
    receipt_schema: Path = DEFAULT_RECEIPT_SCHEMA,
    now: datetime | None = None,
) -> ReceiptResult:
    """Validate a platform-bound native receipt and commit terminal state."""

    raw, _ = _secure_read(
        receipt_path, label="receipt", max_bytes=MAX_OUTPUT_BYTES
    )
    receipt = _json_no_duplicates(raw, label="receipt")
    _require_canonical_file(raw, receipt, label="receipt")
    _validate_json_schema(receipt, receipt_schema, label="receipt")
    mission_id = str(receipt["mission_id"])
    expected_path = inbox_dir / f"{mission_id}.handoff.receipt.json"
    if receipt_path.resolve(strict=True) != expected_path.resolve(strict=True):
        raise HandoffError("receipt_path_not_bound_to_mission")
    status = str(receipt["status"])
    verdict_map = {
        "completed": "accepted",
        "failed": "rejected",
        "abstained": "abstained",
    }
    if receipt["verifier_verdict"]["verdict"] != verdict_map[status]:
        raise HandoffError("receipt_status_verdict_mismatch")
    receipt_sha256 = _sha256(raw)

    lock_fd = _open_lock(state_path.with_suffix(state_path.suffix + ".lock"))
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        state = _load_state(state_path)
        deliveries = dict(state["deliveries"])
        record = deliveries.get(mission_id)
        if not isinstance(record, dict):
            raise HandoffError("receipt_unknown_mission")
        existing_receipt = record.get("receipt")
        terminal_replay = record.get("status") in TERMINAL_STATES
        if terminal_replay:
            if (
                isinstance(existing_receipt, dict)
                and existing_receipt.get("receipt_sha256") == receipt_sha256
                and record.get("status") == status
            ):
                # Replays remain subject to transcript re-attestation below.
                # A matching receipt file alone is not sufficient if the
                # caller now supplies different or mutated transcript bytes.
                pass
            else:
                raise HandoffError("terminal_receipt_conflict")
        elif record.get("status") != "running" or not isinstance(
            record.get("claim"), dict
        ):
            raise HandoffError("receipt_requires_platform_binding")
        claim = record["claim"]
        platform_binding = record.get("platform_binding")
        if not isinstance(platform_binding, dict):
            raise HandoffError("receipt_requires_platform_binding")
        runtime_attestation = receipt["runtime_attestation"]
        bindings = {
            "idempotency_key": receipt["idempotency_key"]
            == record["idempotency_key"],
            "handoff_sha256": receipt["handoff_sha256"]
            == record["handoff_sha256"],
            "claim_id": receipt["claim_id"] == claim["claim_id"],
            "worker_id": receipt["worker_id"] == claim["worker_id"],
            "platform_worker_id": receipt["platform_worker_id"]
            == platform_binding["platform_worker_id"],
            "worker_kind": receipt["worker_kind"] == "native_subagent",
            "tools_used_shape": receipt["tools_used"]
            in ([], NATIVE_CONTROL_PLANE_TOOLS),
            "runtime_platform": runtime_attestation["platform"]
            == platform_binding["platform"],
            "runtime_profile": runtime_attestation["profile"]
            == platform_binding["profile"],
            "runtime_profile_sha256": runtime_attestation["profile_sha256"]
            == platform_binding["profile_sha256"],
            "runtime_tools": runtime_attestation["tools_configured"]
            == platform_binding["tools_configured"],
            "runtime_skills": runtime_attestation["skills_configured"]
            == platform_binding["skills_configured"],
            "runtime_mcp_servers": runtime_attestation[
                "mcp_servers_configured"
            ]
            == platform_binding["mcp_servers_configured"],
            "runtime_max_turns": runtime_attestation["max_turns"]
            == platform_binding["max_turns"],
            "evidence_sha256": receipt["evidence_sha256"]
            == record["evidence_sha256"],
            "provenance_sha256": receipt["provenance_sha256"]
            == record["provenance_sha256"],
        }
        failed = [name for name, valid in bindings.items() if not valid]
        if failed:
            raise HandoffError("receipt_binding_mismatch:" + ",".join(failed))
        handoff_path = Path(str(record["inbox_path"]))
        handoff, _ = _validate_existing_handoff(
            handoff_path, str(record["handoff_sha256"])
        )
        evidence_raw, _ = _secure_read(
            Path(str(handoff["bindings"]["evidence_path"])),
            label="receipt_commit_evidence",
            max_bytes=MAX_OUTPUT_BYTES,
            required_mode=0o600,
        )
        provenance_raw, _ = _secure_read(
            Path(str(handoff["bindings"]["provenance_path"])),
            label="receipt_commit_provenance",
            max_bytes=MAX_OUTPUT_BYTES,
            required_mode=0o600,
        )
        if _sha256(evidence_raw) != record["evidence_sha256"]:
            raise HandoffError("receipt_commit_evidence_hash_mismatch")
        if _sha256(provenance_raw) != record["provenance_sha256"]:
            raise HandoffError("receipt_commit_provenance_hash_mismatch")
        # Lazy import avoids a module-load cycle while making transcript
        # verification mandatory in the only terminal transition.
        from memory.nerves_native_agent_receipt import (
            NativeTranscriptAttestationFailure,
            PROMPT_RENDER_AUDIT_SUFFIX,
            _receipt_findings,
            attest_native_transcript_outcome,
            build_native_reasoner_prompt,
            derive_native_attestation_failure_semantics,
            derive_native_receipt_semantics,
            transcript_snapshot_paths,
        )

        expected_prompt = build_native_reasoner_prompt(
            mission_id, evidence_raw, provenance_raw
        )
        expected_parent_snapshot, expected_child_snapshot = (
            transcript_snapshot_paths(mission_id, inbox_dir=inbox_dir)
        )
        custody_bindings = {
            "parent_transcript_snapshot_path": (
                runtime_attestation["parent_transcript_snapshot_path"]
                == str(expected_parent_snapshot)
            ),
            "child_transcript_snapshot_path": (
                runtime_attestation["child_transcript_snapshot_path"]
                == str(expected_child_snapshot)
            ),
        }
        failed_custody = [
            name for name, valid in custody_bindings.items() if not valid
        ]
        if failed_custody:
            raise HandoffError(
                "native_transcript_snapshot_binding_mismatch:"
                + ",".join(failed_custody)
            )
        for supplied, expected in (
            (parent_transcript_path, expected_parent_snapshot),
            (child_transcript_path, expected_child_snapshot),
        ):
            if supplied is not None and supplied != expected:
                raise HandoffError("native_transcript_snapshot_path_mismatch")
        if (
            not expected_parent_snapshot.exists()
            and not expected_parent_snapshot.is_symlink()
        ) or (
            not expected_child_snapshot.exists()
            and not expected_child_snapshot.is_symlink()
        ):
            raise HandoffError("native_transcript_attestation_required")
        try:
            attested = attest_native_transcript_outcome(
                mission_id,
                str(platform_binding["platform_worker_id"]),
                expected_prompt,
                claim_id=str(claim["claim_id"]),
                profile_sha256=str(platform_binding["profile_sha256"]),
                renderer_bindings={
                    "evidence_sha256": str(record["evidence_sha256"]),
                    "provenance_sha256": str(record["provenance_sha256"]),
                    "handoff_sha256": str(record["handoff_sha256"]),
                },
                prompt_render_audit_path=(
                    inbox_dir
                    / f"{mission_id}{PROMPT_RENDER_AUDIT_SUFFIX}"
                ),
                parent_transcript_path=expected_parent_snapshot,
                child_transcript_path=expected_child_snapshot,
            )
        except RuntimeError as exc:
            raise HandoffError(
                f"native_transcript_attestation_failed:{exc}"
            ) from exc
        attestation_bindings = {
            "parent_transcript_prefix_sha256": (
                runtime_attestation["parent_transcript_prefix_sha256"]
                == attested.parent_transcript_prefix_sha256
            ),
            "parent_transcript_prefix_bytes": (
                runtime_attestation["parent_transcript_prefix_bytes"]
                == attested.parent_transcript_prefix_bytes
            ),
            "child_transcript_prefix_sha256": (
                runtime_attestation["child_transcript_prefix_sha256"]
                == attested.child_transcript_prefix_sha256
            ),
            "child_transcript_prefix_bytes": (
                runtime_attestation["child_transcript_prefix_bytes"]
                == attested.child_transcript_prefix_bytes
            ),
            "child_agent_id": (
                runtime_attestation["child_agent_id"]
                == attested.child_agent_id
            ),
            "tool_events": (
                runtime_attestation["tool_events"] == attested.tool_events
            ),
            "hook_commands_observed": (
                runtime_attestation["hook_commands_observed"]
                == attested.hook_commands_observed
            ),
        }
        failed_attestation = [
            name
            for name, valid in attestation_bindings.items()
            if not valid
        ]
        if failed_attestation:
            raise HandoffError(
                "native_transcript_attestation_mismatch:"
                + ",".join(failed_attestation)
            )
        if isinstance(attested, NativeTranscriptAttestationFailure):
            derived_output, derived_status, derived_verifier = (
                derive_native_attestation_failure_semantics(attested)
            )
            derived_tools_used: list[str] = []
        else:
            derived_result, derived_status, derived_verifier = (
                derive_native_receipt_semantics(
                    evidence_raw, attested.model_result
                )
            )
            derived_output = {
                "summary": derived_result["summary"][:8000],
                "findings": _receipt_findings(derived_result),
            }
            derived_tools_used = NATIVE_CONTROL_PLANE_TOOLS
        semantic_bindings = {
            "status": receipt["status"] == derived_status,
            "tools_used": receipt["tools_used"] == derived_tools_used,
            "output": receipt["output"] == derived_output,
            "verifier_verdict": (
                receipt["verifier_verdict"] == derived_verifier
            ),
            "started_at": receipt["started_at"]
            == platform_binding["bound_at"],
        }
        failed_semantics = [
            name for name, valid in semantic_bindings.items() if not valid
        ]
        if failed_semantics:
            raise HandoffError(
                "native_receipt_semantics_mismatch:"
                + ",".join(failed_semantics)
            )
        transcript_custody = {
            "parent_snapshot_path": str(expected_parent_snapshot),
            "parent_prefix_sha256": runtime_attestation[
                "parent_transcript_prefix_sha256"
            ],
            "parent_prefix_bytes": runtime_attestation[
                "parent_transcript_prefix_bytes"
            ],
            "child_snapshot_path": str(expected_child_snapshot),
            "child_prefix_sha256": runtime_attestation[
                "child_transcript_prefix_sha256"
            ],
            "child_prefix_bytes": runtime_attestation[
                "child_transcript_prefix_bytes"
            ],
        }
        if terminal_replay:
            if existing_receipt.get("transcript_custody") != transcript_custody:
                raise HandoffError("terminal_transcript_custody_mismatch")
            return ReceiptResult(
                mission_id,
                str(receipt["worker_id"]),
                status,
                receipt_sha256,
                False,
            )
        record = dict(record)
        record["status"] = status
        record["receipt"] = {
            "receipt_sha256": receipt_sha256,
            "receipt_path": str(receipt_path),
            "worker_id": receipt["worker_id"],
            "platform_worker_id": receipt["platform_worker_id"],
            "profile_sha256": runtime_attestation["profile_sha256"],
            "claim_id": receipt["claim_id"],
            "status": status,
            "submitted_at": (now or datetime.now(timezone.utc)).isoformat(),
            "transcript_custody": transcript_custody,
        }
        deliveries[mission_id] = record
        _write_state_atomic(state_path, _state_body(deliveries))
        return ReceiptResult(
            mission_id,
            str(receipt["worker_id"]),
            status,
            receipt_sha256,
            True,
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _print_result(value: Any) -> None:
    print(
        json.dumps(
            {
                field: str(getattr(value, field))
                if isinstance(getattr(value, field), Path)
                else getattr(value, field)
                for field in value.__dataclass_fields__
            },
            sort_keys=True,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Deliver, claim, bind a native platform worker, or submit a "
            "JARVIS NERVES handoff."
        )
    )
    parser.add_argument("--inbox-dir", type=Path, default=DEFAULT_INBOX)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    subparsers = parser.add_subparsers(dest="command", required=True)

    deliver = subparsers.add_parser("deliver")
    deliver.add_argument("manifest", type=Path)
    deliver.add_argument("evidence", type=Path)
    deliver.add_argument("provenance", type=Path)
    deliver.add_argument("--live-feed", type=Path, default=DEFAULT_LIVE_FEED)
    deliver.add_argument("--no-live-feed", action="store_true")

    claim = subparsers.add_parser("claim")
    claim.add_argument("mission_id")
    claim.add_argument("idempotency_key")
    claim.add_argument("handoff_sha256")
    claim.add_argument("worker_id")

    bind = subparsers.add_parser("bind-platform")
    bind.add_argument("mission_id")
    bind.add_argument("claim_id")
    bind.add_argument("prebound_worker_id")
    bind.add_argument("platform_worker_id")
    bind.add_argument("profile_sha256")
    bind.add_argument("--workspace", type=Path, default=ROOT)

    receipt = subparsers.add_parser("submit-receipt")
    receipt.add_argument("receipt", type=Path)
    receipt.add_argument("parent_transcript", type=Path, nargs="?")
    receipt.add_argument("child_transcript", type=Path, nargs="?")
    args = parser.parse_args()
    if args.command == "deliver":
        result = deliver_jarvis_handoff(
            args.manifest,
            args.evidence,
            args.provenance,
            inbox_dir=args.inbox_dir,
            state_path=args.state,
            live_feed=None if args.no_live_feed else args.live_feed,
        )
    elif args.command == "claim":
        result = claim_handoff(
            args.mission_id,
            args.idempotency_key,
            args.handoff_sha256,
            args.worker_id,
            inbox_dir=args.inbox_dir,
            state_path=args.state,
        )
    elif args.command == "bind-platform":
        result = bind_platform_worker(
            args.mission_id,
            args.claim_id,
            args.prebound_worker_id,
            args.platform_worker_id,
            args.profile_sha256,
            workspace=args.workspace,
            inbox_dir=args.inbox_dir,
            state_path=args.state,
        )
    else:
        result = submit_receipt(
            args.receipt,
            parent_transcript_path=args.parent_transcript,
            child_transcript_path=args.child_transcript,
            inbox_dir=args.inbox_dir,
            state_path=args.state,
        )
    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
