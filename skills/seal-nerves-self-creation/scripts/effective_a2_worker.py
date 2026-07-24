#!/usr/bin/env python3
"""Worker executed inside the effective SELF_CREATED A2 sandbox."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _denial_probe(protected_pid: int) -> dict[str, object]:
    result: dict[str, object] = {}
    try:
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result["af_inet_denied"] = False
    except OSError as exc:
        result["af_inet_denied"] = True
        result["af_inet_errno"] = exc.errno

    try:
        Path("/home/dadito/IA/proyecto-seal/AGENTS.md").read_text(
            encoding="utf-8"
        )
        result["home_read_denied"] = False
    except OSError as exc:
        result["home_read_denied"] = True
        result["home_read_errno"] = exc.errno

    forbidden = Path(
        "/home/dadito/IA/proyecto-seal/.self-created-forbidden"
    )
    try:
        forbidden.write_text("forbidden\n", encoding="utf-8")
        result["persistent_write_denied"] = False
    except OSError as exc:
        result["persistent_write_denied"] = True
        result["persistent_write_errno"] = exc.errno

    restart = subprocess.run(
        [
            "/usr/bin/systemctl",
            "--user",
            "restart",
            "seal-mcp-server.service",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    result["service_restart_denied"] = restart.returncode != 0
    result["service_restart_rc"] = restart.returncode

    try:
        os.kill(protected_pid, 0)
        result["signal_denied"] = False
    except OSError as exc:
        result["signal_denied"] = True
        result["signal_errno"] = exc.errno

    sensitive_markers = (
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "PASSWD",
        "DSN",
        "DATABASE_URL",
        "PGPASS",
        "SEAL_DB",
    )
    result["credential_env_names"] = sorted(
        key
        for key in os.environ
        if any(marker in key.upper() for marker in sensitive_markers)
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-script", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--mission", type=Path, required=True)
    parser.add_argument("--protected-pid", type=int, required=True)
    args = parser.parse_args()

    skill_run = subprocess.run(
        [
            "/usr/bin/python3",
            str(args.skill_script),
            "--input",
            str(args.source),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            "HOME": "/run/nerves-home",
            "LANG": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": "/tmp",
        },
    )
    skill_result: dict[str, object] | None = None
    if skill_run.returncode == 0:
        try:
            skill_result = json.loads(skill_run.stdout)
        except json.JSONDecodeError:
            skill_result = None

    boundary = _denial_probe(args.protected_pid)
    required_denials = (
        "af_inet_denied",
        "home_read_denied",
        "persistent_write_denied",
        "service_restart_denied",
        "signal_denied",
    )
    ok = (
        skill_result is not None
        and skill_result.get("ok") is True
        and skill_result.get("authority") == "A2_READ_ONLY"
        and skill_result.get("allowed_tools") == []
        and skill_result.get("network") == "none"
        and skill_result.get("mutations") == 0
        and all(boundary.get(key) is True for key in required_denials)
        and boundary.get("credential_env_names") == []
    )
    output = {
        "schema": "seal.nerves.self_created_effect_worker.v1",
        "ok": ok,
        "skill_exit_code": skill_run.returncode,
        "skill_result": skill_result,
        "skill_script_sha256": _sha256_file(args.skill_script),
        "source_sha256": _sha256_file(args.source),
        "candidate_file_sha256": _sha256_file(args.candidate),
        "mission_file_sha256": _sha256_file(args.mission),
        "boundary": boundary,
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
