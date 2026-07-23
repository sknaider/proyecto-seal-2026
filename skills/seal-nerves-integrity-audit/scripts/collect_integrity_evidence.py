#!/usr/bin/env python3
"""Collect bounded read-only evidence for a JARVIS integrity mission."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
MAX_TAIL = 1600


class MissionScopeError(ValueError):
    pass


def _load_mission(path: Path) -> dict[str, Any]:
    mission = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "agent": "JARVIS",
        "specialty": "architecture_integrity_audit",
        "risk_class": "A2_READ_ONLY",
    }
    for field, expected in required.items():
        if mission.get(field) != expected:
            raise MissionScopeError(f"{field}_must_equal_{expected}")
    if mission.get("scope", {}).get("network") != "none":
        raise MissionScopeError("network_must_be_none")
    if not mission.get("expected_evidence"):
        raise MissionScopeError("expected_evidence_required")
    if not mission.get("termination_conditions"):
        raise MissionScopeError("termination_conditions_required")
    return mission


def _run(argv: list[str], timeout: int = 90) -> dict[str, Any]:
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    return {
        "argv": argv,
        "returncode": completed.returncode,
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        "stdout_tail": stdout[-MAX_TAIL:],
        "stderr_tail": stderr[-MAX_TAIL:],
    }


def _classify(checks: list[dict[str, Any]]) -> str:
    by_label = {item["label"]: item for item in checks}
    diff_rc = by_label["dependency_diff"]["returncode"]
    identity_rc = by_label["dependency_identity"]["returncode"]
    absolute_rc = by_label["dependency_absolute"]["returncode"]
    if 2 in {diff_rc, identity_rc} or absolute_rc != 0:
        return "instrument_unavailable"
    for label in ("failed_user_units", "failed_system_units"):
        item = by_label[label]
        if item["returncode"] != 0:
            return "instrument_unavailable"
        failed_lines = (
            item["stdout_tail"] + "\n" + item["stderr_tail"]
        ).lower().splitlines()
        if any("seal-" in line or "soul-" in line for line in failed_lines):
            return "real_regression"
    if diff_rc == 1:
        return "real_regression"
    if identity_rc == 1:
        combined = (
            by_label["dependency_identity"]["stdout_tail"]
            + by_label["dependency_identity"]["stderr_tail"]
        ).lower()
        if "unobservable" in combined or "no observable" in combined:
            return "owner_unobservable"
        return "real_regression"
    if any(item["returncode"] not in {0, 1} for item in checks):
        return "inconclusive"
    return "healthy"


def collect(mission: dict[str, Any] | None = None) -> dict[str, Any]:
    commands = [
        (
            "dependency_diff",
            [sys.executable, str(ROOT / "tools/dependency_inventory.py"), "--diff"],
        ),
        (
            "dependency_identity",
            [sys.executable, str(ROOT / "tools/dependency_inventory.py"), "--identity"],
        ),
        (
            "dependency_absolute",
            [sys.executable, str(ROOT / "tools/dependency_inventory.py"), "--json"],
        ),
        (
            "failed_user_units",
            ["systemctl", "--user", "list-units", "--state=failed", "--no-legend", "--plain"],
        ),
        (
            "failed_system_units",
            ["systemctl", "list-units", "--state=failed", "--no-legend", "--plain"],
        ),
    ]
    checks: list[dict[str, Any]] = []
    for label, argv in commands:
        result = _run(argv)
        result["label"] = label
        checks.append(result)
    return {
        "schema": "seal.nerves.integrity-evidence.v1",
        "mission_id": (mission or {}).get("mission_id"),
        "classification": _classify(checks),
        "read_only": True,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        mission = _load_mission(args.mission) if args.mission else None
        evidence = collect(mission)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        evidence = {
            "schema": "seal.nerves.integrity-evidence.v1",
            "classification": "abstained_scope_invalid",
            "read_only": True,
            "error": str(exc),
            "checks": [],
        }
        print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
        return 2
    print(
        json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if args.pretty else None,
        )
    )
    return 0 if evidence["classification"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
