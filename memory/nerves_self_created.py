#!/usr/bin/env python3
"""Safe A2 lifecycle for nerves created from an agent's verified experience.

The module creates immutable candidates, binds sibling review and canary
evidence to hashes, and promotes only metadata into a dedicated read-only
registry. It never imports candidate code, edits a daemon, changes permissions,
or performs a production mutation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any
import uuid

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs/schemas/nerves_self_created_candidate_v1.schema.json"
REGISTRY_PATH = ROOT / "research/flywire_results/nerves_self_created/active_registry.json"
AGENTS = {"ADA", "ALICE", "DUM", "JARVIS", "NEXUS", "FABLE"}
NAMESPACE = uuid.UUID("b60c8aa7-7b05-4d5c-81c6-11145635318d")


class SelfCreatedNerveError(ValueError):
    """The candidate violates the SELF_CREATED A2 boundary."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _payload_hash(value: dict[str, Any], hash_field: str) -> str:
    payload = {key: item for key, item in value.items() if key != hash_field}
    return _sha256_bytes(_canonical_bytes(payload))


def _safe_resolve(path: Path, parent: Path) -> Path:
    resolved = path.resolve(strict=True)
    base = parent.resolve(strict=True)
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise SelfCreatedNerveError(f"path_outside_boundary:{path}") from exc
    if resolved.is_symlink():
        raise SelfCreatedNerveError(f"symlink_forbidden:{path}")
    return resolved


def skill_bundle_sha256(skill_dir: Path, *, root: Path = ROOT) -> str:
    skills_root = root / "skills"
    resolved = _safe_resolve(skill_dir, skills_root)
    if not resolved.is_dir() or not (resolved / "SKILL.md").is_file():
        raise SelfCreatedNerveError("skill_bundle_requires_real_SKILL.md")
    digest = hashlib.sha256()
    files = sorted(
        path for path in resolved.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and not path.is_symlink()
    )
    if not files:
        raise SelfCreatedNerveError("empty_skill_bundle")
    for path in files:
        relative = path.relative_to(resolved).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _write_private_once(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    payload = _canonical_bytes(value) + b"\n"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        existing = path.read_bytes()
        if existing == payload:
            return
        raise SelfCreatedNerveError(f"immutable_artifact_exists:{path}") from exc
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


def validate_candidate(candidate: dict[str, Any], *, root: Path = ROOT) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(candidate),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        detail = "; ".join(
            f"{'.'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
            for error in errors
        )
        raise SelfCreatedNerveError(f"candidate_schema_invalid:{detail}")
    if candidate["candidate_sha256"] != _payload_hash(candidate, "candidate_sha256"):
        raise SelfCreatedNerveError("candidate_hash_mismatch")
    pattern_id = candidate["pattern_id"]
    if any(item["pattern_id"] != pattern_id for item in candidate["observations"]):
        raise SelfCreatedNerveError("observation_pattern_mismatch")
    observation_ids = [item["observation_id"] for item in candidate["observations"]]
    if len(observation_ids) != len(set(observation_ids)):
        raise SelfCreatedNerveError("duplicate_observation")
    windows = {
        datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00")).date()
        for item in candidate["observations"]
    }
    if len(windows) < 2:
        raise SelfCreatedNerveError("observations_require_two_time_windows")
    skill_path = root / candidate["skill"]["path"]
    actual_hash = skill_bundle_sha256(skill_path, root=root)
    if actual_hash != candidate["skill"]["bundle_sha256"]:
        raise SelfCreatedNerveError("skill_bundle_hash_mismatch")


def propose_self_created_nerve(
    *,
    agent: str,
    name: str,
    pattern_id: str,
    observations: list[dict[str, Any]],
    skill_dir: Path,
    output_dir: Path,
    root: Path = ROOT,
    created_at: str | None = None,
) -> dict[str, Any]:
    agent = agent.upper()
    if agent not in AGENTS:
        raise SelfCreatedNerveError(f"unknown_agent:{agent}")
    relative_skill = _safe_resolve(skill_dir, root / "skills").relative_to(root).as_posix()
    bundle_hash = skill_bundle_sha256(skill_dir, root=root)
    observation_hashes = sorted(str(item.get("evidence_sha256", "")) for item in observations)
    candidate_id = str(
        uuid.uuid5(
            NAMESPACE,
            "|".join((agent, name, pattern_id, bundle_hash, *observation_hashes)),
        )
    )
    candidate: dict[str, Any] = {
        "schema": "seal.nerves.self_created_candidate.v1",
        "candidate_id": candidate_id,
        "agent": agent,
        "name": name,
        "pattern_id": pattern_id,
        "layer": "SELF_CREATED",
        "risk_class": "A2_READ_ONLY",
        "status": "SIBLING_REVIEW_PENDING",
        "observations": observations,
        "skill": {"path": relative_skill, "bundle_sha256": bundle_hash},
        "allowed_tools": [],
        "network": "none",
        "requires_independent_review": True,
        "created_at": created_at or _utc_now(),
    }
    candidate["candidate_sha256"] = _payload_hash(candidate, "candidate_sha256")
    validate_candidate(candidate, root=root)
    _write_private_once(output_dir / f"{candidate_id}.candidate.json", candidate)
    return candidate


def build_sibling_review(
    candidate: dict[str, Any],
    *,
    reviewer: str,
    verdict: str,
    evidence_sha256: str,
    reviewed_at: str | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    validate_candidate(candidate, root=root)
    reviewer = reviewer.upper()
    if reviewer not in AGENTS or reviewer == candidate["agent"]:
        raise SelfCreatedNerveError("reviewer_must_be_independent_sibling")
    if verdict not in {"APPROVED", "REJECTED"}:
        raise SelfCreatedNerveError("invalid_review_verdict")
    if len(evidence_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in evidence_sha256):
        raise SelfCreatedNerveError("invalid_review_evidence_hash")
    review = {
        "schema": "seal.nerves.self_created_review.v1",
        "candidate_id": candidate["candidate_id"],
        "candidate_sha256": candidate["candidate_sha256"],
        "skill_bundle_sha256": candidate["skill"]["bundle_sha256"],
        "reviewer": reviewer,
        "verdict": verdict,
        "evidence_sha256": evidence_sha256,
        "reviewed_at": reviewed_at or _utc_now(),
    }
    review["review_sha256"] = _payload_hash(review, "review_sha256")
    return review


def compile_self_created_mission(
    candidate: dict[str, Any],
    *,
    objective: str,
    root: Path = ROOT,
) -> dict[str, Any]:
    validate_candidate(candidate, root=root)
    if len(objective.strip()) < 10:
        raise SelfCreatedNerveError("objective_too_short")
    return {
        "schema": "seal.nerves.self_created_mission.v1",
        "mission_id": str(uuid.uuid5(NAMESPACE, candidate["candidate_id"] + "|" + objective)),
        "candidate_id": candidate["candidate_id"],
        "agent": candidate["agent"],
        "nerve_layer": "SELF_CREATED",
        "risk_class": "A2_READ_ONLY",
        "objective": objective,
        "skill": candidate["skill"],
        "allowed_tools": [],
        "network": "none",
        "termination": "Produce typed evidence; no system effect.",
    }


def _validate_review(candidate: dict[str, Any], review: dict[str, Any]) -> None:
    if review.get("candidate_id") != candidate["candidate_id"]:
        raise SelfCreatedNerveError("review_candidate_mismatch")
    if review.get("candidate_sha256") != candidate["candidate_sha256"]:
        raise SelfCreatedNerveError("review_candidate_hash_mismatch")
    if review.get("skill_bundle_sha256") != candidate["skill"]["bundle_sha256"]:
        raise SelfCreatedNerveError("review_skill_hash_mismatch")
    if review.get("reviewer") == candidate["agent"]:
        raise SelfCreatedNerveError("self_review_forbidden")
    if review.get("verdict") != "APPROVED":
        raise SelfCreatedNerveError("review_not_approved")
    if review.get("review_sha256") != _payload_hash(review, "review_sha256"):
        raise SelfCreatedNerveError("review_hash_mismatch")


def _validate_canary(candidate: dict[str, Any], canary: dict[str, Any]) -> None:
    required = {
        "candidate_id": candidate["candidate_id"],
        "candidate_sha256": candidate["candidate_sha256"],
        "skill_bundle_sha256": candidate["skill"]["bundle_sha256"],
        "passed": True,
        "risk_class": "A2_READ_ONLY",
        "allowed_tools": [],
        "network": "none",
        "mutations": 0,
    }
    for key, expected in required.items():
        if canary.get(key) != expected:
            raise SelfCreatedNerveError(f"canary_boundary_failed:{key}")
    if canary.get("canary_sha256") != _payload_hash(canary, "canary_sha256"):
        raise SelfCreatedNerveError("canary_hash_mismatch")


def promote_self_created_nerve(
    candidate: dict[str, Any],
    *,
    review: dict[str, Any],
    canary: dict[str, Any],
    william_approval_ref: str,
    registry_path: Path = REGISTRY_PATH,
    root: Path = ROOT,
) -> dict[str, Any]:
    validate_candidate(candidate, root=root)
    _validate_review(candidate, review)
    _validate_canary(candidate, canary)
    if not william_approval_ref.strip():
        raise SelfCreatedNerveError("william_approval_required")
    entry = {
        "candidate_id": candidate["candidate_id"],
        "agent": candidate["agent"],
        "name": candidate["name"],
        "layer": "SELF_CREATED",
        "autonomy_level": "A2_READ_ONLY",
        "skill": candidate["skill"],
        "candidate_sha256": candidate["candidate_sha256"],
        "review_sha256": review["review_sha256"],
        "canary_sha256": canary["canary_sha256"],
        "william_approval_ref": william_approval_ref,
        "status": "active_a2",
    }
    registry = {
        "schema": "seal.nerves.self_created_registry.v1",
        "entries": [],
    }
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    existing = {
        item["candidate_id"]: item for item in registry.get("entries", [])
    }
    if candidate["candidate_id"] in existing:
        if existing[candidate["candidate_id"]] != entry:
            raise SelfCreatedNerveError("registry_candidate_drift")
        return registry
    registry["entries"] = sorted(
        [*registry.get("entries", []), entry],
        key=lambda item: item["candidate_id"],
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(registry_path.parent, 0o700)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".active_registry.",
        suffix=".tmp",
        dir=registry_path.parent,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_canonical_bytes(registry) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, registry_path)
        os.chmod(registry_path, 0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return registry


def load_active_self_created_nerves(
    registry_path: Path = REGISTRY_PATH,
) -> list[dict[str, Any]]:
    if not registry_path.exists():
        return []
    if registry_path.stat().st_mode & 0o077:
        raise SelfCreatedNerveError("registry_permissions_not_private")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if registry.get("schema") != "seal.nerves.self_created_registry.v1":
        raise SelfCreatedNerveError("registry_schema_invalid")
    entries = registry.get("entries")
    if not isinstance(entries, list):
        raise SelfCreatedNerveError("registry_entries_invalid")
    for entry in entries:
        if (
            entry.get("layer") != "SELF_CREATED"
            or entry.get("autonomy_level") != "A2_READ_ONLY"
            or entry.get("status") != "active_a2"
        ):
            raise SelfCreatedNerveError("registry_entry_authority_invalid")
    return entries
