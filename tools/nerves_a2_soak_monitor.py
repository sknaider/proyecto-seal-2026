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
SOAK_SCHEMA = "seal.nerves.a2-soak.v2"
CANARY_SCHEMA = "seal.nerves.a2-soak-canary.v2"
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
DELIVERY_STATES = {
    agent: (
        ROOT
        / f"research/flywire_results/nerves_orchestrator_inbox/{agent}.state.json"
    )
    for agent in REQUIRED_A2_ROUTES
}
RELEASE_FILES = (
    ".claude/agents/nerves-jarvis-reasoner.md",
    "memory/seal_nerves.py",
    "memory/nerves_maintenance_jarvis.py",
    "memory/nerves_agent_mission_core.py",
    "memory/nerves_ollama_runtime_adapter.py",
    "memory/nerves_global_catalog.py",
    "memory/nerves_global_catalog_v1.json",
    "memory/nerves_mission_handoff.py",
    "memory/nerves_self_created.py",
    "memory/nerves_local_sidecar.py",
    "memory/nerves_mission_shadow.py",
    "memory/nerves_integrity_evidence_bundle.py",
    "memory/nerves_native_agent_prompt_hook.py",
    "memory/nerves_native_agent_receipt.py",
    "memory/nerves_read_only_action_guard.py",
    "skills/seal-responsive-delegation/PROMPT.md",
    "docs/schemas/nerves_agent_reasoning_v1.schema.json",
    "docs/schemas/nerves_mission_envelope_v1.schema.json",
    "docs/schemas/nerves_integrity_evidence_v1.schema.json",
    "docs/schemas/nerves_orchestrator_receipt_v1.schema.json",
    "docs/schemas/nerves_orchestrator_receipt_v3.schema.json",
    "skills/seal-nerves-integrity-audit/SKILL.md",
    "skills/seal-nerves-integrity-audit/scripts/collect_integrity_evidence.py",
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
    "tools/nerves_public_ack_canary.py",
    "tools/nerves_ada_ollama_canary.py",
    "tools/nerves_alice_ollama_canary.py",
    "tools/nerves_nexus_ollama_canary.py",
    "tools/nerves_fable_ollama_canary.py",
    "tools/nerves_jarvis_native_canary.py",
    "tools/nerves_jarvis_ollama_canary.py",
    "tools/nerves_a2_canary_record.py",
    "tools/nerves_a2_soak_monitor.py",
)
FINGERPRINT_UNITS = tuple(
    sorted(
        {
            unit
            for row in (*RUNTIME_UNITS, *WORKER_UNITS)
            for unit in row[1:]
        }
        | {"seal-nerves-a2-soak.service", "seal-nerves-a2-soak.timer"}
    )
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


def _cat_unit(unit: str) -> str:
    proc = subprocess.run(
        ["systemctl", "--user", "cat", unit],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise SoakError(f"unit_definition_unreadable:{unit}")
    return proc.stdout


def runtime_fingerprint(
    cat_unit: Callable[[str], str] = _cat_unit,
    show: Callable[[str, str], str] | None = None,
) -> str:
    show_value = show or _show
    digest = hashlib.sha256()
    for unit in FINGERPRINT_UNITS:
        effective = {
            "cat": cat_unit(unit),
            "fragment_path": show_value(unit, "FragmentPath"),
            "drop_in_paths": show_value(unit, "DropInPaths"),
            "environment": show_value(unit, "Environment"),
            "environment_files": show_value(unit, "EnvironmentFiles"),
        }
        raw = _canonical(effective)
        encoded = unit.encode("utf-8")
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
    root: Path = ROOT,
) -> list[dict[str, Any]]:
    if not canary_dir.exists():
        return []
    accepted = []
    for path in sorted(canary_dir.glob("*.json")):
        value = _private_json(path)
        if not isinstance(value.get("recorded_at"), str):
            raise SoakError(f"canary_timestamp_invalid:{path.name}")
        try:
            recorded_at = datetime.fromisoformat(
                str(value["recorded_at"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except ValueError as exc:
            raise SoakError(f"canary_timestamp_invalid:{path.name}") from exc
        # Historical canaries predate this release and may use an older schema.
        # They are not evidence for this soak and must not poison the new window.
        if recorded_at < started_at:
            continue
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
            "mission_id",
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
            "utility_basis",
            "confidence",
            "outcome",
            "human_corrections",
            "harm_avoided",
            "harm_caused",
            "harm_avoided_method",
            "principal_ack_latency_ms",
            "attestation_kind",
            "attestation_path",
            "attestation_sha256",
            "attested_started_at",
            "attested_finished_at",
        }
        if set(value) != exact:
            raise SoakError(f"canary_shape_invalid:{path.name}")
        attestation_path = Path(str(value["attestation_path"]))
        try:
            resolved_attestation = attestation_path.resolve(strict=True)
        except OSError as exc:
            raise SoakError(
                f"canary_attestation_unreadable:{path.name}"
            ) from exc
        if not resolved_attestation.is_relative_to(root.resolve()):
            raise SoakError(f"canary_attestation_outside_workspace:{path.name}")
        attestation = _private_json(resolved_attestation)
        attestation_raw = resolved_attestation.read_bytes()
        if hashlib.sha256(attestation_raw).hexdigest() != value["attestation_sha256"]:
            raise SoakError(f"canary_attestation_digest_mismatch:{path.name}")
        try:
            attested_started = datetime.fromisoformat(
                str(value["attested_started_at"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            attested_finished = datetime.fromisoformat(
                str(value["attested_finished_at"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except ValueError as exc:
            raise SoakError(
                f"canary_attestation_timestamp_invalid:{path.name}"
            ) from exc
        runtime = attestation.get("runtime_attestation")
        local_attestation = (
            value["attestation_kind"] == "local_ollama_receipt"
            and value["agent"] != "JARVIS"
            and attestation.get("schema") == "seal.nerves.orchestrator-receipt.v3"
            and attestation.get("worker_kind") == "local_ollama_subagent"
            and isinstance(runtime, dict)
            and runtime.get("isolation") == "no_tool_api"
            and runtime.get("tool_events") == []
            and runtime.get("endpoint") == "http://127.0.0.1:11434/api/generate"
        )
        jarvis_portable_attestation = (
            value["attestation_kind"] == "jarvis_portable_ollama_receipt"
            and value["agent"] == "JARVIS"
            and attestation.get("schema") == "seal.nerves.orchestrator-receipt.v3"
            and attestation.get("worker_kind") == "local_ollama_subagent"
            and attestation.get("verifier", {}).get("verdict") == "accepted"
            and isinstance(runtime, dict)
            and runtime.get("platform") == "ollama_generate_json"
            and runtime.get("isolation") == "no_tool_api"
            and runtime.get("tool_events") == []
            and runtime.get("endpoint") == "http://127.0.0.1:11434/api/generate"
        )
        jarvis_attestation = (
            value["attestation_kind"] == "jarvis_native_receipt"
            and value["agent"] == "JARVIS"
            and attestation.get("schema") == "seal.nerves.orchestrator-receipt.v1"
            and attestation.get("worker_kind") == "native_subagent"
            and attestation.get("tools_used") == ["SendMessage"]
            and attestation.get("verifier_verdict", {}).get("verdict")
            == "accepted"
            and isinstance(runtime, dict)
            and runtime.get("platform") == "claude_code_agent_tool"
            and runtime.get("profile") == "nerves-jarvis-reasoner"
            and runtime.get("tools_configured") == ["SendMessage"]
            and runtime.get("tool_events") == ["SendMessage:main"]
            and runtime.get("skills_configured") == []
            and runtime.get("mcp_servers_configured") == []
            and runtime.get("max_turns") == 1
        )
        ack_attestation = (
            value["attestation_kind"] == "principal_ack_evidence"
            and value["kind"] == "PRINCIPAL_ACK_CANARY"
            and value["agent"] == "ADA"
            and value["mission_id"] is None
            and attestation.get("schema")
            == "seal.nerves.principal-ack-evidence.v1"
            and attestation.get("channel") == "web_chat"
            and attestation.get("in_reply_to")
            == attestation.get("request_legacy_id")
            and attestation.get("request_created_at")
            == value["attested_started_at"]
            and attestation.get("ack_created_at")
            == value["attested_finished_at"]
        )
        route_attestation = value["kind"] == "A2_ROUTE_CANARY"
        if (
            value["schema"] != CANARY_SCHEMA
            or value["release_fingerprint"] != fingerprint
            or value["ok"] is not True
            or value["risk_class"] != "A2_READ_ONLY"
            or value["mutations"] != 0
            or not isinstance(value["evidence_sha256"], str)
            or len(value["evidence_sha256"]) != 64
            or value["evidence_sha256"] != value["attestation_sha256"]
            or (
                route_attestation
                and (
                    attestation.get("mission_id") != value["mission_id"]
                    or attestation.get("status") != "completed"
                    or attestation.get("started_at")
                    != value["attested_started_at"]
                    or attestation.get("finished_at")
                    != value["attested_finished_at"]
                )
            )
            or attested_started < started_at
            or attested_finished < attested_started
            or recorded_at < attested_finished
            or not (
                local_attestation
                or jarvis_attestation
                or jarvis_portable_attestation
                or ack_attestation
            )
            or (
                route_attestation
                and value["utility_basis"] != "controlled_route_completion"
            )
            or (
                value["kind"] == "PRINCIPAL_ACK_CANARY"
                and value["utility_basis"] != "measured_principal_ack_latency"
            )
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
                    "harm_caused",
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
        if value["kind"] not in {
            "A2_ROUTE_CANARY",
            "PRINCIPAL_ACK_CANARY",
        }:
            raise SoakError(f"canary_kind_invalid:{path.name}")
        if value["kind"] == "PRINCIPAL_ACK_CANARY" and (
            value["agent"] != "ADA"
            or value["missions_created"] != 0
            or value["worker_successes"] != 0
            or value["estimated_cost_units"] != 0
            or value["principal_ack_latency_ms"] is None
            or value["harm_avoided_method"] != "principal_ack_under_2s"
        ):
            raise SoakError(f"ack_canary_semantics_invalid:{path.name}")
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
                        "attestation_path",
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
            "harm_caused",
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
            "basis": item["utility_basis"],
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
        "net_harm_avoided": sums["harm_avoided"] - sums["harm_caused"],
        "routes_observed": sorted(
            {str(item["agent"]) for item in canaries}
        ),
    }


def _terminal_worker_failures(
    started_at: datetime,
    *,
    delivery_states: dict[str, Path] | None = None,
) -> list[str]:
    failures: list[str] = []
    states = DELIVERY_STATES if delivery_states is None else delivery_states
    for agent, path in sorted(states.items()):
        if not path.exists():
            continue
        state = _private_json(path)
        deliveries = state.get("deliveries")
        if not isinstance(deliveries, dict):
            raise SoakError(f"delivery_state_invalid:{agent}")
        for mission_id, record in deliveries.items():
            if not isinstance(record, dict):
                raise SoakError(f"delivery_record_invalid:{agent}:{mission_id}")
            created_raw = record.get("created_at")
            if not isinstance(created_raw, str):
                continue
            try:
                created_at = datetime.fromisoformat(
                    created_raw.replace("Z", "+00:00")
                ).astimezone(timezone.utc)
            except ValueError as exc:
                raise SoakError(
                    f"delivery_created_at_invalid:{agent}:{mission_id}"
                ) from exc
            if created_at >= started_at and record.get("status") == "failed":
                failures.append(f"{agent}:{mission_id}")
    return failures


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
    cat_unit: Callable[[str], str] = _cat_unit,
    delivery_states: dict[str, Path] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not release_id.strip():
        raise SoakError("release_id_required")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    fingerprint = release_fingerprint(root)
    effective_runtime_fingerprint = runtime_fingerprint(cat_unit, show)
    if state_path.exists():
        state = _private_json(state_path)
        if (
            state.get("schema") != SOAK_SCHEMA
            or state.get("release_id") != release_id
            or state.get("release_fingerprint") != fingerprint
            or state.get("runtime_fingerprint") != effective_runtime_fingerprint
        ):
            state["status"] = "FAILED"
            state["failure_count"] = int(state.get("failure_count", 0)) + 1
            if state.get("first_failure") is None:
                state["first_failure"] = {
                    "observed_at": current.isoformat(),
                    "failures": ["release_or_runtime_changed_start_new_soak"],
                }
            _write_private(state_path, state)
            raise SoakError("release_or_runtime_changed_start_new_soak")
        started_at = datetime.fromisoformat(
            str(state["started_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    else:
        started_at = current
        state = {
            "schema": SOAK_SCHEMA,
            "release_id": release_id,
            "release_fingerprint": fingerprint,
            "runtime_fingerprint": effective_runtime_fingerprint,
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
    canaries = _canaries(
        started_at,
        fingerprint,
        canary_dir=canary_dir,
        root=root,
    )
    metrics = _metrics(canaries)
    observed_delivery_states = (
        DELIVERY_STATES
        if delivery_states is None and root.resolve() == ROOT.resolve()
        else (delivery_states or {})
    )
    terminal_worker_failures = _terminal_worker_failures(
        started_at,
        delivery_states=observed_delivery_states,
    )
    failures = [
        f"unit:{row['name']}" for row in rows if not row["ok"]
    ]
    failures.extend(
        f"terminal_worker_failure:{value}"
        for value in terminal_worker_failures
    )
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
        if metrics["worker_failures"] != 0 or metrics["worker_success_rate"] != 1.0:
            failures.append("worker_success_rate_below_one")
        if metrics["false_wakes"] != 0:
            failures.append("false_wakes_nonzero")
        if metrics["human_corrections"] != 0:
            failures.append("human_corrections_nonzero")
        if metrics["harm_caused"] != 0:
            failures.append("harm_caused_nonzero")
        if (
            metrics["confidence_brier_score"] is None
            or metrics["confidence_brier_score"] > 0.05
        ):
            failures.append("confidence_brier_exceeded")
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
        "terminal_worker_failures": terminal_worker_failures,
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
