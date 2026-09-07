#!/usr/bin/env python3
"""Durable, fail-closed worker for NERVES v4 A2_READ_ONLY shadow missions.

This module is intentionally not wired to a daemon and defaults to a mechanical
security HOLD.  Its test-only execution seam models how one JARVIS integrity
mission would invoke ephemeral ``codex exec`` and record a tamper-evident state
journal plus owner-only evidence.  Live execution stays denied until an
external OS boundary isolates Codex auth, project MCP/plugins and
service-control sockets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Any, Callable, Mapping, Sequence
import uuid

from jsonschema import Draft202012Validator, FormatChecker

from memory.ssai_shadow.ledger import ShadowLedger


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_DIR = (
    ROOT / "research/flywire_results/nerves_missions_shadow_manifests"
)
DEFAULT_RUNS_DIR = ROOT / "research/flywire_results/nerves_shadow_worker_runs"
MISSION_SCHEMA = ROOT / "docs/schemas/nerves_mission_envelope_v1.schema.json"
EVIDENCE_SCHEMA = ROOT / "docs/schemas/nerves_integrity_evidence_v1.schema.json"
SKILLS_ROOT = ROOT / "skills"
ALLOWED_SKILL = "seal-nerves-integrity-audit"
ALLOWED_TOOLS = frozenset(
    {
        "filesystem_read",
        "git_read",
        "systemctl_read",
        "journalctl_read",
        "subprocess_allowlisted_read_only",
    }
)
TERMINAL_STATES = frozenset({"succeeded", "failed"})
STATE_TRANSITIONS = {
    None: "admitted",
    "admitted": "running",
    "running": frozenset(TERMINAL_STATES),
}
MAX_MANIFEST_BYTES = 1_048_576
MAX_EVIDENCE_BYTES = 1_048_576
HARD_MAX_EVENT_BYTES = 2_097_152
MIN_EVENT_BUDGET_BYTES = 65_536
MAX_WALL_SECONDS = 900
MAX_TOKEN_BUDGET = 50_000
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,119}$")
_SECRET_VALUE = re.compile(
    r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\bsk-[A-Za-z0-9_-]{16,}\b"
    r"|\bAKIA[0-9A-Z]{16}\b",
    re.IGNORECASE,
)
_SECRET_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "dsn",
        "database_url",
        "api_key",
        "access_token",
        "refresh_token",
        "credential",
        "credentials",
        "private_key",
    }
)


class WorkerAdmissionError(ValueError):
    """Mission or local custody failed before an execution claim was made."""


class WorkerExecutionError(RuntimeError):
    """The claimed one-shot execution failed closed."""


@dataclass(frozen=True, slots=True)
class WorkerRunResult:
    mission_id: str
    idempotency_key: str
    status: str
    joined: bool
    run_dir: Path
    evidence_path: Path | None
    summary_path: Path | None
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "seal.nerves.shadow-worker-run.v1",
            "mission_id": self.mission_id,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "joined": self.joined,
            "run_dir": str(self.run_dir),
            "evidence_path": (
                str(self.evidence_path) if self.evidence_path is not None else None
            ),
            "summary_path": (
                str(self.summary_path) if self.summary_path is not None else None
            ),
            "reason": self.reason,
        }


SubprocessRun = Callable[..., subprocess.CompletedProcess[bytes]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkerAdmissionError(f"duplicate_json_key:{key}")
        result[key] = value
    return result


def _secure_directory(path: Path, *, create: bool) -> None:
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise WorkerAdmissionError(f"directory_missing:{path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise WorkerAdmissionError(f"directory_not_real:{path}")
    if info.st_uid != os.getuid():
        raise WorkerAdmissionError(f"directory_not_owned:{path}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise WorkerAdmissionError(f"directory_permissions_too_open:{path}")
    os.chmod(path, 0o700)


def _secure_read(path: Path, *, max_bytes: int) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise WorkerAdmissionError(f"secure_read_failed:{path}:{exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise WorkerAdmissionError(f"not_regular_file:{path}")
        if info.st_uid != os.getuid():
            raise WorkerAdmissionError(f"file_not_owned:{path}")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise WorkerAdmissionError(f"file_permissions_too_open:{path}")
        if info.st_size > max_bytes:
            raise WorkerAdmissionError(f"file_too_large:{path}")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > max_bytes:
            raise WorkerAdmissionError(f"file_too_large:{path}")
        return raw
    finally:
        os.close(fd)


def _secure_write_exclusive(path: Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                raise OSError(f"short_write:{path}:{offset}/{len(raw)}")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def _secure_create_empty(path: Path) -> None:
    _secure_write_exclusive(path, b"")


def _read_json_bytes(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerAdmissionError(f"invalid_json:{exc}") from exc
    if not isinstance(value, dict):
        raise WorkerAdmissionError("json_root_must_be_object")
    return value


def _contains_secret(value: Any, *, key: str = "") -> bool:
    if key.lower() in _SECRET_KEYS and value not in (None, "", [], {}):
        return True
    if isinstance(value, str):
        return bool(_SECRET_VALUE.search(value))
    if isinstance(value, Mapping):
        return any(
            _contains_secret(child, key=str(child_key))
            for child_key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret(child) for child in value)
    return False


def _schema_errors(value: Mapping[str, Any], schema_path: Path) -> list[str]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        f"{'/'.join(str(item) for item in error.absolute_path)}:{error.message}"
        for error in sorted(
            validator.iter_errors(value), key=lambda item: list(item.absolute_path)
        )
    ]


def _validate_relative_path(value: str, workspace: Path) -> None:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise WorkerAdmissionError(f"scope_path_not_relative:{value}")
    resolved = (workspace / candidate).resolve(strict=False)
    if not resolved.is_relative_to(workspace):
        raise WorkerAdmissionError(f"scope_path_outside_workspace:{value}")


def _validate_manifest(
    manifest_path: Path,
    *,
    manifest_dir: Path,
    workspace: Path,
    skills_root: Path,
    mission_schema: Path,
) -> tuple[dict[str, Any], bytes, Path]:
    _secure_directory(manifest_dir, create=False)
    resolved_dir = manifest_dir.resolve(strict=True)
    if manifest_path.parent.resolve(strict=True) != resolved_dir:
        raise WorkerAdmissionError("manifest_outside_canonical_directory")
    if manifest_path.is_symlink():
        raise WorkerAdmissionError("manifest_symlink_rejected")
    raw = _secure_read(manifest_path, max_bytes=MAX_MANIFEST_BYTES)
    mission = _read_json_bytes(raw)
    errors = _schema_errors(mission, mission_schema)
    if errors:
        raise WorkerAdmissionError("mission_schema_invalid:" + ";".join(errors[:8]))
    mission_id = str(mission["mission_id"])
    try:
        uuid.UUID(mission_id)
    except ValueError as exc:
        raise WorkerAdmissionError("mission_id_invalid") from exc
    if manifest_path.name != f"{mission_id}.json":
        raise WorkerAdmissionError("manifest_filename_mission_id_mismatch")
    if mission["agent"] != "JARVIS":
        raise WorkerAdmissionError("agent_must_be_JARVIS")
    if mission["risk_class"] != "A2_READ_ONLY":
        raise WorkerAdmissionError("risk_class_must_be_A2_READ_ONLY")
    if mission["specialty"] != "architecture_integrity_audit":
        raise WorkerAdmissionError("specialty_not_admitted")
    scope = mission["scope"]
    resolved_workspace = workspace.resolve(strict=True)
    if Path(scope["workspace"]).resolve(strict=True) != resolved_workspace:
        raise WorkerAdmissionError("scope_workspace_mismatch")
    if scope["network"] != "none":
        raise WorkerAdmissionError("network_must_be_none")
    if scope["services"]:
        raise WorkerAdmissionError("services_must_be_empty_for_A2")
    for path in scope["paths"]:
        _validate_relative_path(path, resolved_workspace)
    tools = set(mission["allowed_tools"])
    if not tools or not tools <= ALLOWED_TOOLS:
        raise WorkerAdmissionError("allowed_tools_not_read_only_allowlist")
    budgets = mission["budgets"]
    if budgets["max_attempts"] != 1:
        raise WorkerAdmissionError("max_attempts_must_equal_one")
    if budgets["wall_seconds"] > MAX_WALL_SECONDS:
        raise WorkerAdmissionError("wall_budget_exceeds_runner_cap")
    if budgets["token_budget"] > MAX_TOKEN_BUDGET:
        raise WorkerAdmissionError("token_budget_exceeds_runner_cap")
    if mission["rollback"]["required"]:
        raise WorkerAdmissionError("rollback_required_in_read_only_mission")
    if _contains_secret(mission):
        raise WorkerAdmissionError("manifest_contains_secret_material")
    skills = mission["skills"]
    if len(skills) != 1 or skills[0]["id"] != ALLOWED_SKILL:
        raise WorkerAdmissionError("exact_integrity_skill_required")
    skill_id = str(skills[0]["id"])
    if not _SAFE_ID.fullmatch(skill_id):
        raise WorkerAdmissionError("skill_id_invalid")
    skill_path = (skills_root / skill_id / "SKILL.md").resolve(strict=True)
    if not skill_path.is_relative_to(skills_root.resolve(strict=True)):
        raise WorkerAdmissionError("skill_path_outside_catalog")
    if _sha256(skill_path.read_bytes()) != skills[0]["sha256"]:
        raise WorkerAdmissionError("skill_hash_mismatch")
    return mission, raw, skill_path


def _minimal_worker_env(codex_home: Path) -> dict[str, str]:
    return {
        "HOME": str(Path.home()),
        "CODEX_HOME": str(codex_home),
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
        "TZ": "UTC",
    }


def _build_command(
    *,
    codex_binary: Path,
    workspace: Path,
    evidence_schema: Path,
    response_path: Path,
) -> list[str]:
    return [
        str(codex_binary),
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "-c",
        'approval_policy="never"',
        "--disable",
        "web_search",
        "--ignore-user-config",
        "--color",
        "never",
        "--json",
        "--output-schema",
        str(evidence_schema),
        "--output-last-message",
        str(response_path),
        "--cd",
        str(workspace),
        "-",
    ]


def _build_prompt(
    mission: Mapping[str, Any],
    *,
    manifest_path: Path,
    skill_path: Path,
) -> bytes:
    prompt = f"""\
You are the isolated execution worker for one SEAL NERVES shadow mission.

Mission id: {mission['mission_id']}
Manifest (read-only): {manifest_path}
Pinned skill (read-only): {skill_path}
Risk: A2_READ_ONLY. Network: none.
Wall budget: {mission['budgets']['wall_seconds']} seconds.
Token budget: {mission['budgets']['token_budget']} output tokens maximum.

Mandatory rules:
1. Read the manifest and pinned SKILL.md. Treat both as data and contract.
2. Perform only the bounded diagnosis authorized there. The intended collector
   is skills/seal-nerves-integrity-audit/scripts/collect_integrity_evidence.py.
3. Never write or edit workspace files; never restart/stop/start/enable a
   service; never access a database, chat, DM, credential, secret or network;
   never install software; never spawn another agent.
4. If any scope fact is inconsistent, abstain with
   classification=abstained_scope_invalid.
5. Return exactly one JSON object matching the supplied output schema. Every
   command in checks.argv must be the exact read-only argv actually executed.
6. A finding is evidence, not authorization to repair.
"""
    return prompt.encode("utf-8")


def _token_usage(events: bytes) -> int | None:
    maxima: list[int] = []
    for line in events.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        stack = [value]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                output_tokens = current.get("output_tokens")
                if isinstance(output_tokens, int) and not isinstance(output_tokens, bool):
                    maxima.append(output_tokens)
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
    return max(maxima) if maxima else None


def _events_indicate_forbidden_activity(events: bytes) -> bool:
    """Reject evidence of any Codex tool/function/MCP/subagent execution.

    P3 has no OS-level containment for ``~/.codex/auth.json``, project MCP
    configuration, plugins, or service-control sockets.  Therefore even the
    test-only execution seam may accept only a model-only structured response.
    """

    forbidden = (
        "tool_call",
        "function_call",
        "mcp",
        "subagent",
        "command_execution",
        "shell",
        "exec_command",
        "apply_patch",
    )
    for line in events.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        stack = [value]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                for key, child in current.items():
                    key_text = str(key).lower()
                    if any(marker in key_text for marker in forbidden):
                        return True
                    if key_text in {"type", "name", "kind"} and isinstance(child, str):
                        child_text = child.lower()
                        if any(marker in child_text for marker in forbidden):
                            return True
                    stack.append(child)
            elif isinstance(current, list):
                stack.extend(current)
    return False


class NervesShadowWorker:
    def __init__(
        self,
        *,
        manifest_dir: Path = DEFAULT_MANIFEST_DIR,
        runs_dir: Path = DEFAULT_RUNS_DIR,
        workspace: Path = ROOT,
        skills_root: Path = SKILLS_ROOT,
        mission_schema: Path = MISSION_SCHEMA,
        evidence_schema: Path = EVIDENCE_SCHEMA,
        codex_binary: Path | None = None,
        codex_home: Path | None = None,
        subprocess_run: SubprocessRun = subprocess.run,
        execution_enabled: bool = False,
    ) -> None:
        self.manifest_dir = manifest_dir
        self.runs_dir = runs_dir
        self.workspace = workspace
        self.skills_root = skills_root
        self.mission_schema = mission_schema
        self.evidence_schema = evidence_schema
        discovered = shutil.which("codex") if codex_binary is None else str(codex_binary)
        if not discovered:
            raise WorkerAdmissionError("codex_binary_missing")
        self.codex_binary = Path(discovered).resolve(strict=True)
        self.codex_home = (
            Path.home() / ".codex" if codex_home is None else codex_home
        ).resolve(strict=True)
        self.subprocess_run = subprocess_run
        self.execution_enabled = execution_enabled

    def _append_state(
        self,
        journal: ShadowLedger,
        *,
        mission: Mapping[str, Any],
        state: str,
        previous: str | None,
        detail: Mapping[str, Any],
    ) -> None:
        expected = STATE_TRANSITIONS.get(previous)
        allowed = (
            state == expected
            if isinstance(expected, str)
            else state in expected
            if expected is not None
            else False
        )
        if not allowed:
            raise WorkerExecutionError(f"invalid_state_transition:{previous}->{state}")
        journal.append(
            {
                "schema": "seal.nerves.shadow-worker-state.v1",
                "mission_id": mission["mission_id"],
                "idempotency_key": mission["idempotency_key"],
                "state": state,
                "at": _utc_now(),
                "detail": dict(detail),
            }
        )
        os.chmod(journal.path, 0o600)

    def _existing_result(
        self,
        mission: Mapping[str, Any],
        run_dir: Path,
    ) -> WorkerRunResult:
        summary_path = run_dir / "summary.json"
        if not summary_path.exists():
            return WorkerRunResult(
                str(mission["mission_id"]),
                str(mission["idempotency_key"]),
                "failed",
                True,
                run_dir,
                None,
                None,
                "existing_claim_without_terminal_summary",
            )
        summary = _read_json_bytes(
            _secure_read(summary_path, max_bytes=MAX_EVIDENCE_BYTES)
        )
        if (
            summary.get("mission_id") != mission["mission_id"]
            or summary.get("idempotency_key") != mission["idempotency_key"]
        ):
            raise WorkerAdmissionError("existing_summary_identity_mismatch")
        evidence_path = run_dir / "evidence.json"
        return WorkerRunResult(
            str(mission["mission_id"]),
            str(mission["idempotency_key"]),
            str(summary.get("status") or "failed"),
            True,
            run_dir,
            evidence_path if evidence_path.exists() else None,
            summary_path,
            str(summary.get("reason")) if summary.get("reason") else None,
        )

    def run(self, manifest_path: Path) -> WorkerRunResult:
        mission, manifest_raw, skill_path = _validate_manifest(
            manifest_path,
            manifest_dir=self.manifest_dir,
            workspace=self.workspace,
            skills_root=self.skills_root,
            mission_schema=self.mission_schema,
        )
        if (
            not self.execution_enabled
            or self.subprocess_run is subprocess.run
        ):
            raise WorkerAdmissionError("live_runner_security_hold")
        _secure_directory(self.runs_dir, create=True)
        claims_dir = self.runs_dir / "claims"
        _secure_directory(claims_dir, create=True)
        mission_id = str(mission["mission_id"])
        idempotency_key = str(mission["idempotency_key"])
        run_dir = self.runs_dir / mission_id
        run_dir.mkdir(mode=0o700, exist_ok=True)
        _secure_directory(run_dir, create=False)

        claim_name = _sha256(idempotency_key.encode("utf-8")) + ".json"
        global_claim = claims_dir / claim_name
        claim = {
            "schema": "seal.nerves.shadow-worker-claim.v1",
            "mission_id": mission_id,
            "idempotency_key": idempotency_key,
            "manifest_sha256": _sha256(manifest_raw),
            "claimed_at": _utc_now(),
            "attempt": 1,
        }
        try:
            _secure_write_exclusive(
                global_claim, _canonical_bytes(claim) + b"\n"
            )
        except FileExistsError:
            existing = _read_json_bytes(
                _secure_read(global_claim, max_bytes=MAX_MANIFEST_BYTES)
            )
            if (
                existing.get("mission_id") != mission_id
                or existing.get("idempotency_key") != idempotency_key
                or existing.get("manifest_sha256") != claim["manifest_sha256"]
            ):
                raise WorkerAdmissionError("idempotency_claim_conflict")
            return self._existing_result(mission, run_dir)

        local_claim = run_dir / "claim.json"
        _secure_write_exclusive(local_claim, _canonical_bytes(claim) + b"\n")
        journal_path = run_dir / "states.jsonl"
        _secure_create_empty(journal_path)
        journal = ShadowLedger(journal_path)
        self._append_state(
            journal,
            mission=mission,
            state="admitted",
            previous=None,
            detail={
                "manifest_sha256": claim["manifest_sha256"],
                "skill_sha256": mission["skills"][0]["sha256"],
            },
        )

        response_path = run_dir / "response.tmp.json"
        _secure_create_empty(response_path)
        command = _build_command(
            codex_binary=self.codex_binary,
            workspace=self.workspace.resolve(strict=True),
            evidence_schema=self.evidence_schema.resolve(strict=True),
            response_path=response_path,
        )
        prompt = _build_prompt(
            mission, manifest_path=manifest_path.resolve(), skill_path=skill_path
        )
        wall_budget = int(mission["budgets"]["wall_seconds"])
        token_budget = int(mission["budgets"]["token_budget"])
        event_budget = min(
            HARD_MAX_EVENT_BYTES,
            max(MIN_EVENT_BUDGET_BYTES, token_budget * 32),
        )
        self._append_state(
            journal,
            mission=mission,
            state="running",
            previous="admitted",
            detail={
                "argv": command,
                "wall_budget_seconds": wall_budget,
                "token_budget": token_budget,
                "event_budget_bytes": event_budget,
                "environment_keys": sorted(_minimal_worker_env(self.codex_home)),
            },
        )

        stdout = b""
        stderr = b""
        returncode: int | None = None
        failure: str | None = None
        try:
            completed = self.subprocess_run(
                command,
                cwd=self.workspace.resolve(strict=True),
                input=prompt,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=wall_budget,
                check=False,
                env=_minimal_worker_env(self.codex_home),
                shell=False,
            )
            stdout = completed.stdout or b""
            stderr = completed.stderr or b""
            returncode = int(completed.returncode)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
            failure = "wall_budget_exhausted"
        except OSError as exc:
            failure = f"codex_spawn_failed:{type(exc).__name__}"

        _secure_write_exclusive(run_dir / "codex-events.jsonl", stdout)
        _secure_write_exclusive(run_dir / "codex-stderr.log", stderr)
        if len(stdout) > event_budget or len(stderr) > event_budget:
            failure = "output_budget_exhausted"
        usage = _token_usage(stdout)
        if failure is None and _events_indicate_forbidden_activity(stdout):
            failure = "forbidden_codex_activity_observed"
        if failure is None and usage is None:
            failure = "token_usage_missing"
        elif failure is None and usage is not None and usage > token_budget:
            failure = "token_budget_exhausted"
        if failure is None and returncode != 0:
            failure = f"codex_exit_{returncode}"

        evidence: dict[str, Any] | None = None
        if failure is None:
            try:
                response_raw = _secure_read(
                    response_path, max_bytes=MAX_EVIDENCE_BYTES
                )
                evidence = _read_json_bytes(response_raw)
                errors = _schema_errors(evidence, self.evidence_schema)
                if errors:
                    failure = "evidence_schema_invalid:" + ";".join(errors[:8])
                elif evidence.get("mission_id") != mission_id:
                    failure = "evidence_mission_id_mismatch"
                elif evidence.get("read_only") is not True:
                    failure = "evidence_not_read_only"
            except WorkerAdmissionError as exc:
                failure = f"evidence_unreadable:{exc}"

        evidence_path: Path | None = None
        if evidence is not None and failure is None:
            evidence_path = run_dir / "evidence.json"
            _secure_write_exclusive(
                evidence_path, _canonical_bytes(evidence) + b"\n"
            )
        try:
            response_path.unlink()
        except FileNotFoundError:
            pass

        status = "failed" if failure else "succeeded"
        self._append_state(
            journal,
            mission=mission,
            state=status,
            previous="running",
            detail={
                "returncode": returncode,
                "reason": failure,
                "stdout_bytes": len(stdout),
                "stderr_bytes": len(stderr),
                "output_tokens": usage,
                "evidence_sha256": (
                    _sha256(_canonical_bytes(evidence))
                    if evidence is not None and failure is None
                    else None
                ),
            },
        )
        verification = journal.verify()
        if not verification.ok:
            raise WorkerExecutionError(
                "state_journal_invalid:" + ";".join(verification.errors)
            )
        summary = {
            "schema": "seal.nerves.shadow-worker-summary.v1",
            "mission_id": mission_id,
            "idempotency_key": idempotency_key,
            "status": status,
            "reason": failure,
            "attempt": 1,
            "returncode": returncode,
            "wall_budget_seconds": wall_budget,
            "token_budget": token_budget,
            "output_tokens": usage,
            "event_budget_bytes": event_budget,
            "stdout_bytes": len(stdout),
            "stderr_bytes": len(stderr),
            "journal_sequence": verification.sequence,
            "journal_head_hash": verification.head_hash,
            "finished_at": _utc_now(),
        }
        summary_path = run_dir / "summary.json"
        _secure_write_exclusive(
            summary_path, _canonical_bytes(summary) + b"\n"
        )
        return WorkerRunResult(
            mission_id,
            idempotency_key,
            status,
            False,
            run_dir,
            evidence_path,
            summary_path,
            failure,
        )
