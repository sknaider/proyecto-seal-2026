#!/usr/bin/env python3
"""Validate a SOUL true-green evidence manifest without executing its commands."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA = "soul-true-green-evidence-v1"
STATUSES = {"TRUE_GREEN", "CANDIDATE_GREEN", "YELLOW", "RED", "INDETERMINATE"}
GATE_STATUSES = {"PASS", "FAIL", "UNKNOWN", "N_A"}
REQUIRED_GATES = {
    "claim", "version", "command", "discovery", "quality", "red_green",
    "effect", "negative", "integration", "daemon", "reboot_safety",
    "db_identity", "rls_grants", "cleanup", "regression", "independence",
    "soak", "security", "durability", "closure",
}
HIGH_RISK_REQUIRED_PASS = {
    "claim", "version", "command", "discovery", "quality", "red_green",
    "effect", "negative", "integration", "regression", "independence",
    "security", "durability", "closure",
}
DAEMON_REQUIRED_PASS = {"daemon", "reboot_safety"}
DATABASE_REQUIRED_PASS = {"db_identity"}
RLS_REQUIRED_PASS = {"rls_grants"}
GENERIC_NA_RE = re.compile(r"^(?:not applicable|n/?a|none)(?:\s+(?:to|for)\s+.*)?[.!]?$", re.I)
DAEMON_RE = re.compile(r"(?:daemon|service|systemd|timer|unit(?:\s|$)|\.service|\.timer)", re.I)
DATABASE_RE = re.compile(r"(?:postgres|database|\bdb\b|schema|sql|role)", re.I)
RLS_RE = re.compile(r"(?:\brls\b|row.level|policy|tenant)", re.I)
SECRET_KEY_RE = re.compile(r"(?:password|passwd|secret|token|credential|authorization|api[_-]?key)", re.I)
SECRET_VALUE_RE = re.compile(r"(?:bearer\s+[A-Za-z0-9._~-]{8,}|postgres(?:ql)?://[^\s:@]+:[^\s@]+@)", re.I)


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    location: str
    message: str


def parse_time(value: Any, location: str, issues: list[Issue]) -> datetime | None:
    if not isinstance(value, str):
        issues.append(Issue("ERROR", "timestamp-missing", location, "ISO-8601 timestamp required"))
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        issues.append(Issue("ERROR", "timestamp-invalid", location, f"invalid ISO-8601 timestamp: {value!r}"))
        return None


def inspect_secrets(value: Any, location: str, issues: list[Issue]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if SECRET_KEY_RE.search(str(key)) and key not in {"token_namespace_match", "credential_present"}:
                issues.append(Issue("ERROR", "secret-field", child_location, "secret-bearing fields are forbidden"))
            inspect_secrets(child, child_location, issues)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            inspect_secrets(child, f"{location}[{index}]", issues)
    elif isinstance(value, str) and SECRET_VALUE_RE.search(value):
        issues.append(Issue("ERROR", "secret-value", location, "credential-like value is forbidden"))


def require_text(data: dict[str, Any], key: str, issues: list[Issue]) -> None:
    if not isinstance(data.get(key), str) or not data[key].strip():
        issues.append(Issue("ERROR", "required-field", key, "non-empty string required"))


def principal(value: Any) -> str:
    """Normalize an evidence principal; aliases differing only in case/space are equal."""
    return value.strip().casefold() if isinstance(value, str) else ""


def validate(data: Any, manifest_path: Path) -> dict[str, Any]:
    issues: list[Issue] = []
    if not isinstance(data, dict):
        return {"valid": False, "classification": "INDETERMINATE", "issues": [asdict(Issue("ERROR", "not-object", "$", "manifest must be an object"))]}

    inspect_secrets(data, "$", issues)
    if data.get("schema") != SCHEMA:
        issues.append(Issue("ERROR", "schema", "schema", f"expected {SCHEMA!r}"))
    for key in ("claim_id", "claim", "revision", "worktree_state", "builder", "last_change_at", "started_at", "finished_at", "status"):
        require_text(data, key, issues)
    if not isinstance(data.get("scope"), list) or not data["scope"] or not all(isinstance(item, str) and item for item in data["scope"]):
        issues.append(Issue("ERROR", "scope", "scope", "non-empty string array required"))

    claimed = data.get("status")
    if claimed not in STATUSES:
        issues.append(Issue("ERROR", "status", "status", f"expected one of {sorted(STATUSES)}"))
    risk = data.get("risk")
    if risk not in {"low", "medium", "high", "critical"}:
        issues.append(Issue("ERROR", "risk", "risk", "expected low, medium, high, or critical"))

    last_change = parse_time(data.get("last_change_at"), "last_change_at", issues)
    started = parse_time(data.get("started_at"), "started_at", issues)
    finished = parse_time(data.get("finished_at"), "finished_at", issues)
    if last_change and started and started < last_change:
        issues.append(Issue("ERROR", "stale-run", "started_at", "run started before final change"))
    if started and finished and finished < started:
        issues.append(Issue("ERROR", "time-order", "finished_at", "finish precedes start"))

    checks = data.get("checks")
    check_ids: set[str] = set()
    check_actors: dict[str, str] = {}
    signatures: dict[str, str] = {}
    observed_failure = False
    if not isinstance(checks, list) or not checks:
        issues.append(Issue("ERROR", "checks", "checks", "at least one check is required"))
        checks = []
    for index, check in enumerate(checks):
        loc = f"checks[{index}]"
        if not isinstance(check, dict):
            issues.append(Issue("ERROR", "check-not-object", loc, "check must be an object"))
            continue
        check_id = check.get("id")
        if not isinstance(check_id, str) or not check_id:
            issues.append(Issue("ERROR", "check-id", f"{loc}.id", "non-empty id required"))
        elif check_id in check_ids:
            issues.append(Issue("ERROR", "duplicate-check-id", f"{loc}.id", check_id))
        else:
            check_ids.add(check_id)
        actor = check.get("actor")
        if not principal(actor):
            issues.append(Issue("ERROR", "check-actor", f"{loc}.actor", "non-empty evidence actor required"))
        elif isinstance(check_id, str) and check_id:
            check_actors[check_id] = principal(actor)
        argv = check.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            issues.append(Issue("ERROR", "argv", f"{loc}.argv", "non-empty argv array required; shell strings are forbidden"))
        if not isinstance(check.get("cwd"), str) or not check["cwd"]:
            issues.append(Issue("ERROR", "cwd", f"{loc}.cwd", "cwd required"))
        c_started = parse_time(check.get("started_at"), f"{loc}.started_at", issues)
        c_finished = parse_time(check.get("finished_at"), f"{loc}.finished_at", issues)
        if c_started and last_change and c_started < last_change:
            issues.append(Issue("ERROR", "stale-check", loc, "check predates final change"))
        if c_started and c_finished and c_finished < c_started:
            issues.append(Issue("ERROR", "check-time-order", loc, "check finish precedes start"))
        if c_finished and finished and c_finished > finished:
            issues.append(Issue("ERROR", "check-after-manifest", loc, "check ends after manifest"))
        exit_code = check.get("exit_code")
        if not isinstance(exit_code, int):
            issues.append(Issue("ERROR", "exit-code", f"{loc}.exit_code", "integer exit code required"))
        elif exit_code != 0:
            observed_failure = True
            issues.append(Issue("FAIL", "nonzero-exit", loc, f"exit_code={exit_code}"))
        for counter in ("passed", "failed", "errors", "skipped", "xfail", "xpass"):
            if counter in check and (not isinstance(check[counter], int) or check[counter] < 0):
                issues.append(Issue("ERROR", "counter", f"{loc}.{counter}", "non-negative integer required"))
        if any(check.get(counter, 0) for counter in ("failed", "errors", "xpass")):
            observed_failure = True
            issues.append(Issue("FAIL", "test-failure", loc, "failed/errors/xpass is nonzero"))
        if check.get("skipped", 0) or check.get("xfail", 0):
            if not check.get("skip_scope_excluded", False):
                issues.append(Issue("WARNING", "unscoped-skip", loc, "skip/xfail blocks TRUE_GREEN unless explicitly out of scope"))

        for sig in check.get("scenario_signatures", []):
            if not isinstance(sig, str) or not sig:
                issues.append(Issue("ERROR", "scenario-signature", loc, "scenario signatures must be non-empty strings"))
                continue
            if sig in signatures and not isinstance(check.get("stress_iterations"), int):
                issues.append(Issue("WARNING", "duplicate-scenario", loc, f"{sig!r} already appears in {signatures[sig]}"))
            signatures.setdefault(sig, check_id or loc)

        artifacts = check.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            issues.append(Issue("ERROR", "artifacts", f"{loc}.artifacts", "at least one raw artifact is required"))
            artifacts = []
        for artifact in artifacts:
            if not isinstance(artifact, str) or not artifact:
                issues.append(Issue("ERROR", "artifact-path", f"{loc}.artifacts", "artifact path must be a string"))
                continue
            path = Path(artifact)
            if not path.is_absolute():
                path = manifest_path.parent / path
            if not path.is_file():
                issues.append(Issue("ERROR", "artifact-missing", f"{loc}.artifacts", artifact))
            elif c_started and datetime.fromtimestamp(path.stat().st_mtime, tz=c_started.tzinfo) < c_started:
                issues.append(Issue("ERROR", "artifact-stale", f"{loc}.artifacts", f"{artifact} predates check"))

    gates = data.get("gates")
    gate_names: set[str] = set()
    open_gate = False
    if not isinstance(gates, list) or not gates:
        issues.append(Issue("ERROR", "gates", "gates", "at least one gate is required"))
        gates = []
    for index, gate in enumerate(gates):
        loc = f"gates[{index}]"
        if not isinstance(gate, dict):
            issues.append(Issue("ERROR", "gate-not-object", loc, "gate must be an object"))
            continue
        name = gate.get("name")
        status = gate.get("status")
        if not isinstance(name, str) or not name:
            issues.append(Issue("ERROR", "gate-name", f"{loc}.name", "gate name required"))
        elif name in gate_names:
            issues.append(Issue("ERROR", "duplicate-gate", f"{loc}.name", name))
        else:
            gate_names.add(name)
        if status not in GATE_STATUSES:
            issues.append(Issue("ERROR", "gate-status", f"{loc}.status", f"expected one of {sorted(GATE_STATUSES)}"))
        elif status in {"FAIL", "UNKNOWN"}:
            open_gate = True
            severity = "FAIL" if status == "FAIL" else "ERROR"
            issues.append(Issue(severity, "open-gate", loc, f"{name}={status}"))
            observed_failure |= status == "FAIL"
        elif status == "N_A":
            reason = gate.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                issues.append(Issue("ERROR", "na-without-reason", loc, "N_A requires a concrete reason"))
            elif GENERIC_NA_RE.fullmatch(reason.strip()):
                issues.append(Issue("ERROR", "na-generic-reason", loc, "N_A reason must identify the excluded surface and why it cannot affect the claim"))
        elif status == "PASS":
            evidence = gate.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                issues.append(Issue("ERROR", "pass-without-evidence", loc, "PASS gate requires evidence IDs"))
            else:
                missing = [item for item in evidence if item not in check_ids]
                if missing:
                    issues.append(Issue("ERROR", "unknown-evidence", loc, f"unknown check IDs: {missing}"))

    builder = principal(data.get("builder"))
    verifier = principal(data.get("verifier"))
    independent = bool(builder and verifier and builder != verifier)
    scope_text = " ".join([str(data.get("claim", "")), *(data.get("scope") or [])])
    required_pass = set()
    if risk in {"high", "critical"} and claimed == "TRUE_GREEN":
        required_pass |= HIGH_RISK_REQUIRED_PASS
    if DAEMON_RE.search(scope_text):
        required_pass |= DAEMON_REQUIRED_PASS
    if DATABASE_RE.search(scope_text):
        required_pass |= DATABASE_REQUIRED_PASS
    if RLS_RE.search(scope_text):
        required_pass |= RLS_REQUIRED_PASS

    gate_by_name = {
        gate.get("name"): gate
        for gate in gates
        if isinstance(gate, dict) and isinstance(gate.get("name"), str)
    }
    for name in sorted(required_pass):
        gate = gate_by_name.get(name)
        if not gate or gate.get("status") != "PASS":
            issues.append(Issue("ERROR", "required-gate-not-pass", f"gates.{name}", f"{name} must PASS for this risk/surface; N_A is not accepted"))
    if claimed == "TRUE_GREEN":
        missing_gates = sorted(REQUIRED_GATES - gate_names)
        if missing_gates:
            issues.append(Issue("ERROR", "mandatory-gates-missing", "gates", f"missing gates: {missing_gates}"))
    if risk in {"high", "critical"} and claimed == "TRUE_GREEN":
        if not independent:
            issues.append(Issue("ERROR", "not-independent", "verifier", "high-risk TRUE_GREEN requires builder != verifier"))
        if "independence" not in gate_names:
            issues.append(Issue("ERROR", "independence-gate-missing", "gates", "high-risk TRUE_GREEN requires independence gate"))
        verifier_checks = {check_id for check_id, actor in check_actors.items() if actor == verifier}
        if not verifier_checks:
            issues.append(Issue("ERROR", "verifier-check-missing", "checks", "high-risk TRUE_GREEN requires a check run by the declared verifier"))
        independence_gate = gate_by_name.get("independence")
        independence_evidence = set(independence_gate.get("evidence", [])) if isinstance(independence_gate, dict) else set()
        if verifier_checks and not (verifier_checks & independence_evidence):
            issues.append(Issue("ERROR", "independence-evidence-missing", "gates.independence", "independence gate must cite a verifier-run check"))

    blocking = any(issue.severity in {"ERROR", "FAIL"} for issue in issues)
    warnings = any(issue.severity == "WARNING" for issue in issues)
    if observed_failure:
        classification = "RED"
    elif blocking:
        classification = "INDETERMINATE"
    elif open_gate or warnings:
        classification = "YELLOW"
    elif risk in {"high", "critical"} and not independent:
        classification = "CANDIDATE_GREEN"
    else:
        classification = "TRUE_GREEN"

    claim_valid = claimed == classification
    if claimed == "TRUE_GREEN" and classification != "TRUE_GREEN":
        issues.append(Issue("FAIL", "false-green", "status", f"claimed TRUE_GREEN but evidence classifies {classification}"))
        claim_valid = False
    return {
        "schema": "soul-true-green-validation-v1",
        "valid": not any(issue.severity in {"ERROR", "FAIL"} for issue in issues) and claim_valid,
        "claimed_status": claimed,
        "classification": classification,
        "counts": {
            "checks": len(checks),
            "gates": len(gates),
            "unique_scenarios": len(signatures),
            "errors": sum(issue.severity == "ERROR" for issue in issues),
            "failures": sum(issue.severity == "FAIL" for issue in issues),
            "warnings": sum(issue.severity == "WARNING" for issue in issues),
        },
        "issues": [asdict(issue) for issue in issues],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result = {"valid": False, "classification": "INDETERMINATE", "issues": [{"severity": "ERROR", "code": "manifest-read", "location": "$", "message": str(exc)}]}
    else:
        result = validate(data, args.manifest.resolve())
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        tmp = args.json_output.with_suffix(args.json_output.suffix + ".tmp")
        tmp.write_text(rendered, encoding="utf-8")
        os.replace(tmp, args.json_output)
    print(rendered, end="")
    return 0 if result.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
