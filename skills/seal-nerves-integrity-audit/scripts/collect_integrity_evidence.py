#!/usr/bin/env python3
"""Collect bounded, deterministic, read-only JARVIS integrity evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, Sequence


ROOT = Path(__file__).resolve().parents[3]
MAX_TAIL = 1600
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TERM": "dumb",
    "TZ": "UTC",
    "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
    "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{os.getuid()}/bus",
}
SYSTEMCTL = "/usr/bin/systemctl"

# This is deliberately code, not mission input.  A mission cannot add a
# command, service, database, network endpoint, shell fragment, or chat call.
COMMAND_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "dependency_diff",
        (sys.executable, str(ROOT / "tools/dependency_inventory.py"), "--diff"),
    ),
    (
        "dependency_identity",
        (sys.executable, str(ROOT / "tools/dependency_inventory.py"), "--identity"),
    ),
    (
        "dependency_absolute",
        (sys.executable, str(ROOT / "tools/dependency_inventory.py"), "--json"),
    ),
    (
        "failed_user_units",
        (
            SYSTEMCTL,
            "--user",
            "list-units",
            "--state=failed",
            "--no-legend",
            "--plain",
        ),
    ),
    (
        "failed_system_units",
        (
            SYSTEMCTL,
            "list-units",
            "--state=failed",
            "--no-legend",
            "--plain",
        ),
    ),
)


class MissionScopeError(ValueError):
    pass


SubprocessRun = Callable[..., subprocess.CompletedProcess[str]]


def _load_mission(path: Path) -> dict[str, Any]:
    """Compatibility seam backed by the full custody/provenance admission."""

    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from memory.nerves_integrity_evidence_bundle import admit_mission

        return admit_mission(path).mission
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MissionScopeError(str(exc)) from exc


def _minimal_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Return a constant environment; mission and parent cannot influence PATH."""

    del source
    return dict(SAFE_ENV)


def _run(
    argv: Sequence[str],
    timeout: int = 90,
    *,
    subprocess_run: SubprocessRun = subprocess.run,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    completed = subprocess_run(
        list(argv),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        env=_minimal_environment(environment),
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    return {
        "argv": list(argv),
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
    if 2 in {diff_rc, identity_rc} or absolute_rc not in {0, 1}:
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
    if diff_rc == 1 or absolute_rc == 1:
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


def _summary(classification: str, checks: list[dict[str, Any]]) -> str:
    signals: list[str] = []
    for item in checks:
        combined = (item["stdout_tail"] + "\n" + item["stderr_tail"]).lower()
        if item["returncode"] != 0 or (
            item["label"] in {"failed_user_units", "failed_system_units"}
            and any(marker in combined for marker in ("seal-", "soul-"))
        ):
            signals.append(item["label"])
    suffix = ",".join(signals) if signals else "none"
    return (
        f"Deterministic read-only integrity collection: {classification}; "
        f"signal_checks={suffix}."
    )


def collect(
    mission: dict[str, Any],
    *,
    subprocess_run: SubprocessRun = subprocess.run,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    mission_id = mission.get("mission_id")
    if not isinstance(mission_id, str) or not mission_id:
        raise MissionScopeError("mission_id_required")
    checks: list[dict[str, Any]] = []
    for label, argv in COMMAND_SPECS:
        result = _run(
            argv,
            subprocess_run=subprocess_run,
            environment=environment,
        )
        result["label"] = label
        checks.append(result)
    classification = _classify(checks)
    return {
        "schema": "seal.nerves.integrity-evidence.v1",
        "mission_id": mission_id,
        "classification": classification,
        "read_only": True,
        "summary": _summary(classification, checks),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission", type=Path, required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        # Import here so the collector remains a fixed mechanical instrument.
        evidence = collect(_load_mission(args.mission))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # An invalid mission is not evidence and therefore is deliberately not
        # shaped like a successful typed evidence object.
        print(
            json.dumps(
                {
                    "schema": "seal.nerves.integrity-evidence-error.v1",
                    "classification": "abstained_scope_invalid",
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
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
