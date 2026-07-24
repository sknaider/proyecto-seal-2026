#!/usr/bin/env python3
"""Safe A2 lifecycle for nerves created from an agent's verified experience.

The module creates immutable candidates, binds sibling review and canary
evidence to hashes, and promotes only metadata into a dedicated read-only
registry. It never imports candidate code, edits a daemon, changes permissions,
or performs a production mutation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
import uuid

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs/schemas/nerves_self_created_candidate_v1.schema.json"
REGISTRY_PATH = ROOT / "research/flywire_results/nerves_self_created/active_registry.json"
AGENTS = {"ADA", "ALICE", "DUM", "JARVIS", "NEXUS", "FABLE"}
NAMESPACE = uuid.UUID("b60c8aa7-7b05-4d5c-81c6-11145635318d")
CHAT_REF_PREFIX = "chat_messages.id="
CANARY_MAX_AGE = timedelta(minutes=15)
AUTHORITY_DSN_FILE = Path("/home/dadito/.config/seal/ada_bridge_db_runtime.env")
AUTHORITY_DSN_KEY = "SEAL_DB_DSN"
SYSTEMCTL = Path("/usr/bin/systemctl")
JOURNALCTL = Path("/usr/bin/journalctl")
SYSTEMD_ATTESTATION_PROPERTIES = (
    "ActiveState",
    "SubState",
    "Result",
    "ExecMainCode",
    "ExecMainStatus",
    "InvocationID",
    "User",
    "Group",
    "PrivateNetwork",
    "ProtectSystem",
    "ProtectHome",
    "NoNewPrivileges",
    "RestrictAddressFamilies",
    "SystemCallFilter",
    "BindReadOnlyPaths",
    "ExecStart",
    "ControlGroup",
    "TasksMax",
    "MemoryMax",
    "CPUQuotaPerSecUSec",
)
REVIEW_AUTHORITY_PATTERN = re.compile(
    r"^NERVES_REVIEW_V1 "
    r"candidate_id=(?P<candidate>[0-9a-f-]{36}) "
    r"canary_sha256=(?P<canary>[0-9a-f]{64}) "
    r"verdict=(?P<verdict>APPROVED|REJECTED)$"
)
APPROVAL_AUTHORITY_PATTERN = re.compile(
    r"^NERVES_APPROVAL_V1 "
    r"candidate_id=(?P<candidate>[0-9a-f-]{36}) "
    r"canary_sha256=(?P<canary>[0-9a-f]{64}) "
    r"review_sha256=(?P<review>[0-9a-f]{64}) "
    r"action=(?P<action>ACTIVATE_A2|REJECT)$"
)


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


def _fixed_command(command: list[str], *, timeout: int = 10) -> str:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={"LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"},
    )
    if result.returncode != 0:
        raise SelfCreatedNerveError(
            "trusted_attestation_command_failed:"
            f"{Path(command[0]).name}:rc={result.returncode}:"
            f"{result.stderr.strip()[:300]}"
        )
    return result.stdout


def _load_live_systemd_attestation(
    unit: str,
    expected_invocation_id: str,
) -> dict[str, Any]:
    if not re.fullmatch(
        r"seal-self-created-[0-9a-f-]{36}-[0-9a-f]{12}\.service",
        unit,
    ):
        raise SelfCreatedNerveError("canary_systemd_unit_invalid")
    if not re.fullmatch(r"[0-9a-f]{32}", expected_invocation_id):
        raise SelfCreatedNerveError("canary_systemd_invocation_id_invalid")
    properties_arg = ",".join(SYSTEMD_ATTESTATION_PROPERTIES)
    output = _fixed_command(
        [
            str(SYSTEMCTL),
            "show",
            "--no-pager",
            f"--property={properties_arg}",
            unit,
        ]
    )
    properties: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    if set(properties) != set(SYSTEMD_ATTESTATION_PROPERTIES):
        raise SelfCreatedNerveError("canary_systemd_properties_incomplete")
    expected_properties = {
        "ActiveState": "active",
        "SubState": "exited",
        "Result": "success",
        "ExecMainCode": "1",
        "ExecMainStatus": "0",
        "InvocationID": expected_invocation_id,
        "User": "dadito",
        "Group": "dadito",
        "PrivateNetwork": "yes",
        "ProtectSystem": "strict",
        "ProtectHome": "tmpfs",
        "NoNewPrivileges": "yes",
        "RestrictAddressFamilies": "AF_UNIX",
        "TasksMax": "8",
        "MemoryMax": "268435456",
        "CPUQuotaPerSecUSec": "500ms",
    }
    for key, expected in expected_properties.items():
        if properties.get(key) != expected:
            raise SelfCreatedNerveError(
                f"canary_systemd_property_invalid:{key}"
            )
    exec_start = properties["ExecStart"]
    if (
        "path=/usr/bin/python3" not in exec_start
        or "argv[]=/usr/bin/python3 /opt/nerves/worker.py "
        "--skill-script /opt/nerves/skill.py "
        "--source /opt/nerves/source.json "
        "--candidate /opt/nerves/candidate.json "
        "--mission /opt/nerves/mission.json "
        "--protected-pid " not in exec_start
    ):
        raise SelfCreatedNerveError("canary_systemd_execstart_invalid")
    bind_paths = properties["BindReadOnlyPaths"]
    for binding in (
        "/opt/nerves/worker.py",
        "/opt/nerves/skill.py",
        "/opt/nerves/source.json",
        "/opt/nerves/candidate.json",
        "/opt/nerves/mission.json",
    ):
        if binding not in bind_paths:
            raise SelfCreatedNerveError(
                f"canary_systemd_readonly_binding_missing:{binding}"
            )
    syscall_filter = properties["SystemCallFilter"]
    filter_tokens = syscall_filter.split()
    denied_syscalls = {token.removeprefix("~") for token in filter_tokens}
    if (
        not filter_tokens
        or not filter_tokens[0].startswith("~")
        or denied_syscalls
        != {"kill", "tkill", "tgkill", "pidfd_send_signal"}
    ):
        raise SelfCreatedNerveError("canary_systemd_signal_filter_invalid")
    journal = _fixed_command(
        [
            "/usr/bin/sudo",
            "-n",
            str(JOURNALCTL),
            "--quiet",
            "--output=cat",
            f"_SYSTEMD_INVOCATION_ID={expected_invocation_id}",
        ]
    )
    worker_records: list[dict[str, Any]] = []
    for line in journal.splitlines():
        if not line.lstrip().startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if value.get("schema") == "seal.nerves.self_created_effect_worker.v1":
            worker_records.append(value)
    if len(worker_records) != 1:
        raise SelfCreatedNerveError("canary_systemd_worker_record_invalid")
    control_group = properties["ControlGroup"]
    if control_group:
        if (
            not control_group.startswith("/system.slice/")
            or ".." in control_group
        ):
            raise SelfCreatedNerveError(
                f"canary_systemd_cgroup_invalid:{control_group}"
            )
        cgroup_procs = (
            Path("/sys/fs/cgroup")
            / control_group.lstrip("/")
            / "cgroup.procs"
        )
        try:
            live_pids = [
                line.strip()
                for line in cgroup_procs.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]
        except FileNotFoundError:
            live_pids = []
        except OSError as exc:
            raise SelfCreatedNerveError(
                "canary_systemd_cgroup_unverifiable"
            ) from exc
    else:
        # systemd removes an empty transient cgroup after the oneshot exits.
        # A surviving descendant keeps ControlGroup populated.
        live_pids = []
    if live_pids:
        raise SelfCreatedNerveError("canary_systemd_cgroup_not_empty")
    return {
        "unit": unit,
        "invocation_id": expected_invocation_id,
        "properties": properties,
        "properties_sha256": _sha256_bytes(_canonical_bytes(properties)),
        "worker_record": worker_records[0],
        "worker_record_sha256": _sha256_bytes(
            _canonical_bytes(worker_records[0])
        ),
        "cgroup_empty": True,
    }


def _read_private_env_value(path: Path, key: str) -> str:
    try:
        stat = path.stat()
    except OSError as exc:
        raise SelfCreatedNerveError("trusted_authority_dsn_unavailable") from exc
    if stat.st_mode & 0o077:
        raise SelfCreatedNerveError("trusted_authority_dsn_not_private")
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        values[name.strip()] = value.strip().strip("\"").strip("'")
    result = values.get(key, "")
    if not result.startswith(("postgresql://", "postgres://")):
        raise SelfCreatedNerveError("trusted_authority_dsn_invalid")
    return result


async def _fetch_chat_authority_rows_async(ids: tuple[int, int]) -> list[dict[str, Any]]:
    try:
        import asyncpg
    except ImportError as exc:
        raise SelfCreatedNerveError(
            "trusted_authority_database_driver_missing"
        ) from exc
    dsn = _read_private_env_value(AUTHORITY_DSN_FILE, AUTHORITY_DSN_KEY)
    connection = await asyncpg.connect(dsn, timeout=5)
    try:
        records = await connection.fetch(
            """
            SELECT id, sender_type, sender_name, channel, content, created_at,
                   metadata
            FROM soul_v3.chat_messages
            WHERE id = ANY($1::bigint[])
            ORDER BY id
            """,
            list(ids),
        )
    finally:
        await connection.close()
    return [dict(record) for record in records]


def _fetch_chat_authority_rows(ids: tuple[int, int]) -> list[dict[str, Any]]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_fetch_chat_authority_rows_async(ids))
    raise SelfCreatedNerveError(
        "trusted_authority_verifier_requires_sync_boundary"
    )


def _chat_ref_id(value: str, error: str) -> int:
    if (
        not isinstance(value, str)
        or not value.startswith(CHAT_REF_PREFIX)
        or not value.removeprefix(CHAT_REF_PREFIX).isdigit()
    ):
        raise SelfCreatedNerveError(error)
    return int(value.removeprefix(CHAT_REF_PREFIX))


def _verify_trusted_chat_authority(
    candidate: dict[str, Any],
    review: dict[str, Any],
    canary: dict[str, Any],
    william_approval_ref: str,
) -> None:
    reviewer_id = _chat_ref_id(
        str(review.get("reviewer_ref", "")),
        "reviewer_chat_reference_required",
    )
    approval_id = _chat_ref_id(
        william_approval_ref,
        "william_approval_required",
    )
    rows = _fetch_chat_authority_rows((reviewer_id, approval_id))
    indexed = {int(row["id"]): row for row in rows}
    if set(indexed) != {reviewer_id, approval_id}:
        raise SelfCreatedNerveError("trusted_authority_row_missing")
    reviewer_row = indexed[reviewer_id]
    approval_row = indexed[approval_id]
    def authenticated_session_user(row: dict[str, Any]) -> str:
        metadata = row.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                return ""
        if not isinstance(metadata, dict):
            return ""
        return str(metadata.get("session_user", ""))

    if (
        reviewer_row.get("sender_type") != "user"
        or str(reviewer_row.get("sender_name", "")).upper()
        != review["reviewer"]
        or authenticated_session_user(reviewer_row).upper()
        != review["reviewer"]
        or reviewer_row.get("channel") != "web_chat"
    ):
        raise SelfCreatedNerveError("trusted_reviewer_identity_invalid")
    review_match = REVIEW_AUTHORITY_PATTERN.fullmatch(
        str(reviewer_row.get("content", "")).strip()
    )
    if (
        review_match is None
        or review_match["candidate"] != candidate["candidate_id"]
        or review_match["canary"] != canary["canary_sha256"]
        or review_match["verdict"] != "APPROVED"
    ):
        raise SelfCreatedNerveError("trusted_reviewer_statement_invalid")
    if (
        approval_row.get("sender_type") != "user"
        or str(approval_row.get("sender_name", "")).casefold() != "william"
        or authenticated_session_user(approval_row).casefold() != "william"
        or approval_row.get("channel") != "web_chat"
    ):
        raise SelfCreatedNerveError("trusted_william_identity_invalid")
    approval_match = APPROVAL_AUTHORITY_PATTERN.fullmatch(
        str(approval_row.get("content", "")).strip()
    )
    if (
        approval_match is None
        or approval_match["candidate"] != candidate["candidate_id"]
        or approval_match["canary"] != canary["canary_sha256"]
        or approval_match["review"] != review["review_sha256"]
        or approval_match["action"] != "ACTIVATE_A2"
    ):
        raise SelfCreatedNerveError("trusted_william_statement_invalid")
    issued_at = datetime.fromisoformat(
        str(canary["issued_at"]).replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    reviewer_at = reviewer_row["created_at"].astimezone(timezone.utc)
    approval_at = approval_row["created_at"].astimezone(timezone.utc)
    if reviewer_at < issued_at or approval_at < reviewer_at:
        raise SelfCreatedNerveError("trusted_authority_order_invalid")


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
    canary: dict[str, Any] | None = None,
    reviewer_ref: str | None = None,
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
    if canary is not None:
        _validate_canary(candidate, canary, root=root)
        if (
            reviewer_ref is None
            or not reviewer_ref.startswith(CHAT_REF_PREFIX)
            or not reviewer_ref.removeprefix(CHAT_REF_PREFIX).isdigit()
        ):
            raise SelfCreatedNerveError("reviewer_chat_reference_required")
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
    if canary is not None:
        review["canary_sha256"] = canary["canary_sha256"]
        review["reviewer_ref"] = reviewer_ref
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


def _validate_review(
    candidate: dict[str, Any],
    review: dict[str, Any],
    canary: dict[str, Any],
) -> None:
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
    if review.get("canary_sha256") != canary.get("canary_sha256"):
        raise SelfCreatedNerveError("review_canary_hash_mismatch")
    reviewer_ref = review.get("reviewer_ref")
    if (
        not isinstance(reviewer_ref, str)
        or not reviewer_ref.startswith(CHAT_REF_PREFIX)
        or not reviewer_ref.removeprefix(CHAT_REF_PREFIX).isdigit()
    ):
        raise SelfCreatedNerveError("reviewer_chat_reference_required")
    if review.get("review_sha256") != _payload_hash(review, "review_sha256"):
        raise SelfCreatedNerveError("review_hash_mismatch")


def _validate_canary(
    candidate: dict[str, Any],
    canary: dict[str, Any],
    *,
    root: Path = ROOT,
    now: datetime | None = None,
) -> None:
    required = {
        "schema": "seal.nerves.self_created_canary.v6",
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
    boundary = canary.get("effective_boundary")
    if not isinstance(boundary, dict):
        raise SelfCreatedNerveError("canary_effective_boundary_missing")
    effective_required = {
        "backend": "systemd_transient_system",
        "af_inet_denied": True,
        "home_read_denied": True,
        "persistent_write_denied": True,
        "service_restart_denied": True,
        "signal_denied": True,
        "credential_env_names": [],
        "forbidden_probe_absent_after": True,
        "protected_process_unchanged": True,
    }
    for key, expected in effective_required.items():
        if boundary.get(key) != expected:
            raise SelfCreatedNerveError(
                f"canary_effective_boundary_failed:{key}"
            )
    invocation_id = canary.get("invocation_id")
    unit = boundary.get("unit")
    if not isinstance(invocation_id, str) or not isinstance(unit, str):
        raise SelfCreatedNerveError("canary_invocation_binding_invalid")
    if not unit.startswith(
        f"seal-self-created-{candidate['candidate_id']}-"
    ):
        raise SelfCreatedNerveError("canary_candidate_unit_binding_invalid")
    try:
        issued_at = datetime.fromisoformat(
            str(canary["issued_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        expires_at = datetime.fromisoformat(
            str(canary["expires_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise SelfCreatedNerveError("canary_freshness_invalid") from exc
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if (
        expires_at <= issued_at
        or expires_at - issued_at > CANARY_MAX_AGE
        or current < issued_at
        or current > expires_at
    ):
        raise SelfCreatedNerveError("canary_freshness_invalid")
    artifacts = canary.get("artifacts")
    if not isinstance(artifacts, dict):
        raise SelfCreatedNerveError("canary_artifact_bindings_missing")
    expected_roots = {
        "launcher": root / "skills/seal-nerves-self-creation/scripts",
        "worker": root / "skills/seal-nerves-self-creation/scripts",
        "source": root / "research/flywire_results/nerves_self_created",
        "skill_script": root / candidate["skill"]["path"],
        "candidate": root / "research/flywire_results/nerves_self_created",
        "mission": root / "research/flywire_results/nerves_self_created",
        "validator": root / "memory",
    }
    for name, parent in expected_roots.items():
        binding = artifacts.get(name)
        if not isinstance(binding, dict):
            raise SelfCreatedNerveError(f"canary_artifact_binding_missing:{name}")
        path = _safe_resolve(root / str(binding.get("path", "")), parent)
        if _sha256_bytes(path.read_bytes()) != binding.get("sha256"):
            raise SelfCreatedNerveError(f"canary_artifact_hash_mismatch:{name}")
    if artifacts["validator"].get("path") != "memory/nerves_self_created.py":
        raise SelfCreatedNerveError("canary_validator_path_invalid")
    try:
        bound_candidate = json.loads(
            (root / artifacts["candidate"]["path"]).read_text(
                encoding="utf-8"
            )
        )
        bound_mission = json.loads(
            (root / artifacts["mission"]["path"]).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise SelfCreatedNerveError("canary_bound_input_invalid") from exc
    if bound_candidate != candidate:
        raise SelfCreatedNerveError("canary_candidate_file_mismatch")
    expected_mission = {
        "candidate_id": candidate["candidate_id"],
        "agent": candidate["agent"],
        "nerve_layer": "SELF_CREATED",
        "risk_class": "A2_READ_ONLY",
        "skill": candidate["skill"],
        "allowed_tools": [],
        "network": "none",
    }
    for key, expected in expected_mission.items():
        if bound_mission.get(key) != expected:
            raise SelfCreatedNerveError(
                f"canary_mission_file_mismatch:{key}"
            )
    stored_attestation = canary.get("systemd_attestation")
    if not isinstance(stored_attestation, dict):
        raise SelfCreatedNerveError("canary_systemd_attestation_missing")
    live_attestation = _load_live_systemd_attestation(unit, invocation_id)
    for key in (
        "unit",
        "invocation_id",
        "properties_sha256",
        "worker_record_sha256",
        "cgroup_empty",
    ):
        if stored_attestation.get(key) != live_attestation[key]:
            raise SelfCreatedNerveError(
                f"canary_systemd_attestation_mismatch:{key}"
            )
    expected_bindings = {
        (
            str((root / artifacts["worker"]["path"]).resolve(strict=True)),
            "/opt/nerves/worker.py",
        ),
        (
            str((root / artifacts["skill_script"]["path"]).resolve(strict=True)),
            "/opt/nerves/skill.py",
        ),
        (
            str((root / artifacts["source"]["path"]).resolve(strict=True)),
            "/opt/nerves/source.json",
        ),
        (
            str((root / artifacts["candidate"]["path"]).resolve(strict=True)),
            "/opt/nerves/candidate.json",
        ),
        (
            str((root / artifacts["mission"]["path"]).resolve(strict=True)),
            "/opt/nerves/mission.json",
        ),
    }
    actual_bindings: set[tuple[str, str]] = set()
    for token in live_attestation["properties"]["BindReadOnlyPaths"].split():
        parts = token.split(":")
        if len(parts) >= 2:
            actual_bindings.add((parts[0], parts[1]))
    if actual_bindings != expected_bindings:
        raise SelfCreatedNerveError("canary_systemd_source_binding_invalid")
    worker_record = live_attestation["worker_record"]
    if (
        worker_record.get("ok") is not True
        or worker_record.get("boundary") != {
            key: value
            for key, value in boundary.items()
            if key
            not in {
                "backend",
                "unit",
                "forbidden_probe_absent_after",
                "protected_process_unchanged",
            }
        }
        or worker_record.get("skill_result", {}).get("result_sha256")
        != canary.get("evidence_sha256")
        or worker_record.get("candidate_file_sha256")
        != artifacts["candidate"]["sha256"]
        or worker_record.get("mission_file_sha256")
        != artifacts["mission"]["sha256"]
    ):
        raise SelfCreatedNerveError("canary_systemd_worker_binding_invalid")
    before = canary.get("protected_process_before")
    after = canary.get("protected_process_after")
    if not isinstance(before, dict) or before != after:
        raise SelfCreatedNerveError("canary_protected_process_binding_invalid")
    if (
        f"--protected-pid {before.get('pid')} "
        not in live_attestation["properties"]["ExecStart"] + " "
    ):
        raise SelfCreatedNerveError("canary_protected_pid_execstart_mismatch")
    try:
        proc = Path("/proc") / str(int(after["pid"]))
        stat_fields = (proc / "stat").read_text(encoding="utf-8").split()
        executable = (proc / "exe").resolve(strict=True)
        current_process = {
            "pid": int(after["pid"]),
            "starttime_ticks": int(stat_fields[21]),
            "exe": str(executable),
            "exe_sha256": _sha256_bytes(executable.read_bytes()),
        }
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise SelfCreatedNerveError(
            "canary_protected_process_unverifiable"
        ) from exc
    if current_process != after:
        raise SelfCreatedNerveError("canary_protected_process_changed")
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
    _validate_canary(candidate, canary, root=root)
    _validate_review(candidate, review, canary)
    if (
        not william_approval_ref.startswith(CHAT_REF_PREFIX)
        or not william_approval_ref.removeprefix(CHAT_REF_PREFIX).isdigit()
    ):
        raise SelfCreatedNerveError("william_approval_required")
    _verify_trusted_chat_authority(
        candidate,
        review,
        canary,
        william_approval_ref,
    )
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
