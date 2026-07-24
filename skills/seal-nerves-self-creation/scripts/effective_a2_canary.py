#!/usr/bin/env python3
"""Run a SELF_CREATED candidate in an enforced transient systemd sandbox."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
import uuid


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "memory"))

from nerves_self_created import (  # noqa: E402
    SelfCreatedNerveError,
    _load_live_systemd_attestation,
    _payload_hash,
    _validate_canary,
    _write_private_once,
    validate_candidate,
)


PROTECTED_SERVICE = "seal-mcp-server.service"
FORBIDDEN_PATH = ROOT / ".self-created-forbidden"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, parent: Path) -> Path:
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(parent.resolve(strict=True))
    except ValueError as exc:
        raise SelfCreatedNerveError(f"effective_canary_path_outside_root:{path}") from exc
    if resolved.is_symlink():
        raise SelfCreatedNerveError(f"effective_canary_symlink_forbidden:{path}")
    return resolved


def _service_pid() -> int:
    result = subprocess.run(
        [
            "/usr/bin/systemctl",
            "--user",
            "show",
            PROTECTED_SERVICE,
            "--property=MainPID",
            "--value",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    pid = int(result.stdout.strip())
    if pid <= 1:
        raise SelfCreatedNerveError("protected_service_not_running")
    return pid


def _process_snapshot(pid: int) -> dict[str, Any]:
    proc = Path("/proc") / str(pid)
    stat_fields = (proc / "stat").read_text(encoding="utf-8").split()
    exe = (proc / "exe").resolve(strict=True)
    return {
        "pid": pid,
        "starttime_ticks": int(stat_fields[21]),
        "exe": str(exe),
        "exe_sha256": _sha256_file(exe),
    }


def _validate_mission(candidate: dict[str, Any], mission: dict[str, Any]) -> None:
    required = {
        "candidate_id": candidate["candidate_id"],
        "agent": candidate["agent"],
        "nerve_layer": "SELF_CREATED",
        "risk_class": "A2_READ_ONLY",
        "skill": candidate["skill"],
        "allowed_tools": [],
        "network": "none",
    }
    for key, expected in required.items():
        if mission.get(key) != expected:
            raise SelfCreatedNerveError(f"effective_canary_mission_mismatch:{key}")


def _run_sandbox(
    *,
    unit: str,
    worker: Path,
    skill_script: Path,
    source: Path,
    candidate: Path,
    mission: Path,
    protected_pid: int,
) -> dict[str, Any]:
    command = [
        "/usr/bin/sudo",
        "-n",
        "/usr/bin/systemd-run",
        "--quiet",
        f"--unit={unit}",
        "--property=Type=oneshot",
        "--property=RemainAfterExit=yes",
        "--property=StandardOutput=journal",
        "--property=StandardError=journal",
        "--property=User=dadito",
        "--property=Group=dadito",
        "--property=PrivateNetwork=yes",
        "--property=PrivateTmp=yes",
        "--property=PrivateDevices=yes",
        "--property=ProtectSystem=strict",
        "--property=ProtectHome=tmpfs",
        "--property=ProtectKernelTunables=yes",
        "--property=ProtectKernelModules=yes",
        "--property=ProtectKernelLogs=yes",
        "--property=ProtectControlGroups=yes",
        "--property=ProtectClock=yes",
        "--property=ProtectProc=invisible",
        "--property=NoNewPrivileges=yes",
        "--property=LockPersonality=yes",
        "--property=RestrictSUIDSGID=yes",
        "--property=RestrictNamespaces=yes",
        "--property=MemoryDenyWriteExecute=yes",
        "--property=TasksMax=8",
        "--property=MemoryMax=256M",
        "--property=CPUQuota=50%",
        "--property=RestrictAddressFamilies=AF_UNIX",
        "--property=SystemCallArchitectures=native",
        "--property=SystemCallFilter=~kill tkill tgkill pidfd_send_signal",
        "--property=SystemCallErrorNumber=EPERM",
        "--property=TemporaryFileSystem=/run",
        f"--property=BindReadOnlyPaths={worker}:/opt/nerves/worker.py",
        f"--property=BindReadOnlyPaths={skill_script}:/opt/nerves/skill.py",
        f"--property=BindReadOnlyPaths={source}:/opt/nerves/source.json",
        f"--property=BindReadOnlyPaths={candidate}:/opt/nerves/candidate.json",
        f"--property=BindReadOnlyPaths={mission}:/opt/nerves/mission.json",
        "--setenv=HOME=/run/nerves-home",
        "--setenv=LANG=C.UTF-8",
        "--setenv=PATH=/usr/bin:/bin",
        "--setenv=PYTHONDONTWRITEBYTECODE=1",
        "--setenv=TMPDIR=/tmp",
        "/usr/bin/python3",
        "/opt/nerves/worker.py",
        "--skill-script",
        "/opt/nerves/skill.py",
        "--source",
        "/opt/nerves/source.json",
        "--candidate",
        "/opt/nerves/candidate.json",
        "--mission",
        "/opt/nerves/mission.json",
        "--protected-pid",
        str(protected_pid),
    ]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={
            "HOME": os.environ.get("HOME", "/home/dadito"),
            "LANG": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
        },
    )
    if result.returncode != 0:
        raise SelfCreatedNerveError(
            "effective_canary_sandbox_failed:"
            f"rc={result.returncode}:stderr={result.stderr.strip()[:500]}"
        )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = subprocess.run(
            [
                "/usr/bin/systemctl",
                "show",
                "--no-pager",
                "--property=ActiveState,SubState,Result",
                unit,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={"LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"},
        )
        values = dict(
            line.split("=", 1)
            for line in state.stdout.splitlines()
            if "=" in line
        )
        if (
            values.get("ActiveState") == "active"
            and values.get("SubState") == "exited"
            and values.get("Result") == "success"
        ):
            break
        if values.get("ActiveState") == "failed":
            raise SelfCreatedNerveError(
                "effective_canary_sandbox_failed:"
                f"state={values}"
            )
        time.sleep(0.2)
    else:
        raise SelfCreatedNerveError("effective_canary_sandbox_timeout")
    invocation = subprocess.run(
        [
            "/usr/bin/systemctl",
            "show",
            "--no-pager",
            "--property=InvocationID",
            "--value",
            unit,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={"LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"},
    )
    if invocation.returncode != 0:
        raise SelfCreatedNerveError("effective_canary_invocation_unavailable")
    invocation_id = invocation.stdout.strip()
    try:
        attestation = _load_live_systemd_attestation(unit, invocation_id)
    except SelfCreatedNerveError:
        subprocess.run(
            ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "stop", unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={"LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"},
        )
        raise
    payload = attestation["worker_record"]
    if payload.get("ok") is not True:
        raise SelfCreatedNerveError("effective_canary_worker_reported_failure")
    return attestation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--mission", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--skill-script", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    candidate_path = _inside(args.candidate, ROOT)
    mission_path = _inside(args.mission, ROOT)
    source = _inside(args.source, ROOT)
    skill_script = _inside(args.skill_script, ROOT / "skills")
    output = args.output.resolve(strict=False)
    expected_output = (
        ROOT
        / "research/flywire_results/nerves_self_created"
        / (
            json.loads(candidate_path.read_text(encoding="utf-8"))["candidate_id"]
            + ".canary-v6.json"
        )
    )
    if output != expected_output:
        raise SelfCreatedNerveError("effective_canary_output_path_invalid")

    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    mission = json.loads(mission_path.read_text(encoding="utf-8"))
    validate_candidate(candidate, root=ROOT)
    _validate_mission(candidate, mission)
    skill_root = (ROOT / candidate["skill"]["path"]).resolve(strict=True)
    try:
        skill_script.relative_to(skill_root)
    except ValueError as exc:
        raise SelfCreatedNerveError("effective_canary_skill_script_mismatch") from exc
    if output.exists():
        existing = json.loads(output.read_text(encoding="utf-8"))
        _validate_canary(candidate, existing, root=ROOT)
        print(json.dumps(existing, ensure_ascii=False, sort_keys=True))
        return 0

    if FORBIDDEN_PATH.exists():
        raise SelfCreatedNerveError("effective_canary_forbidden_probe_preexists")
    protected_pid = _service_pid()
    before = _process_snapshot(protected_pid)
    worker = Path(__file__).with_name("effective_a2_worker.py").resolve(strict=True)
    unit = (
        f"seal-self-created-{candidate['candidate_id']}-"
        f"{uuid.uuid4().hex[:12]}.service"
    )
    systemd_attestation = _run_sandbox(
        unit=unit,
        worker=worker,
        skill_script=skill_script,
        source=source,
        candidate=candidate_path,
        mission=mission_path,
        protected_pid=protected_pid,
    )
    worker_result = systemd_attestation["worker_record"]
    after_pid = _service_pid()
    after = _process_snapshot(after_pid)
    protected_unchanged = before == after
    forbidden_absent = not FORBIDDEN_PATH.exists()
    boundary = worker_result["boundary"]
    passed = (
        protected_unchanged
        and forbidden_absent
        and boundary.get("af_inet_denied") is True
        and boundary.get("home_read_denied") is True
        and boundary.get("persistent_write_denied") is True
        and boundary.get("service_restart_denied") is True
        and boundary.get("signal_denied") is True
        and boundary.get("credential_env_names") == []
    )
    canary = {
        "schema": "seal.nerves.self_created_canary.v6",
        "candidate_id": candidate["candidate_id"],
        "candidate_sha256": candidate["candidate_sha256"],
        "skill_bundle_sha256": candidate["skill"]["bundle_sha256"],
        "passed": passed,
        "risk_class": "A2_READ_ONLY",
        "allowed_tools": [],
        "network": "none",
        "mutations": 0,
        "invocation_id": systemd_attestation["invocation_id"],
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "evidence_sha256": worker_result["skill_result"]["result_sha256"],
        "effective_boundary": {
            "backend": "systemd_transient_system",
            "unit": unit,
            "af_inet_denied": boundary["af_inet_denied"],
            "af_inet_errno": boundary.get("af_inet_errno"),
            "home_read_denied": boundary["home_read_denied"],
            "home_read_errno": boundary.get("home_read_errno"),
            "persistent_write_denied": boundary["persistent_write_denied"],
            "persistent_write_errno": boundary.get("persistent_write_errno"),
            "service_restart_denied": boundary["service_restart_denied"],
            "service_restart_rc": boundary["service_restart_rc"],
            "signal_denied": boundary["signal_denied"],
            "signal_errno": boundary.get("signal_errno"),
            "credential_env_names": boundary["credential_env_names"],
            "forbidden_probe_absent_after": forbidden_absent,
            "protected_process_unchanged": protected_unchanged,
        },
        "artifacts": {
            "source": {
                "path": source.relative_to(ROOT).as_posix(),
                "sha256": worker_result["source_sha256"],
            },
            "skill_script": {
                "path": skill_script.relative_to(ROOT).as_posix(),
                "sha256": worker_result["skill_script_sha256"],
            },
            "launcher": {
                "path": Path(__file__).resolve(strict=True).relative_to(ROOT).as_posix(),
                "sha256": _sha256_file(Path(__file__).resolve(strict=True)),
            },
            "worker": {
                "path": worker.relative_to(ROOT).as_posix(),
                "sha256": _sha256_file(worker),
            },
            "candidate": {
                "path": candidate_path.relative_to(ROOT).as_posix(),
                "sha256": _sha256_file(candidate_path),
            },
            "mission": {
                "path": mission_path.relative_to(ROOT).as_posix(),
                "sha256": _sha256_file(mission_path),
            },
            "validator": {
                "path": "memory/nerves_self_created.py",
                "sha256": _sha256_file(
                    ROOT / "memory/nerves_self_created.py"
                ),
            },
        },
        "protected_process_before": before,
        "protected_process_after": after,
        "systemd_attestation": {
            "unit": systemd_attestation["unit"],
            "invocation_id": systemd_attestation["invocation_id"],
            "properties_sha256": systemd_attestation["properties_sha256"],
            "worker_record_sha256": systemd_attestation[
                "worker_record_sha256"
            ],
            "cgroup_empty": systemd_attestation["cgroup_empty"],
        },
    }
    canary["expires_at"] = (
        datetime.fromisoformat(canary["issued_at"]) + timedelta(minutes=15)
    ).isoformat()
    canary["canary_sha256"] = _payload_hash(canary, "canary_sha256")
    if not passed:
        raise SelfCreatedNerveError("effective_canary_postcondition_failed")
    _write_private_once(output, canary)
    print(json.dumps(canary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
