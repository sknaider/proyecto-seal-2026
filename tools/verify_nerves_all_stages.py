#!/usr/bin/env python3
"""Fail-closed, end-to-end acceptance gate for the live NERVES runtime.

The gate proves distinct layers instead of treating a timer heartbeat as useful
autonomy: deterministic invariants, contract/runtime wiring, useful role action,
real threshold firing with DB state restoration, and supervised scheduling.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "memory/nerves_contract_v3.json"
REPORT = ROOT / "research/flywire_results/nerves_full_gate.json"
TEST_FILES = (
    "memory/test_nerves_useful_dispatch.py",
    "memory/test_seal_nerves_alert_sensor.py",
    "memory/test_seal_nerves_task_alert_scope.py",
    "memory/test_seal_nerves_ada_silence.py",
    "memory/test_nerves_mass_acceptance.py",
    "memory/test_autonomous_lifecycle.py",
    "fable/test_fable_nerves_live.py",
    "tests/test_verify_nerves_spec_contract.py",
    "tests/test_verify_nerves_all_stages.py",
    "tests/test_nerves_e2e_hard_identity.py",
)


def _run(
    name: str,
    argv: list[str],
    timeout: int = 240,
    env: dict[str, str] | None = None,
) -> dict:
    started = time.monotonic()
    proc = subprocess.run(
        argv,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
        env=env,
    )
    output = proc.stdout or ""
    return {
        "name": name,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "output_tail": "\n".join(output.splitlines()[-20:]),
    }


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _test_count(output: str) -> int:
    match = re.search(r"(\d+) passed", output)
    return int(match.group(1)) if match else 0


def _global_contract_env(source: dict[str, str]) -> dict[str, str]:
    """Drop the caller identity so the contract can audit every agent row."""
    env = source.copy()
    for name in ("SEAL_DB_DSN", "SEAL_DB_URL", "SEAL_PG_DSN", "SEAL_AGENT"):
        env.pop(name, None)
    return env


def _show(unit: str, prop: str) -> str:
    proc = subprocess.run(
        ["systemctl", "--user", "show", unit, f"--property={prop}", "--value"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _runtime_row(agent: str, service: str, timer: str, artifact: Path) -> dict:
    mode = artifact.stat().st_mode & 0o777 if artifact.exists() else None
    age_s = (
        max(0.0, time.time() - artifact.stat().st_mtime)
        if artifact.exists()
        else None
    )
    row = {
        "agent": agent,
        "service": service,
        "timer": timer,
        "timer_active": _show(timer, "ActiveState") == "active",
        "timer_enabled": _show(timer, "UnitFileState") == "enabled",
        "last_trigger": _show(timer, "LastTriggerUSec"),
        "service_result": _show(service, "Result"),
        "service_exec_status": _show(service, "ExecMainStatus"),
        "artifact": str(artifact.relative_to(ROOT)),
        "artifact_exists": artifact.is_file(),
        "artifact_mode": oct(mode) if mode is not None else None,
        "artifact_age_s": round(age_s, 1) if age_s is not None else None,
    }
    row["ok"] = all(
        (
            row["timer_active"],
            row["timer_enabled"],
            bool(row["last_trigger"]),
            row["service_result"] == "success",
            row["service_exec_status"] == "0",
            row["artifact_exists"],
            mode == 0o600,
            age_s is not None and age_s <= 7200,
        )
    )
    return row


def runtime_report(contract: dict) -> dict:
    shared = contract["shared_runtime"]
    rows = []
    for agent in shared["agents"]:
        units = shared["services"][agent]
        artifact = ROOT / shared["maintenance"][agent]["artifact"]
        rows.append(_runtime_row(agent, units["service"], units["timer"], artifact))
    fable = contract["fable_sidecar"]
    rows.append(
        _runtime_row(
            "FABLE",
            fable["service"],
            fable["timer"],
            ROOT / fable["artifact"],
        )
    )
    return {"ok": all(row["ok"] for row in rows), "agents": rows}


def _write_report(report: dict) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPORT.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(REPORT)
    os.chmod(REPORT, 0o600)


def main() -> int:
    dsn = os.environ.get("SEAL_DB_DSN", "")
    identity = urlsplit(dsn).username if dsn else None
    agent = os.environ.get("SEAL_AGENT", "").strip().upper()
    expected_identity = f"svc_soul_nerves_{agent.lower()}" if agent else None
    if not expected_identity or identity != expected_identity:
        report = {
            "schema": "seal.nerves_full_gate.v1",
            "ts": datetime.now(timezone.utc).isoformat(),
            "pass": False,
            "error": (
                "SEAL_DB_DSN must authenticate directly as the per-agent NERVES role "
                f"(expected={expected_identity or 'SEAL_AGENT missing'})"
            ),
        }
        _write_report(report)
        print(json.dumps(report, indent=2))
        return 2

    stages: list[dict] = []
    tests = _run("deterministic_tests", [sys.executable, "-m", "pytest", "-q", *TEST_FILES])
    tests["passed_tests"] = _test_count(tests["output_tail"])
    tests["ok"] = tests["ok"] and tests["passed_tests"] >= 1000
    stages.append(tests)

    contract_report_path = ROOT / "research/flywire_results/nerves_contract_verification.json"
    contract_report_path.unlink(missing_ok=True)
    contract_stage = _run(
        "contract_and_runtime_spec",
        [
            sys.executable,
            "tools/verify_nerves_spec_contract.py",
            "--json",
            "--output",
            str(contract_report_path),
        ],
        env=_global_contract_env(os.environ),
    )
    contract_evidence = _read_json(contract_report_path) if contract_report_path.exists() else {}
    contract_stage["passed_checks"] = contract_evidence.get("passed", 0)
    contract_stage["total_checks"] = contract_evidence.get("total", 0)
    contract_stage["ok"] = contract_stage["ok"] and bool(contract_evidence.get("pass"))
    stages.append(contract_stage)

    activation_report_path = ROOT / "research/flywire_results/nerves_activation_canary.json"
    activation_report_path.unlink(missing_ok=True)
    activation_stage = _run(
        "useful_actions", [sys.executable, "memory/nerves_activation_canary.py"]
    )
    activation = _read_json(activation_report_path) if activation_report_path.exists() else {}
    activation_stage["agents_passed"] = sum(
        row.get("status") == "pass" for row in activation.get("results", [])
    )
    activation_stage["ok"] = activation_stage["ok"] and bool(activation.get("pass"))
    stages.append(activation_stage)

    e2e_report_path = ROOT / "research/flywire_results/nerves_e2e_canary.json"
    e2e_report_path.unlink(missing_ok=True)
    e2e_env = os.environ.copy()
    e2e_env["SEAL_NERVES_USEFUL"] = "1"
    e2e_env["SEAL_NERVES_TRIGGER_SOURCE"] = "controlled_e2e_canary"
    e2e_stage = _run(
        "threshold_to_effect_and_restore",
        [sys.executable, "memory/nerves_e2e_canary.py"],
        env=e2e_env,
    )
    e2e = _read_json(e2e_report_path) if e2e_report_path.exists() else {}
    e2e_stage["agents_passed"] = sum(
        row.get("status") == "pass" for row in e2e.get("results", [])
    )
    e2e_stage["ok"] = e2e_stage["ok"] and bool(e2e.get("pass"))
    stages.append(e2e_stage)

    contract = _read_json(CONTRACT)
    runtime = runtime_report(contract)
    stages.append({"name": "supervised_automatic_runtime", **runtime})

    report = {
        "schema": "seal.nerves_full_gate.v1",
        "ts": datetime.now(timezone.utc).isoformat(),
        "db_identity": identity,
        "stages": stages,
        "pass": all(stage.get("ok") for stage in stages),
    }
    _write_report(report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
