#!/usr/bin/env python3
"""Secure custody and provenance for deterministic JARVIS NERVES evidence."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable, Mapping
import uuid

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
MISSION_SCHEMA = ROOT / "docs/schemas/nerves_mission_envelope_v1.schema.json"
EVIDENCE_SCHEMA = ROOT / "docs/schemas/nerves_integrity_evidence_v1.schema.json"
SKILL_DIR = ROOT / "skills/seal-nerves-integrity-audit"
COLLECTOR_PATH = SKILL_DIR / "scripts/collect_integrity_evidence.py"
MAX_MANIFEST_BYTES = 1_048_576
MAX_SOURCE_BYTES = 16_777_216
MAX_OUTPUT_BYTES = 2_097_152
ALLOWED_TOOLS = frozenset(
    {
        "filesystem_read",
        "git_read",
        "systemctl_read",
        "journalctl_read",
        "subprocess_allowlisted_read_only",
    }
)
MISSION_COMPILER_REV = "jarvis-integrity-v10-native-string-envelope"
IGNORED_SKILL_PARTS = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)
IGNORED_SKILL_SUFFIXES = frozenset({".pyc", ".pyo"})


class EvidenceBundleError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MissionAdmission:
    mission: dict[str, Any]
    manifest_sha256: str
    source_path: Path
    source_witness_sha256: str
    source_record: dict[str, Any]
    source_record_sha256: str
    skill_bundle_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceBundleResult:
    evidence_path: Path
    provenance_path: Path
    evidence_sha256: str
    provenance_sha256: str
    joined: bool


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceBundleError(f"duplicate_json_key:{key}")
        result[key] = value
    return result


def _secure_read(
    path: Path,
    *,
    max_bytes: int,
    exact_mode: int | None = None,
    owner_required: bool = True,
) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise EvidenceBundleError(f"secure_read_failed:{path}:{exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise EvidenceBundleError(f"not_regular_file:{path}")
        if owner_required and info.st_uid != os.getuid():
            raise EvidenceBundleError(f"file_not_owned:{path}")
        if exact_mode is not None and stat.S_IMODE(info.st_mode) != exact_mode:
            raise EvidenceBundleError(
                f"file_mode_must_be_{exact_mode:o}:{path}"
            )
        if info.st_size > max_bytes:
            raise EvidenceBundleError(f"file_too_large:{path}")
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
            raise EvidenceBundleError(f"file_too_large:{path}")
        return raw
    finally:
        os.close(fd)


def _secure_write_exclusive(path: Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise EvidenceBundleError(f"secure_write_failed:{path}:{exc}") from exc
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                raise OSError(f"short_write:{path}:{offset}/{len(raw)}")
            offset += written
        os.fsync(fd)
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
            raise EvidenceBundleError(f"output_mode_not_600:{path}")
    finally:
        os.close(fd)


def _open_mission_lock(path: Path) -> int:
    """Open an owner-only regular lock file without following links."""

    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise EvidenceBundleError(f"mission_lock_open_failed:{path}:{exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise EvidenceBundleError(f"mission_lock_not_regular:{path}")
        if info.st_uid != os.getuid():
            raise EvidenceBundleError(f"mission_lock_not_owned:{path}")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise EvidenceBundleError(f"mission_lock_mode_must_be_600:{path}")
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd
    except Exception:
        os.close(fd)
        raise


def _read_json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceBundleError(f"invalid_json:{exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceBundleError("json_root_must_be_object")
    return value


def _validate_schema(value: Mapping[str, Any], schema_path: Path, label: str) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        rendered = ";".join(
            f"{'/'.join(map(str, error.absolute_path))}:{error.message}"
            for error in errors[:8]
        )
        raise EvidenceBundleError(f"{label}_schema_invalid:{rendered}")


def skill_bundle_manifest(skill_dir: Path = SKILL_DIR) -> dict[str, Any]:
    resolved = skill_dir.resolve(strict=True)
    candidates = sorted(
        path
        for path in resolved.rglob("*")
        if path.is_file()
        and not any(part in IGNORED_SKILL_PARTS for part in path.parts)
        and path.suffix not in IGNORED_SKILL_SUFFIXES
    )
    entries: list[dict[str, Any]] = []
    for path in candidates:
        if not path.is_file():
            raise EvidenceBundleError(f"skill_bundle_source_missing:{path}")
        if path.is_symlink():
            raise EvidenceBundleError(f"skill_bundle_symlink:{path}")
        raw = _secure_read(path, max_bytes=MAX_SOURCE_BYTES)
        entries.append(
            {
                "path": path.relative_to(resolved).as_posix(),
                "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
                "size": len(raw),
                "sha256": _sha256(raw),
            }
        )
    if not entries or entries[0]["path"] != "SKILL.md":
        raise EvidenceBundleError("skill_bundle_missing_SKILL_md")
    return {"schema": "seal.skill-bundle.v1", "entries": entries}


def skill_bundle_digest(skill_dir: Path = SKILL_DIR) -> str:
    return _sha256(_canonical_bytes(skill_bundle_manifest(skill_dir)))


def _parse_source_refs(source_refs: list[str]) -> tuple[str, str, str]:
    values: dict[str, list[str]] = {}
    for ref in source_refs:
        key, separator, value = ref.partition(":")
        if separator:
            values.setdefault(key, []).append(value)
    for key in ("file", "record_sha256", "episode_anchor"):
        if len(values.get(key, [])) != 1:
            raise EvidenceBundleError(f"source_ref_{key}_must_be_unique")
    digest = values["record_sha256"][0]
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise EvidenceBundleError("source_ref_record_sha256_invalid")
    return values["file"][0], digest, values["episode_anchor"][0]


def _episode_key(record: Mapping[str, Any], episode_anchor: str) -> str:
    findings = sorted(
        " ".join(str(item).strip().lower().split())
        for item in record.get("findings", [])
    )
    material = {
        "agent": record.get("agent"),
        "action": record.get("action"),
        "state": record.get("state"),
        "findings": findings,
        "episode_anchor": episode_anchor,
        "compiler_rev": MISSION_COMPILER_REV,
    }
    return _sha256(_canonical_bytes(material))


def _locate_source_episode(
    raw: bytes,
    *,
    mission: Mapping[str, Any],
    episode_anchor: str,
) -> dict[str, Any]:
    nerve_fire = str(mission["nerve_fire_id"])
    prefix = "jarvis-integrity:"
    if not nerve_fire.startswith(prefix) or ":" not in nerve_fire[len(prefix) :]:
        raise EvidenceBundleError("nerve_fire_id_format_invalid")
    timestamp_and_prefix = nerve_fire[len(prefix) :]
    timestamp, correlation_prefix = timestamp_and_prefix.rsplit(":", 1)
    correlation = mission.get("correlation_id")
    if (
        not isinstance(correlation, str)
        or len(correlation) != 64
        or not correlation.startswith(correlation_prefix)
    ):
        raise EvidenceBundleError("correlation_id_nerve_fire_mismatch")
    matches: list[dict[str, Any]] = []
    for number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line, object_pairs_hook=_reject_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise EvidenceBundleError(f"source_jsonl_invalid_line_{number}") from exc
        if not isinstance(value, dict):
            raise EvidenceBundleError(f"source_line_{number}_not_object")
        if (
            value.get("ts") == timestamp
            and value.get("agent") == "JARVIS"
            and value.get("action") == "integrity_pulse"
            and _episode_key(value, episode_anchor) == correlation
        ):
            matches.append(value)
    if len(matches) != 1:
        raise EvidenceBundleError(
            f"source_episode_match_count_must_be_one:{len(matches)}"
        )
    return matches[0]


def admit_mission(
    manifest_path: Path,
    *,
    workspace: Path = ROOT,
    mission_schema: Path = MISSION_SCHEMA,
    skill_dir: Path = SKILL_DIR,
) -> MissionAdmission:
    raw = _secure_read(
        manifest_path, max_bytes=MAX_MANIFEST_BYTES, exact_mode=0o600
    )
    mission = _read_json(raw)
    _validate_schema(mission, mission_schema, "mission")
    if manifest_path.name != f"{mission['mission_id']}.json":
        raise EvidenceBundleError("manifest_filename_mission_id_mismatch")
    if mission["agent"] != "JARVIS":
        raise EvidenceBundleError("agent_must_be_JARVIS")
    if mission["risk_class"] != "A2_READ_ONLY":
        raise EvidenceBundleError("risk_class_must_be_A2_READ_ONLY")
    if mission["specialty"] != "architecture_integrity_audit":
        raise EvidenceBundleError("specialty_not_admitted")
    scope = mission["scope"]
    resolved_workspace = workspace.resolve(strict=True)
    if Path(scope["workspace"]).resolve(strict=True) != resolved_workspace:
        raise EvidenceBundleError("scope_workspace_mismatch")
    if scope["network"] != "none":
        raise EvidenceBundleError("network_must_be_none")
    if scope["services"] != []:
        raise EvidenceBundleError("services_must_be_empty")
    if set(mission["allowed_tools"]) != ALLOWED_TOOLS:
        raise EvidenceBundleError("allowed_tools_must_equal_A2_allowlist")
    skills = mission["skills"]
    if len(skills) != 1 or skills[0]["id"] != "seal-nerves-integrity-audit":
        raise EvidenceBundleError("single_integrity_skill_required")
    actual_skill_digest = skill_bundle_digest(skill_dir)
    if skills[0]["sha256"] != actual_skill_digest:
        raise EvidenceBundleError("skill_bundle_digest_mismatch")

    relative, expected_record_digest, episode_anchor = _parse_source_refs(
        mission["source_refs"]
    )
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise EvidenceBundleError("source_path_must_be_workspace_relative")
    source_path = resolved_workspace / relative_path
    if source_path.is_symlink():
        raise EvidenceBundleError("source_symlink_rejected")
    try:
        resolved_source = source_path.resolve(strict=True)
    except OSError as exc:
        raise EvidenceBundleError(f"source_resolve_failed:{source_path}") from exc
    if not resolved_source.is_relative_to(resolved_workspace):
        raise EvidenceBundleError("source_path_outside_workspace")
    if relative_path.as_posix() not in scope["paths"]:
        raise EvidenceBundleError("source_path_not_in_mission_scope")
    source_raw = _secure_read(
        source_path, max_bytes=MAX_SOURCE_BYTES, exact_mode=0o600
    )
    source_record = _locate_source_episode(
        source_raw, mission=mission, episode_anchor=episode_anchor
    )
    source_record_digest = _sha256(_canonical_bytes(source_record))
    if source_record_digest != expected_record_digest:
        raise EvidenceBundleError("source_record_digest_mismatch")
    source_witness_digest = _sha256(
        _canonical_bytes(
            {
                "path": relative_path.as_posix(),
                "record_sha256": source_record_digest,
                "timestamp": source_record.get("ts"),
                "correlation_id": mission.get("correlation_id"),
                "nerve_fire_id": mission["nerve_fire_id"],
            }
        )
    )
    return MissionAdmission(
        mission=mission,
        manifest_sha256=_sha256(raw),
        source_path=source_path,
        source_witness_sha256=source_witness_digest,
        source_record=source_record,
        source_record_sha256=source_record_digest,
        skill_bundle_sha256=actual_skill_digest,
    )


def _load_collector() -> Callable[[dict[str, Any]], dict[str, Any]]:
    spec = importlib.util.spec_from_file_location(
        "_seal_integrity_collector", COLLECTOR_PATH
    )
    if spec is None or spec.loader is None:
        raise EvidenceBundleError("collector_import_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.collect


def _provenance_for(
    admission: MissionAdmission,
    *,
    workspace: Path,
    evidence_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": "seal.nerves.integrity-provenance.v1",
        "mission_id": admission.mission["mission_id"],
        "manifest_sha256": admission.manifest_sha256,
        "skill_bundle_sha256": admission.skill_bundle_sha256,
        "source": {
            "path": admission.source_path.relative_to(
                workspace.resolve(strict=True)
            ).as_posix(),
            "episode_witness_sha256": admission.source_witness_sha256,
            "record_sha256": admission.source_record_sha256,
            "timestamp": admission.source_record.get("ts"),
            "correlation_id": admission.mission.get("correlation_id"),
            "nerve_fire_id": admission.mission["nerve_fire_id"],
        },
        "evidence_sha256": evidence_sha256,
    }


def _join_existing_bundle(
    admission: MissionAdmission,
    *,
    evidence_path: Path,
    provenance_path: Path,
    workspace: Path,
    evidence_schema: Path,
) -> EvidenceBundleResult | None:
    evidence_exists = os.path.lexists(evidence_path)
    provenance_exists = os.path.lexists(provenance_path)
    if evidence_exists != provenance_exists:
        raise EvidenceBundleError("partial_evidence_bundle")
    if not evidence_exists:
        return None

    evidence_raw = _secure_read(
        evidence_path, max_bytes=MAX_OUTPUT_BYTES, exact_mode=0o600
    )
    provenance_raw = _secure_read(
        provenance_path, max_bytes=MAX_OUTPUT_BYTES, exact_mode=0o600
    )
    evidence = _read_json(evidence_raw)
    provenance = _read_json(provenance_raw)
    _validate_schema(evidence, evidence_schema, "evidence")
    if evidence.get("mission_id") != admission.mission["mission_id"]:
        raise EvidenceBundleError("existing_evidence_mission_id_mismatch")
    if evidence_raw != _canonical_bytes(evidence) + b"\n":
        raise EvidenceBundleError("existing_evidence_not_canonical")
    evidence_digest = _sha256(evidence_raw)
    expected_provenance = _provenance_for(
        admission,
        workspace=workspace,
        evidence_sha256=evidence_digest,
    )
    if provenance != expected_provenance:
        raise EvidenceBundleError("existing_provenance_identity_mismatch")
    if provenance_raw != _canonical_bytes(provenance) + b"\n":
        raise EvidenceBundleError("existing_provenance_not_canonical")
    return EvidenceBundleResult(
        evidence_path=evidence_path,
        provenance_path=provenance_path,
        evidence_sha256=evidence_digest,
        provenance_sha256=_sha256(provenance_raw),
        joined=True,
    )


def build_integrity_evidence_bundle(
    manifest_path: Path,
    *,
    output_dir: Path,
    workspace: Path = ROOT,
    mission_schema: Path = MISSION_SCHEMA,
    evidence_schema: Path = EVIDENCE_SCHEMA,
    skill_dir: Path = SKILL_DIR,
    collect_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> EvidenceBundleResult:
    admission = admit_mission(
        manifest_path,
        workspace=workspace,
        mission_schema=mission_schema,
        skill_dir=skill_dir,
    )
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise EvidenceBundleError("output_dir_not_real")
    output_info = output_dir.stat()
    if output_info.st_uid != os.getuid():
        raise EvidenceBundleError("output_dir_not_owned")
    os.chmod(output_dir, 0o700)
    mission_id = str(uuid.UUID(admission.mission["mission_id"]))
    evidence_path = output_dir / f"{mission_id}.evidence.json"
    provenance_path = output_dir / f"{mission_id}.provenance.json"
    lock_path = output_dir / f".{mission_id}.bundle.lock"
    lock_fd = _open_mission_lock(lock_path)
    try:
        joined = _join_existing_bundle(
            admission,
            evidence_path=evidence_path,
            provenance_path=provenance_path,
            workspace=workspace,
            evidence_schema=evidence_schema,
        )
        if joined is not None:
            return joined

        collector = collect_fn or _load_collector()
        evidence = collector(admission.mission)
        _validate_schema(evidence, evidence_schema, "evidence")
        if evidence["mission_id"] != admission.mission["mission_id"]:
            raise EvidenceBundleError("evidence_mission_id_mismatch")
        evidence_raw = _canonical_bytes(evidence) + b"\n"
        if len(evidence_raw) > MAX_OUTPUT_BYTES:
            raise EvidenceBundleError("evidence_too_large")
        evidence_digest = _sha256(evidence_raw)
        provenance = _provenance_for(
            admission,
            workspace=workspace,
            evidence_sha256=evidence_digest,
        )
        provenance_raw = _canonical_bytes(provenance) + b"\n"
        _secure_write_exclusive(evidence_path, evidence_raw)
        try:
            _secure_write_exclusive(provenance_path, provenance_raw)
        except Exception:
            # Do not delete/overwrite an already-custodied evidence file.  A
            # missing sidecar is a visible failed-closed state.
            raise
        return EvidenceBundleResult(
            evidence_path=evidence_path,
            provenance_path=provenance_path,
            evidence_sha256=evidence_digest,
            provenance_sha256=_sha256(provenance_raw),
            joined=False,
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
