#!/usr/bin/env python3
"""Persist a fail-closed 24-hour A2 NERVES soak over the live user runtime."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "research/flywire_results/nerves_a2_soak/state.json"
CANARY_DIR = ROOT / "research/flywire_results/nerves_a2_soak/canaries"
SOAK_SECONDS = 24 * 60 * 60
REQUIRED_A2_ROUTES = {"ADA", "ALICE", "NEXUS", "FABLE", "JARVIS"}
RUNTIME_UNITS = (
    ("JARVIS", "seal-nerves-daemon.service", "seal-nerves-daemon.timer"),
    ("ADA", "seal-ada-nerves.service", "seal-ada-nerves.timer"),
    ("ALICE", "seal-alice-nerves.service", "seal-alice-nerves.timer"),
    ("NEXUS", "seal-nexus-nerves.service", "seal-nexus-nerves.timer"),
    ("FABLE", "fable-nerves.service", "fable-nerves.timer"),
    ("DUM", "seal-dum-nerves.service", "seal-dum-nerves.timer"),
    ("ALICE_ORION", "alice-orion-nerve.service", "alice-orion-nerve.timer"),
)
WORKER_UNITS = (
    ("ADA_WORKER", "seal-ada-nerves-worker.service", "seal-ada-nerves-worker.timer"),
    ("ALICE_WORKER", "seal-alice-nerves-worker.service", "seal-alice-nerves-worker.timer"),
    ("NEXUS_WORKER", "seal-nexus-nerves-worker.service", "seal-nexus-nerves-worker.timer"),
    ("FABLE_WORKER", "seal-fable-nerves-worker.service", "seal-fable-nerves-worker.timer"),
)
RELEASE_FILES = (
    "memory/nerves_agent_mission_core.py",
    "memory/nerves_ollama_runtime_adapter.py",
    "memory/nerves_global_catalog.py",
    "memory/nerves_global_catalog_v1.json",
    "memory/nerves_mission_handoff.py",
    "memory/nerves_self_created.py",
    "memory/nerves_local_sidecar.py",
    "docs/schemas/nerves_agent_reasoning_v1.schema.json",
    "docs/schemas/nerves_orchestrator_receipt_v3.schema.json",
    "skills/seal-nerves-engineering-audit/SKILL.md",
    "skills/seal-nerves-engineering-audit/scripts/collect_engineering_evidence.py",
    "skills/seal-nerves-orion-audit/SKILL.md",
    "skills/seal-nerves-orion-audit/scripts/collect_orion_evidence.py",
    "skills/seal-nerves-security-triage/SKILL.md",
    "skills/seal-nerves-security-triage/scripts/collect_security_evidence.py",
    "skills/seal-nerves-rigor-adjudication/SKILL.md",
    "skills/seal-nerves-rigor-adjudication/scripts/collect_rigor_evidence.py",
    "skills/seal-ada-ack-latency-triage/SKILL.md",
    "skills/seal-ada-ack-latency-triage/scripts/ack_latency_triage.py",
    "tools/nerves_a2_canary_record.py",
    "tools/nerves_ada_ollama_canary.py",
    "tools/nerves_alice_ollama_canary.py",
    "tools/nerves_nexus_ollama_canary.py",
    "tools/nerves_fable_ollama_canary.py",
)


class SoakError(RuntimeError):
    """The release or live runtime cannot satisfy the soak contract."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def release_fingerprint(root: Path = ROOT) -> str:
    digest = hashlib.sha256()
    for relative in RELEASE_FILES:
        path = root / relative
        if not path.is_file():
            raise SoakError(f"release_file_missing:{relative}")
        raw = path.read_bytes()
        encoded = relative.encode()
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _show(unit: str, prop: str) -> str:
    proc = subprocess.run(
        ["systemctl", "--user", "show", unit, f"--property={prop}", "--value"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _unit_row(
    name: str,
    service: str,
    timer: str,
    show: Callable[[str, str], str],
) -> dict[str, Any]:
    row = {
        "name": name,
        "service": service,
        "timer": timer,
        "timer_active": show(timer, "ActiveState") == "active",
        "timer_enabled": show(timer, "UnitFileState") == "enabled",
        "last_trigger": show(timer, "LastTriggerUSec"),
        "service_result": show(service, "Result"),
        "service_exec_status": show(service, "ExecMainStatus"),
    }
    row["ok"] = all(
        (
            row["timer_active"],
            row["timer_enabled"],
            bool(row["last_trigger"]),
            row["service_result"] == "success",
            row["service_exec_status"] == "0",
        )
    )
    return row


def _private_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise SoakError(f"artifact_missing_or_symlink:{path}")
    if path.stat().st_mode & 0o077:
        raise SoakError(f"artifact_not_private:{path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SoakError(f"artifact_not_object:{path}")
    return value


def _canaries(
    started_at: datetime,
    fingerprint: str,
    *,
    canary_dir: Path = CANARY_DIR,
) -> list[dict[str, Any]]:
    if not canary_dir.exists():
        return []
    accepted = []
    for path in sorted(canary_dir.glob("*.json")):
        value = _private_json(path)
        exact = {
            "schema",
            "canary_id",
            "release_fingerprint",
            "recorded_at",
            "kind",
            "ok",
            "risk_class",
            "mutations",
            "evidence_sha256",
            "agent",
            "signals_seen",
            "missions_created",
            "missions_coalesced",
            "duplicate_side_effects",
            "worker_successes",
            "worker_failures",
            "evidence_attempts",
            "evidence_rejections",
            "false_wakes",
            "dead_letters",
            "verified_effects",
            "estimated_cost_units",
            "predicted_utility",
            "actual_utility",
            "confidence",
            "outcome",
            "human_corrections",
            "harm_avoided",
            "harm_avoided_method",
            "principal_ack_latency_ms",
        }
        if set(value) != exact:
            raise SoakError(f"canary_shape_invalid:{path.name}")
        try:
            recorded_at = datetime.fromisoformat(
                str(value["recorded_at"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except ValueError as exc:
            raise SoakError(f"canary_timestamp_invalid:{path.name}") from exc
        if recorded_at < started_at:
            continue
        if (
            value["release_fingerprint"] != fingerprint
            or value["ok"] is not True
            or value["risk_class"] != "A2_READ_ONLY"
            or value["mutations"] != 0
            or not isinstance(value["evidence_sha256"], str)
            or len(value["evidence_sha256"]) != 64
            or value["agent"] not in REQUIRED_A2_ROUTES | {"SELF_CREATED"}
            or any(
                not isinstance(value[field], int) or value[field] < 0
                for field in (
                    "signals_seen",
                    "missions_created",
                    "missions_coalesced",
                    "duplicate_side_effects",
                    "worker_successes",
                    "worker_failures",
                    "evidence_attempts",
                    "evidence_rejections",
                    "false_wakes",
                    "dead_letters",
                    "verified_effects",
                    "estimated_cost_units",
                    "human_corrections",
                    "harm_avoided",
                )
            )
            or not all(
                isinstance(value[field], (int, float))
                and 0.0 <= float(value[field]) <= 1.0
                for field in ("predicted_utility", "actual_utility", "confidence")
            )
            or value["outcome"] not in {0, 1}
            or not isinstance(value["harm_avoided_method"], str)
            or not value["harm_avoided_method"].strip()
            or (
                value["principal_ack_latency_ms"] is not None
                and (
                    not isinstance(value["principal_ack_latency_ms"], int)
                    or value["principal_ack_latency_ms"] < 0
                )
            )
        ):
            raise SoakError(f"canary_boundary_failed:{path.name}")
        accepted.append(
            {
                "canary_id": value["canary_id"],
                "kind": value["kind"],
                "recorded_at": recorded_at.isoformat(),
                "evidence_sha256": value["evidence_sha256"],
                **{
                    field: value[field]
                    for field in exact
                    if field
                    not in {
                        "schema",
                        "canary_id",
                        "release_fingerprint",
                        "recorded_at",
                        "kind",
                        "ok",
                        "risk_class",
                        "mutations",
                        "evidence_sha256",
                    }
                },
            }
        )
    ids = [item["canary_id"] for item in accepted]
    if len(ids) != len(set(ids)):
        raise SoakError("duplicate_canary_id")
    return accepted


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _percentile_95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, (95 * len(ordered) + 99) // 100 - 1)
    return ordered[index]


def _metrics(canaries: list[dict[str, Any]]) -> dict[str, Any]:
    sums = {
        field: sum(int(item[field]) for item in canaries)
        for field in (
            "signals_seen",
            "missions_created",
            "missions_coalesced",
            "duplicate_side_effects",
            "worker_successes",
            "worker_failures",
            "evidence_attempts",
            "evidence_rejections",
            "false_wakes",
            "dead_letters",
            "verified_effects",
            "estimated_cost_units",
            "human_corrections",
            "harm_avoided",
        )
    }
    ack_values = [
        int(item["principal_ack_latency_ms"])
        for item in canaries
        if item["principal_ack_latency_ms"] is not None
    ]
    brier = (
        round(
            sum(
                (float(item["confidence"]) - int(item["outcome"])) ** 2
                for item in canaries
            )
            / len(canaries),
            6,
        )
        if canaries
        else None
    )
    utility_pairs = [
        {
            "agent": item["agent"],
            "predicted": item["predicted_utility"],
            "actual": item["actual_utility"],
        }
        for item in canaries
    ]
    return {
        **sums,
        "mission_actionable_rate": _ratio(
            sums["missions_created"], sums["signals_seen"]
        ),
        "coalescing_ratio": _ratio(
            sums["missions_coalesced"], sums["signals_seen"]
        ),
        "worker_success_rate": _ratio(
            sums["worker_successes"],
            sums["worker_successes"] + sums["worker_failures"],
        ),
        "evidence_rejection_rate": _ratio(
            sums["evidence_rejections"], sums["evidence_attempts"]
        ),
        "false_wake_rate": _ratio(sums["false_wakes"], sums["signals_seen"]),
        "dead_letter_rate": _ratio(sums["dead_letters"], sums["missions_created"]),
        "cost_per_verified_effect": _ratio(
            sums["estimated_cost_units"], sums["verified_effects"]
        ),
        "predicted_vs_actual_utility": utility_pairs,
        "confidence_brier_score": brier,
        "human_correction_rate": _ratio(
            sums["human_corrections"], sums["verified_effects"]
        ),
        "principal_ack_p95_ms": _percentile_95(ack_values),
        "principal_ack_samples": len(ack_values),
        "harm_avoided_methods": sorted(
            {str(item["harm_avoided_method"]) for item in canaries}
        ),
        "routes_observed": sorted(
            {str(item["agent"]) for item in canaries}
        ),
    }


def _write_private(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sample(
    *,
    release_id: str,
    state_path: Path = STATE,
    canary_dir: Path = CANARY_DIR,
    root: Path = ROOT,
    show: Callable[[str, str], str] = _show,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not release_id.strip():
        raise SoakError("release_id_required")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    fingerprint = release_fingerprint(root)
    if state_path.exists():
        state = _private_json(state_path)
        if (
            state.get("release_id") != release_id
            or state.get("release_fingerprint") != fingerprint
        ):
            raise SoakError("release_changed_start_new_soak")
        started_at = datetime.fromisoformat(
            str(state["started_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    else:
        started_at = current
        state = {
            "schema": "seal.nerves.a2-soak.v1",
            "release_id": release_id,
            "release_fingerprint": fingerprint,
            "started_at": started_at.isoformat(),
            "deadline_at": (started_at + timedelta(seconds=SOAK_SECONDS)).isoformat(),
            "sample_count": 0,
            "failure_count": 0,
            "first_failure": None,
            "status": "SOAKING",
        }
    rows = [
        _unit_row(name, service, timer, show)
        for name, service, timer in (*RUNTIME_UNITS, *WORKER_UNITS)
    ]
    canaries = _canaries(started_at, fingerprint, canary_dir=canary_dir)
    metrics = _metrics(canaries)
    failures = [
        f"unit:{row['name']}" for row in rows if not row["ok"]
    ]
    elapsed_s = max(0, int((current - started_at).total_seconds()))
    if elapsed_s >= SOAK_SECONDS and not canaries:
        failures.append("non_vacuous_a2_canary_missing")
    if elapsed_s >= SOAK_SECONDS:
        missing_routes = REQUIRED_A2_ROUTES - set(metrics["routes_observed"])
        if missing_routes:
            failures.append(
                "a2_route_canaries_missing:" + ",".join(sorted(missing_routes))
            )
        if metrics["principal_ack_samples"] < 1:
            failures.append("principal_ack_canary_missing")
        elif int(metrics["principal_ack_p95_ms"]) > 2000:
            failures.append("principal_ack_p95_exceeded")
        if metrics["duplicate_side_effects"] != 0:
            failures.append("duplicate_side_effects_nonzero")
        if metrics["dead_letters"] != 0:
            failures.append("dead_letters_nonzero")
    state["sample_count"] = int(state.get("sample_count", 0)) + 1
    state["failure_count"] = int(state.get("failure_count", 0)) + len(failures)
    if failures and state.get("first_failure") is None:
        state["first_failure"] = {
            "observed_at": current.isoformat(),
            "failures": failures,
        }
    if state["failure_count"]:
        status = "FAILED"
    elif elapsed_s >= SOAK_SECONDS and canaries:
        status = "PASSED"
    else:
        status = "SOAKING"
    state["status"] = status
    state["last_sample"] = {
        "observed_at": current.isoformat(),
        "elapsed_seconds": elapsed_s,
        "remaining_seconds": max(0, SOAK_SECONDS - elapsed_s),
        "units_ok": sum(row["ok"] for row in rows),
        "units_total": len(rows),
        "non_vacuous_canaries": len(canaries),
        "failures": failures,
        "rows": rows,
        "canaries": canaries,
        "metrics": metrics,
    }
    state["state_sha256"] = hashlib.sha256(
        _canonical({key: value for key, value in state.items() if key != "state_sha256"})
    ).hexdigest()
    _write_private(state_path, state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--canary-dir", type=Path, default=CANARY_DIR)
    args = parser.parse_args()
    try:
        state = sample(
            release_id=args.release_id,
            state_path=args.state,
            canary_dir=args.canary_dir,
        )
    except (OSError, ValueError, json.JSONDecodeError, SoakError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "ok": state["status"] != "FAILED",
                "status": state["status"],
                "release_id": state["release_id"],
                "started_at": state["started_at"],
                "deadline_at": state["deadline_at"],
                **state["last_sample"],
            },
            sort_keys=True,
        )
    )
    return 0 if state["status"] != "FAILED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
