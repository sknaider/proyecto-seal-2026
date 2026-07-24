#!/usr/bin/env python3
"""Runtime-neutral custody, claim, and receipt core for SEAL NERVES missions.

JARVIS keeps its closed Claude receipt v1 adapter.  This module is the v2 core
for new agents: route configuration is server-side, evidence is domain-specific,
and the runtime adapter is explicit.  It never launches a model or grants a
repair capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any, Mapping
import urllib.request
import uuid

from jsonschema import Draft202012Validator, FormatChecker

from memory.nerves_integrity_evidence_bundle import skill_bundle_digest
from memory.nerves_mission_handoff import (
    HandoffError,
    _append_live_feed,
    _canonical_bytes,
    _ensure_private_directory,
    _feed_contains_event,
    _json_no_duplicates,
    _load_state,
    _open_lock,
    _secure_create,
    _secure_read,
    _sha256,
    _state_body,
    _write_state_atomic,
)
from memory.nerves_mission_shadow import ShadowMissionLedger, write_shadow_manifest


ROOT = Path(__file__).resolve().parents[1]
MISSION_SCHEMA = ROOT / "docs/schemas/nerves_mission_envelope_v1.schema.json"
EVIDENCE_SCHEMA = ROOT / "docs/schemas/nerves_agent_evidence_v1.schema.json"
REASONING_SCHEMA = ROOT / "docs/schemas/nerves_agent_reasoning_v1.schema.json"
RECEIPT_SCHEMA = ROOT / "docs/schemas/nerves_orchestrator_receipt_v3.schema.json"
MISSION_NAMESPACE = uuid.UUID("bf6a2971-6ae8-489f-b29f-4d079299ef12")
HANDOFF_NAMESPACE = uuid.UUID("a1f51b87-96dc-4a15-a1b5-30cfab905296")
CLAIM_NAMESPACE = uuid.UUID("35158828-dd03-4cfa-80aa-8b5d99d3395b")
DEFAULT_LEDGER = (
    ROOT / "research/flywire_results/nerves_agent_missions_v2.jsonl"
)
DEFAULT_MANIFEST_DIR = (
    ROOT / "research/flywire_results/nerves_agent_mission_manifests"
)
MAX_JSON_BYTES = 4_194_304
MAX_OLLAMA_RESPONSE_BYTES = 16_777_216
ROUTE_REV = "seal-nerves-agent-route-v2"
HANDOFF_SCHEMA = "seal.nerves.orchestrator-handoff.v3"
RECEIPT_SCHEMA_ID = "seal.nerves.orchestrator-receipt.v3"
STATE_TERMINAL = frozenset({"completed", "failed", "abstained"})


class AgentMissionError(RuntimeError):
    """Raised when route, custody, authority, or receipt validation fails."""


@dataclass(frozen=True, slots=True)
class NervesRouteConfig:
    agent: str
    action: str
    specialty: str
    objective: str
    skill_id: str
    skill_dir: Path
    allowed_tools: tuple[str, ...]
    runtime: str
    inbox_dir: Path
    runtime_artifact_dir: Path
    state_path: Path
    live_feed: Path
    live_channel: str
    result_schema: Path = REASONING_SCHEMA
    risk_class: str = "A2_READ_ONLY"
    wall_seconds: int = 120
    token_budget: int = 4000
    claim_lease_seconds: int = 180

    def identity(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "action": self.action,
            "specialty": self.specialty,
            "risk_class": self.risk_class,
            "skill_id": self.skill_id,
            "skill_bundle_sha256": skill_bundle_digest(self.skill_dir),
            "allowed_tools": list(self.allowed_tools),
            "runtime": self.runtime,
            "claim_lease_seconds": self.claim_lease_seconds,
            "route_rev": ROUTE_REV,
        }

    @property
    def route_sha256(self) -> str:
        return _sha256(_canonical_bytes(self.identity()))


ADA_ROUTE = NervesRouteConfig(
    agent="ADA",
    action="engineering_pulse",
    specialty="engineering_integrity_diagnosis",
    objective=(
        "Classify the authenticated ADA engineering finding and propose the "
        "smallest separately governed repair without mutation."
    ),
    skill_id="seal-nerves-engineering-audit",
    skill_dir=ROOT / "skills/seal-nerves-engineering-audit",
    allowed_tools=(),
    runtime="local_ollama_json_no_tools",
    inbox_dir=(
        ROOT / "research/flywire_results/nerves_orchestrator_inbox/ADA"
    ),
    runtime_artifact_dir=(
        ROOT / "research/flywire_results/nerves_ollama_runs/ADA"
    ),
    state_path=(
        ROOT
        / "research/flywire_results/nerves_orchestrator_inbox/ADA.state.json"
    ),
    live_feed=Path("/tmp/seal_events_ADA.log"),
    live_channel="internal:nerves:ada",
)

ALICE_ROUTE = NervesRouteConfig(
    agent="ALICE",
    action="orion_product_pulse",
    specialty="orion_product_reliability",
    objective=(
        "Classify the authenticated ALICE ORION finding and propose the "
        "smallest separately governed recovery without mutation."
    ),
    skill_id="seal-nerves-orion-audit",
    skill_dir=ROOT / "skills/seal-nerves-orion-audit",
    allowed_tools=(),
    runtime="local_ollama_json_no_tools",
    inbox_dir=(
        ROOT / "research/flywire_results/nerves_orchestrator_inbox/ALICE"
    ),
    runtime_artifact_dir=(
        ROOT / "research/flywire_results/nerves_ollama_runs/ALICE"
    ),
    state_path=(
        ROOT
        / "research/flywire_results/nerves_orchestrator_inbox/ALICE.state.json"
    ),
    live_feed=Path("/tmp/seal_events_ALICE.log"),
    live_channel="internal:nerves:alice",
)

NEXUS_ROUTE = NervesRouteConfig(
    agent="NEXUS",
    action="security_pulse",
    specialty="security_control_integrity",
    objective=(
        "Classify the authenticated NEXUS security finding or instrument "
        "failure and propose the smallest separately governed next action "
        "without mutation."
    ),
    skill_id="seal-nerves-security-triage",
    skill_dir=ROOT / "skills/seal-nerves-security-triage",
    allowed_tools=(),
    runtime="local_ollama_json_no_tools",
    inbox_dir=(
        ROOT / "research/flywire_results/nerves_orchestrator_inbox/NEXUS"
    ),
    runtime_artifact_dir=(
        ROOT / "research/flywire_results/nerves_ollama_runs/NEXUS"
    ),
    state_path=(
        ROOT
        / "research/flywire_results/nerves_orchestrator_inbox/NEXUS.state.json"
    ),
    live_feed=Path("/tmp/seal_events_NEXUS.log"),
    live_channel="internal:nerves:nexus",
)

FABLE_ROUTE = NervesRouteConfig(
    agent="FABLE",
    action="rigor_pulse",
    specialty="evidence_integrity_and_false_green_adjudication",
    objective=(
        "Adjudicate one authenticated FABLE rigor finding, distinguish a "
        "real unhealthy instrument from an unverifiable or false-green claim, "
        "and propose the smallest separately governed next action without "
        "mutation."
    ),
    skill_id="seal-nerves-rigor-adjudication",
    skill_dir=ROOT / "skills/seal-nerves-rigor-adjudication",
    allowed_tools=(),
    runtime="local_ollama_json_no_tools",
    inbox_dir=(
        ROOT / "research/flywire_results/nerves_orchestrator_inbox/FABLE"
    ),
    runtime_artifact_dir=(
        ROOT / "research/flywire_results/nerves_ollama_runs/FABLE"
    ),
    state_path=(
        ROOT
        / "research/flywire_results/nerves_orchestrator_inbox/FABLE.state.json"
    ),
    live_feed=Path("/tmp/seal_events_FABLE.log"),
    live_channel="internal:nerves:fable",
)

ROUTES = {
    "ADA": ADA_ROUTE,
    "ALICE": ALICE_ROUTE,
    "NEXUS": NEXUS_ROUTE,
    "FABLE": FABLE_ROUTE,
}


@dataclass(frozen=True, slots=True)
class CompiledMission:
    mission: dict[str, Any]
    manifest_path: Path
    evidence_path: Path
    provenance_path: Path
    created: bool


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    mission_id: str
    idempotency_key: str
    handoff_path: Path
    receipt_path: Path
    handoff_sha256: str
    created: bool
    status: str
    live_notified: bool


@dataclass(frozen=True, slots=True)
class ClaimResult:
    mission_id: str
    claim_id: str
    worker_id: str
    claimed: bool
    status: str
    receipt_path: Path


@dataclass(frozen=True, slots=True)
class HoldResult:
    mission_id: str
    command_id: str
    status: str
    held: bool
    lease_released: bool
    accepted_effects_after_command: int


@dataclass(frozen=True, slots=True)
class CompletionResult:
    mission_id: str
    status: str
    receipt_sha256: str
    accepted: bool


def _validate_schema(
    value: Mapping[str, Any], schema_path: Path, *, label: str
) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        detail = ";".join(
            f"{'/'.join(map(str, error.absolute_path))}:{error.message}"
            for error in errors[:8]
        )
        raise AgentMissionError(f"{label}_schema_invalid:{detail}")


def validate_reasoning_result(
    value: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    label: str,
) -> list[str]:
    """Validate one reasoning result against schema and admitted evidence."""
    _validate_schema(value, REASONING_SCHEMA, label=label)
    allowed_ids = {
        str(check["evidence_id"])
        for check in evidence.get("checks", [])
        if isinstance(check, dict) and check.get("evidence_id")
    }
    cited_ids = [
        str(identifier)
        for hypothesis in value.get("hypotheses", [])
        for identifier in hypothesis.get("evidence_ids", [])
    ]
    if len(cited_ids) != len(set(cited_ids)):
        raise AgentMissionError(f"{label}_duplicate_evidence_id")
    if not set(cited_ids).issubset(allowed_ids):
        raise AgentMissionError(f"{label}_unknown_evidence_id")
    for action in value.get("recommended_actions", []):
        if (
            action["risk_class"]
            in {"A4_SERVICE_CHANGE", "A5_GOVERNED", "A6_DESTRUCTIVE"}
            and action["requires_human_approval"] is not True
        ):
            raise AgentMissionError(f"{label}_governed_action_missing_approval")
    return [
        f"{label}_schema",
        f"{label}_evidence_id_allowlist",
        f"{label}_evidence_id_unique",
        f"{label}_governed_action_approval",
    ]


def _secure_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw, _ = _secure_read(
            path, label=label, max_bytes=MAX_JSON_BYTES, required_mode=0o600
        )
        value = _json_no_duplicates(raw, label=label)
    except HandoffError as exc:
        raise AgentMissionError(str(exc)) from exc
    if raw != _canonical_bytes(value) + b"\n":
        raise AgentMissionError(f"{label}_must_be_canonical")
    return value, raw


def _write_private_json(path: Path, value: Mapping[str, Any]) -> bool:
    raw = _canonical_bytes(dict(value)) + b"\n"
    try:
        return _secure_create(path, raw, mode=0o600)
    except HandoffError as exc:
        raise AgentMissionError(str(exc)) from exc


def _replay_local_ollama_request(
    request_document: Mapping[str, Any],
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Independently replay a deterministic local request for authenticity."""
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=_canonical_bytes(dict(request_document)),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=float(timeout_seconds)
        ) as response:
            raw = response.read(MAX_OLLAMA_RESPONSE_BYTES + 1)
            if len(raw) > MAX_OLLAMA_RESPONSE_BYTES:
                raise AgentMissionError("ollama_replay_response_too_large")
            value = json.loads(raw)
            if int(response.status) != 200 or not isinstance(value, dict):
                raise AgentMissionError("ollama_replay_transport_invalid")
            return value
    except AgentMissionError:
        raise
    except Exception as exc:
        raise AgentMissionError(
            f"ollama_replay_failed:{type(exc).__name__}"
        ) from exc


def _load_engineering_builder():
    script = (
        ADA_ROUTE.skill_dir
        / "scripts/collect_engineering_evidence.py"
    )
    spec = importlib.util.spec_from_file_location(
        "seal_nerves_engineering_evidence", script
    )
    if spec is None or spec.loader is None:
        raise AgentMissionError("engineering_evidence_loader_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_orion_builder(route: NervesRouteConfig = ALICE_ROUTE):
    script = route.skill_dir / "scripts/collect_orion_evidence.py"
    spec = importlib.util.spec_from_file_location(
        "seal_nerves_orion_evidence", script
    )
    if spec is None or spec.loader is None:
        raise AgentMissionError("orion_evidence_loader_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_security_builder(route: NervesRouteConfig = NEXUS_ROUTE):
    script = route.skill_dir / "scripts/collect_security_evidence.py"
    spec = importlib.util.spec_from_file_location(
        "seal_nerves_security_evidence", script
    )
    if spec is None or spec.loader is None:
        raise AgentMissionError("security_evidence_loader_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_rigor_builder(route: NervesRouteConfig = FABLE_ROUTE):
    script = route.skill_dir / "scripts/collect_rigor_evidence.py"
    spec = importlib.util.spec_from_file_location(
        "seal_nerves_rigor_evidence", script
    )
    if spec is None or spec.loader is None:
        raise AgentMissionError("rigor_evidence_loader_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record_sha256(record: Mapping[str, Any]) -> str:
    return _sha256(_canonical_bytes(dict(record)))


def latest_actionable_ada_episode(
    artifact_path: Path,
) -> tuple[dict[str, Any], str] | None:
    records: list[dict[str, Any]] = []
    for number, line in enumerate(
        artifact_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AgentMissionError(
                f"artifact_invalid_json_line_{number}"
            ) from exc
        if not isinstance(value, dict):
            raise AgentMissionError(f"artifact_line_{number}_not_object")
        records.append(value)
    last_clean = "genesis"
    candidate: tuple[dict[str, Any], str] | None = None
    for record in records:
        if record.get("agent") != "ADA" or record.get("action") != "engineering_pulse":
            continue
        if record.get("status") == "clean":
            last_clean = str(record.get("ts") or "clean_without_timestamp")
            candidate = None
        elif record.get("status") == "issue" and candidate is None:
            candidate = (record, last_clean)
    return candidate


def latest_actionable_alice_episode(
    artifact_path: Path,
) -> tuple[dict[str, Any], str] | None:
    """Return the first unresolved ORION issue after the latest healthy state."""
    records: list[dict[str, Any]] = []
    for number, line in enumerate(
        artifact_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AgentMissionError(
                f"alice_artifact_invalid_json_line_{number}"
            ) from exc
        if not isinstance(value, dict):
            raise AgentMissionError(
                f"alice_artifact_line_{number}_not_object"
            )
        records.append(value)
    last_clean = "genesis"
    candidate: tuple[dict[str, Any], str] | None = None
    for record in records:
        status = str(record.get("status") or "")
        if status == "OK":
            last_clean = str(record.get("ts") or "clean_without_timestamp")
            candidate = None
        elif status in {
            "FAIL",
            "CRITICAL",
            "REMEDIATED",
            "UNVERIFIABLE",
        } and candidate is None:
            candidate = (record, last_clean)
    return candidate


def latest_actionable_nexus_episode(
    artifact_path: Path,
) -> tuple[dict[str, Any], str] | None:
    """Return the current distinct security-finding transition.

    Repeated identical snapshots join one mission.  A changed finding set
    creates a new episode even when no GREEN record occurred between them.
    """
    records: list[dict[str, Any]] = []
    for number, line in enumerate(
        artifact_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AgentMissionError(
                f"nexus_artifact_invalid_json_line_{number}"
            ) from exc
        if not isinstance(value, dict):
            raise AgentMissionError(
                f"nexus_artifact_line_{number}_not_object"
            )
        records.append(value)
    last_clean = "genesis"
    candidate: tuple[dict[str, Any], str] | None = None
    prior_issue_fingerprint: str | None = None
    for record in records:
        if (
            record.get("agent") != "NEXUS"
            or record.get("action") != "security_pulse"
        ):
            continue
        state = str(record.get("state") or "")
        if state == "GREEN" and record.get("status") == "clean":
            last_clean = str(record.get("ts") or "clean_without_timestamp")
            candidate = None
            prior_issue_fingerprint = None
        elif (
            state in {"FINDING", "BROKEN"}
            and record.get("status") == "issue"
        ):
            fingerprint = _sha256(
                _canonical_bytes(
                    {
                        "state": state,
                        "findings": sorted(record.get("findings") or []),
                        "broken": sorted(record.get("broken") or []),
                        "detail": record.get("detail"),
                    }
                )
            )
            if fingerprint != prior_issue_fingerprint:
                transition_anchor = (
                    f"{last_clean}:"
                    f"{prior_issue_fingerprint or 'first-issue'}:"
                    f"{record.get('ts') or 'issue-without-timestamp'}"
                )
                candidate = (record, transition_anchor)
                prior_issue_fingerprint = fingerprint
    return candidate


def latest_actionable_fable_episode(
    artifact_path: Path,
) -> tuple[dict[str, Any], str] | None:
    """Return the latest distinct unresolved FABLE rigor transition."""
    try:
        raw, _ = _secure_read(
            artifact_path,
            label="fable_rigor_ledger",
            max_bytes=4_194_304,
            required_mode=0o600,
        )
    except HandoffError as exc:
        raise AgentMissionError(str(exc)) from exc
    records: list[dict[str, Any]] = []
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = _json_no_duplicates(
                line, label=f"fable_rigor_line_{number}"
            )
        except HandoffError as exc:
            raise AgentMissionError(str(exc)) from exc
        if not isinstance(value, dict):
            raise AgentMissionError(
                f"fable_rigor_line_{number}_not_object"
            )
        records.append(value)

    clean_anchor = "genesis"
    prior_fingerprint: str | None = None
    candidate: tuple[dict[str, Any], str] | None = None
    for record in records:
        if (
            record.get("schema") != "seal.fable.rigor-claim-audit.v2"
            or record.get("agent") != "FABLE"
            or record.get("action") != "rigor_pulse"
            or record.get("target") != "rigor_claim_audit"
            or record.get("action_source")
            != "fable/rigor_claim_audit.py"
        ):
            raise AgentMissionError("fable_artifact_route_mismatch")
        state = str(record.get("state") or "")
        if state == "GREEN" and record.get("status") == "clean":
            clean_anchor = str(
                record.get("event_id")
                or record.get("ts")
                or "clean_without_anchor"
            )
            prior_fingerprint = None
            candidate = None
            continue
        if (
            state not in {"FINDING", "BROKEN"}
            or record.get("status") != "issue"
        ):
            raise AgentMissionError("fable_artifact_status_invalid")
        failures = sorted(
            (
                str(check.get("evidence_id") or ""),
                str(check.get("reason_code") or ""),
            )
            for check in record.get("checks", [])
            if isinstance(check, dict)
            and check.get("required") is True
            and check.get("status") == "FAIL"
        )
        broken = sorted(
            (
                str(check.get("evidence_id") or ""),
                str(check.get("reason_code") or ""),
            )
            for check in record.get("checks", [])
            if isinstance(check, dict)
            and check.get("required") is True
            and check.get("status") == "UNKNOWN"
        )
        semantic_fingerprint = _sha256(
            _canonical_bytes(
                {
                    "contract_rev": record.get("contract_rev"),
                    "target": record.get("target"),
                    "claim_id": (record.get("claim") or {}).get("claim_id"),
                    "source_record_sha256": (
                        record.get("claim") or {}
                    ).get("source_record_sha256"),
                    "state": state,
                    "failures": failures,
                    "broken": broken,
                }
            )
        )
        if semantic_fingerprint != prior_fingerprint:
            candidate = (
                record,
                f"{clean_anchor}:{semantic_fingerprint}",
            )
            prior_fingerprint = semantic_fingerprint
    return candidate


def _compile_ada_envelope(
    record: Mapping[str, Any],
    *,
    artifact_path: Path,
    episode_anchor: str,
    route: NervesRouteConfig = ADA_ROUTE,
    workspace_root: Path = ROOT,
) -> dict[str, Any]:
    if route.agent != "ADA" or route.action != "engineering_pulse":
        raise AgentMissionError("ada_compiler_route_mismatch")
    if record.get("agent") != route.agent or record.get("action") != route.action:
        raise AgentMissionError("source_record_route_mismatch")
    if record.get("status") != "issue":
        raise AgentMissionError("source_record_not_actionable")
    workspace = workspace_root.resolve(strict=True)
    source = artifact_path.resolve(strict=True)
    if not source.is_relative_to(workspace):
        raise AgentMissionError("artifact_outside_workspace")
    source_relative = source.relative_to(workspace).as_posix()
    record_digest = _record_sha256(record)
    episode_material = {
        "agent": route.agent,
        "action": route.action,
        "record_sha256": record_digest,
        "episode_anchor": episode_anchor,
        "route_sha256": route.route_sha256,
    }
    episode_key = _sha256(_canonical_bytes(episode_material))
    mission_id = str(uuid.uuid5(MISSION_NAMESPACE, episode_key))
    syntax_paths = []
    for value in record.get("syntax_failures", []):
        if not isinstance(value, str) or not value:
            continue
        candidate = Path(value)
        if candidate.is_absolute() or candidate.suffix != ".py":
            continue
        try:
            resolved = (workspace / candidate).resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if (
            not resolved.is_file()
            or not resolved.is_relative_to(workspace)
        ):
            continue
        syntax_paths.append(resolved.relative_to(workspace).as_posix())
    mission = {
        "schema": "seal.nerves.mission.v1",
        "mission_id": mission_id,
        "idempotency_key": f"ada-engineering-{episode_key}",
        "agent": route.agent,
        "tenant_id": "00000000-0000-0000-0000-000000000000",
        "nerve_fire_id": (
            f"ada-engineering:{record.get('ts')}:{episode_key[:16]}"
        ),
        "correlation_id": episode_key,
        "nerve_layer": "AGENT_ROLE",
        "drive": "reactive",
        "specialty": route.specialty,
        "objective": route.objective,
        "risk_class": route.risk_class,
        "source_refs": [
            f"file:{source_relative}",
            f"record_sha256:{record_digest}",
            f"episode_anchor:{episode_anchor}",
        ],
        "initiation_conditions": [
            "engineering_pulse status=issue",
            "source record is unique and hash-bound",
        ],
        "scope": {
            "workspace": str(workspace),
            "paths": sorted(
                {
                    "memory/nerves_maintenance_ada.py",
                    source_relative,
                    *syntax_paths,
                }
            ),
            "services": [],
            "network": "none",
        },
        "skills": [
            {
                "id": route.skill_id,
                "version": "1",
                "sha256": skill_bundle_digest(route.skill_dir),
            }
        ],
        "allowed_tools": list(route.allowed_tools),
        "budgets": {
            "wall_seconds": route.wall_seconds,
            "max_attempts": 1,
            "token_budget": route.token_budget,
        },
        "expected_evidence": [
            "typed engineering evidence",
            "runtime trace hash",
            "deterministic parent verifier",
        ],
        "termination_conditions": [
            "one typed result submitted",
            "scope invalid and worker abstains",
            "budget exhausted and mission fails closed",
        ],
        "rollback": {
            "required": False,
            "plan": "A2 is non-mutating; retain evidence and stop the worker.",
        },
        "builder": "ADA@mission-core-v2",
        "verifier": "deterministic-parent",
        "confidence_prior": 0.5,
    }
    _validate_schema(mission, MISSION_SCHEMA, label="mission")
    return mission


def compile_ada_engineering_mission(
    artifact_path: Path,
    *,
    route: NervesRouteConfig = ADA_ROUTE,
    ledger_path: Path = DEFAULT_LEDGER,
    manifest_dir: Path = DEFAULT_MANIFEST_DIR,
    bundle_dir: Path | None = None,
    workspace_root: Path = ROOT,
) -> CompiledMission:
    episode = latest_actionable_ada_episode(artifact_path)
    if episode is None:
        raise AgentMissionError("ada_artifact_has_no_actionable_episode")
    record, anchor = episode
    mission = _compile_ada_envelope(
        record,
        artifact_path=artifact_path,
        episode_anchor=anchor,
        route=route,
        workspace_root=workspace_root,
    )
    opened = ShadowMissionLedger(ledger_path).open_or_join(mission)
    manifest_path = write_shadow_manifest(opened.mission, manifest_dir)

    mission_id = str(opened.mission["mission_id"])
    bundle_dir = bundle_dir or (
        ROOT / "research/flywire_results/nerves_evidence_bundles/ADA"
    )
    _ensure_private_directory(bundle_dir)
    evidence_path = bundle_dir / f"{mission_id}.evidence.json"
    provenance_path = bundle_dir / f"{mission_id}.provenance.json"
    builder = _load_engineering_builder()
    record_digest = _record_sha256(record)
    evidence = builder.build_evidence(
        mission_id, record, expected_record_sha256=record_digest
    )
    _validate_schema(evidence, EVIDENCE_SCHEMA, label="evidence")
    evidence_created = _write_private_json(evidence_path, evidence)
    evidence_raw = _canonical_bytes(evidence) + b"\n"
    manifest_raw = manifest_path.read_bytes()
    provenance = {
        "schema": "seal.nerves.agent-provenance.v1",
        "mission_id": mission_id,
        "route_sha256": route.route_sha256,
        "manifest_sha256": _sha256(manifest_raw),
        "skill_bundle_sha256": skill_bundle_digest(route.skill_dir),
        "source": {
            "path": str(
                artifact_path.resolve(strict=True).relative_to(
                    workspace_root.resolve(strict=True)
                )
            ),
            "record_sha256": record_digest,
            "timestamp": str(record.get("ts") or ""),
            "correlation_id": opened.mission["correlation_id"],
            "nerve_fire_id": opened.mission["nerve_fire_id"],
        },
        "evidence_sha256": _sha256(evidence_raw),
    }
    provenance_created = _write_private_json(provenance_path, provenance)
    if not evidence_created:
        current, _ = _secure_json(evidence_path, label="evidence")
        if current != evidence:
            raise AgentMissionError("existing_evidence_mismatch")
    if not provenance_created:
        current, _ = _secure_json(provenance_path, label="provenance")
        if current != provenance:
            raise AgentMissionError("existing_provenance_mismatch")
    return CompiledMission(
        opened.mission,
        manifest_path,
        evidence_path,
        provenance_path,
        opened.created,
    )


def _compile_alice_envelope(
    record: Mapping[str, Any],
    *,
    artifact_path: Path,
    episode_anchor: str,
    route: NervesRouteConfig = ALICE_ROUTE,
    workspace_root: Path = ROOT,
) -> dict[str, Any]:
    if route.agent != "ALICE" or route.action != "orion_product_pulse":
        raise AgentMissionError("alice_compiler_route_mismatch")
    if record.get("status") not in {
        "FAIL",
        "CRITICAL",
        "REMEDIATED",
        "UNVERIFIABLE",
    }:
        raise AgentMissionError("alice_source_record_not_actionable")
    workspace = workspace_root.resolve(strict=True)
    source = artifact_path.resolve(strict=True)
    if not source.is_relative_to(workspace):
        raise AgentMissionError("alice_artifact_outside_workspace")
    source_relative = source.relative_to(workspace).as_posix()
    record_digest = _record_sha256(record)
    episode_key = _sha256(
        _canonical_bytes(
            {
                "agent": route.agent,
                "action": route.action,
                "record_sha256": record_digest,
                "episode_anchor": episode_anchor,
                "route_sha256": route.route_sha256,
            }
        )
    )
    mission_id = str(uuid.uuid5(MISSION_NAMESPACE, episode_key))
    mission = {
        "schema": "seal.nerves.mission.v1",
        "mission_id": mission_id,
        "idempotency_key": f"alice-orion-{episode_key}",
        "agent": route.agent,
        "tenant_id": "00000000-0000-0000-0000-000000000000",
        "nerve_fire_id": (
            f"alice-orion:{record.get('ts')}:{episode_key[:16]}"
        ),
        "correlation_id": episode_key,
        "nerve_layer": "AGENT_ROLE",
        "drive": "reactive",
        "specialty": route.specialty,
        "objective": route.objective,
        "risk_class": route.risk_class,
        "source_refs": [
            f"file:{source_relative}",
            f"record_sha256:{record_digest}",
            f"episode_anchor:{episode_anchor}",
        ],
        "initiation_conditions": [
            "ORION status requires diagnosis or a full OK confirmation",
            "source record is unique and hash-bound",
        ],
        "scope": {
            "workspace": str(workspace),
            "paths": sorted(
                {
                    "agents/ALICE/orion/orion_nerve.py",
                    "memory/nerves_maintenance_alice.py",
                    source_relative,
                }
            ),
            "services": ["orion-exam.service"],
            "network": "none",
        },
        "skills": [
            {
                "id": route.skill_id,
                "version": "1",
                "sha256": skill_bundle_digest(route.skill_dir),
            }
        ],
        "allowed_tools": list(route.allowed_tools),
        "budgets": {
            "wall_seconds": route.wall_seconds,
            "max_attempts": 1,
            "token_budget": route.token_budget,
        },
        "expected_evidence": [
            "typed ORION product evidence",
            "runtime trace hash",
            "deterministic parent verifier",
        ],
        "termination_conditions": [
            "one typed result submitted",
            "scope invalid and worker abstains",
            "budget exhausted and mission fails closed",
        ],
        "rollback": {
            "required": False,
            "plan": "A2 is non-mutating; retain evidence and stop the worker.",
        },
        "builder": "ADA@mission-core-v3",
        "verifier": "deterministic-parent",
        "confidence_prior": 0.5,
    }
    _validate_schema(mission, MISSION_SCHEMA, label="alice_mission")
    return mission


def compile_alice_orion_mission(
    artifact_path: Path,
    *,
    route: NervesRouteConfig = ALICE_ROUTE,
    ledger_path: Path = DEFAULT_LEDGER,
    manifest_dir: Path = DEFAULT_MANIFEST_DIR,
    bundle_dir: Path | None = None,
    workspace_root: Path = ROOT,
) -> CompiledMission:
    episode = latest_actionable_alice_episode(artifact_path)
    if episode is None:
        raise AgentMissionError("alice_artifact_has_no_actionable_episode")
    record, anchor = episode
    mission = _compile_alice_envelope(
        record,
        artifact_path=artifact_path,
        episode_anchor=anchor,
        route=route,
        workspace_root=workspace_root,
    )
    opened = ShadowMissionLedger(ledger_path).open_or_join(mission)
    manifest_path = write_shadow_manifest(opened.mission, manifest_dir)

    mission_id = str(opened.mission["mission_id"])
    bundle_dir = bundle_dir or (
        ROOT / "research/flywire_results/nerves_evidence_bundles/ALICE"
    )
    _ensure_private_directory(bundle_dir)
    evidence_path = bundle_dir / f"{mission_id}.evidence.json"
    provenance_path = bundle_dir / f"{mission_id}.provenance.json"
    builder = _load_orion_builder(route)
    record_digest = _record_sha256(record)
    evidence = builder.build_evidence(
        mission_id, record, expected_record_sha256=record_digest
    )
    _validate_schema(evidence, EVIDENCE_SCHEMA, label="alice_evidence")
    evidence_created = _write_private_json(evidence_path, evidence)
    evidence_raw = _canonical_bytes(evidence) + b"\n"
    manifest_raw = manifest_path.read_bytes()
    provenance = {
        "schema": "seal.nerves.agent-provenance.v1",
        "mission_id": mission_id,
        "route_sha256": route.route_sha256,
        "manifest_sha256": _sha256(manifest_raw),
        "skill_bundle_sha256": skill_bundle_digest(route.skill_dir),
        "source": {
            "path": str(
                artifact_path.resolve(strict=True).relative_to(
                    workspace_root.resolve(strict=True)
                )
            ),
            "record_sha256": record_digest,
            "timestamp": str(record.get("ts") or ""),
            "correlation_id": opened.mission["correlation_id"],
            "nerve_fire_id": opened.mission["nerve_fire_id"],
        },
        "evidence_sha256": _sha256(evidence_raw),
    }
    provenance_created = _write_private_json(provenance_path, provenance)
    if not evidence_created:
        current, _ = _secure_json(evidence_path, label="alice_evidence")
        if current != evidence:
            raise AgentMissionError("existing_alice_evidence_mismatch")
    if not provenance_created:
        current, _ = _secure_json(
            provenance_path, label="alice_provenance"
        )
        if current != provenance:
            raise AgentMissionError("existing_alice_provenance_mismatch")
    return CompiledMission(
        opened.mission,
        manifest_path,
        evidence_path,
        provenance_path,
        opened.created,
    )


def _compile_nexus_envelope(
    record: Mapping[str, Any],
    *,
    artifact_path: Path,
    episode_anchor: str,
    route: NervesRouteConfig = NEXUS_ROUTE,
    workspace_root: Path = ROOT,
) -> dict[str, Any]:
    if route.agent != "NEXUS" or route.action != "security_pulse":
        raise AgentMissionError("nexus_compiler_route_mismatch")
    if (
        record.get("agent") != route.agent
        or record.get("action") != route.action
        or record.get("state") not in {"FINDING", "BROKEN"}
        or record.get("status") != "issue"
    ):
        raise AgentMissionError("nexus_source_record_not_actionable")
    workspace = workspace_root.resolve(strict=True)
    source = artifact_path.resolve(strict=True)
    if not source.is_relative_to(workspace):
        raise AgentMissionError("nexus_artifact_outside_workspace")
    source_relative = source.relative_to(workspace).as_posix()
    record_digest = _record_sha256(record)
    episode_key = _sha256(
        _canonical_bytes(
            {
                "agent": route.agent,
                "action": route.action,
                "record_sha256": record_digest,
                "episode_anchor": episode_anchor,
                "route_sha256": route.route_sha256,
            }
        )
    )
    mission_id = str(uuid.uuid5(MISSION_NAMESPACE, episode_key))
    mission = {
        "schema": "seal.nerves.mission.v1",
        "mission_id": mission_id,
        "idempotency_key": f"nexus-security-{episode_key}",
        "agent": route.agent,
        "tenant_id": "00000000-0000-0000-0000-000000000000",
        "nerve_fire_id": (
            f"nexus-security:{record.get('ts')}:{episode_key[:16]}"
        ),
        "correlation_id": episode_key,
        "nerve_layer": "AGENT_ROLE",
        "drive": "reactive",
        "specialty": route.specialty,
        "objective": route.objective,
        "risk_class": route.risk_class,
        "source_refs": [
            f"file:{source_relative}",
            f"record_sha256:{record_digest}",
            f"episode_anchor:{episode_anchor}",
        ],
        "initiation_conditions": [
            "security_pulse state is FINDING or BROKEN",
            "source record is unique and hash-bound",
        ],
        "scope": {
            "workspace": str(workspace),
            "paths": sorted(
                {
                    "tools/nexus_nerves_watch.py",
                    "memory/nerves_maintenance_nexus.py",
                    source_relative,
                }
            ),
            "services": [
                "seal-security-monitor.service",
                "seal-chat.service",
            ],
            "network": "none",
        },
        "skills": [
            {
                "id": route.skill_id,
                "version": "1",
                "sha256": skill_bundle_digest(route.skill_dir),
            }
        ],
        "allowed_tools": list(route.allowed_tools),
        "budgets": {
            "wall_seconds": route.wall_seconds,
            "max_attempts": 1,
            "token_budget": route.token_budget,
        },
        "expected_evidence": [
            "typed NEXUS security evidence",
            "runtime trace hash",
            "deterministic parent verifier",
        ],
        "termination_conditions": [
            "one typed result submitted",
            "scope invalid and worker abstains",
            "budget exhausted and mission fails closed",
        ],
        "rollback": {
            "required": False,
            "plan": "A2 is non-mutating; retain evidence and stop the worker.",
        },
        "builder": "ADA@mission-core-v4",
        "verifier": "deterministic-parent",
        "confidence_prior": 0.5,
    }
    _validate_schema(mission, MISSION_SCHEMA, label="nexus_mission")
    return mission


def compile_nexus_security_mission(
    artifact_path: Path,
    *,
    route: NervesRouteConfig = NEXUS_ROUTE,
    ledger_path: Path = DEFAULT_LEDGER,
    manifest_dir: Path = DEFAULT_MANIFEST_DIR,
    bundle_dir: Path | None = None,
    workspace_root: Path = ROOT,
) -> CompiledMission:
    episode = latest_actionable_nexus_episode(artifact_path)
    if episode is None:
        raise AgentMissionError("nexus_artifact_has_no_actionable_episode")
    record, anchor = episode
    mission = _compile_nexus_envelope(
        record,
        artifact_path=artifact_path,
        episode_anchor=anchor,
        route=route,
        workspace_root=workspace_root,
    )
    opened = ShadowMissionLedger(ledger_path).open_or_join(mission)
    manifest_path = write_shadow_manifest(opened.mission, manifest_dir)
    mission_id = str(opened.mission["mission_id"])
    bundle_dir = bundle_dir or (
        ROOT / "research/flywire_results/nerves_evidence_bundles/NEXUS"
    )
    _ensure_private_directory(bundle_dir)
    evidence_path = bundle_dir / f"{mission_id}.evidence.json"
    provenance_path = bundle_dir / f"{mission_id}.provenance.json"
    builder = _load_security_builder(route)
    record_digest = _record_sha256(record)
    evidence = builder.build_evidence(
        mission_id, record, expected_record_sha256=record_digest
    )
    _validate_schema(evidence, EVIDENCE_SCHEMA, label="nexus_evidence")
    evidence_created = _write_private_json(evidence_path, evidence)
    evidence_raw = _canonical_bytes(evidence) + b"\n"
    provenance = {
        "schema": "seal.nerves.agent-provenance.v1",
        "mission_id": mission_id,
        "route_sha256": route.route_sha256,
        "manifest_sha256": _sha256(manifest_path.read_bytes()),
        "skill_bundle_sha256": skill_bundle_digest(route.skill_dir),
        "source": {
            "path": str(
                artifact_path.resolve(strict=True).relative_to(
                    workspace_root.resolve(strict=True)
                )
            ),
            "record_sha256": record_digest,
            "timestamp": str(record.get("ts") or ""),
            "correlation_id": opened.mission["correlation_id"],
            "nerve_fire_id": opened.mission["nerve_fire_id"],
        },
        "evidence_sha256": _sha256(evidence_raw),
    }
    provenance_created = _write_private_json(provenance_path, provenance)
    if not evidence_created:
        current, _ = _secure_json(evidence_path, label="nexus_evidence")
        if current != evidence:
            raise AgentMissionError("existing_nexus_evidence_mismatch")
    if not provenance_created:
        current, _ = _secure_json(
            provenance_path, label="nexus_provenance"
        )
        if current != provenance:
            raise AgentMissionError("existing_nexus_provenance_mismatch")
    return CompiledMission(
        opened.mission,
        manifest_path,
        evidence_path,
        provenance_path,
        opened.created,
    )


def _compile_fable_envelope(
    record: Mapping[str, Any],
    *,
    artifact_path: Path,
    episode_anchor: str,
    route: NervesRouteConfig = FABLE_ROUTE,
    workspace_root: Path = ROOT,
) -> dict[str, Any]:
    if route.agent != "FABLE" or route.action != "rigor_pulse":
        raise AgentMissionError("fable_compiler_route_mismatch")
    if (
        record.get("agent") != route.agent
        or record.get("action") != route.action
        or record.get("target") != "rigor_claim_audit"
        or record.get("state") not in {"FINDING", "BROKEN"}
        or record.get("status") != "issue"
    ):
        raise AgentMissionError("fable_source_record_not_actionable")
    workspace = workspace_root.resolve(strict=True)
    source = artifact_path.resolve(strict=True)
    if not source.is_relative_to(workspace):
        raise AgentMissionError("fable_artifact_outside_workspace")
    source_relative = source.relative_to(workspace).as_posix()
    record_digest = _record_sha256(record)
    episode_key = _sha256(
        _canonical_bytes(
            {
                "agent": route.agent,
                "action": route.action,
                "episode_anchor": episode_anchor,
                "route_sha256": route.route_sha256,
            }
        )
    )
    mission_id = str(uuid.uuid5(MISSION_NAMESPACE, episode_key))
    mission = {
        "schema": "seal.nerves.mission.v1",
        "mission_id": mission_id,
        "idempotency_key": f"fable-rigor-{episode_key}",
        "agent": route.agent,
        "tenant_id": "00000000-0000-0000-0000-000000000000",
        "nerve_fire_id": (
            f"fable-rigor:{record.get('ts')}:{episode_key[:16]}"
        ),
        "correlation_id": episode_key,
        "nerve_layer": "AGENT_ROLE",
        "drive": "proactive",
        "specialty": route.specialty,
        "objective": route.objective,
        "risk_class": route.risk_class,
        "source_refs": [
            f"file:{source_relative}",
            f"record_sha256:{record_digest}",
            f"episode_anchor:{episode_anchor}",
        ],
        "initiation_conditions": [
            "FABLE rigor claim audit state is FINDING or BROKEN",
            "source record is typed, transition-deduplicated, and hash-bound",
        ],
        "scope": {
            "workspace": str(workspace),
            "paths": sorted(
                {
                    "fable/fable_nerves.py",
                    "fable/rigor_claim_audit.py",
                    source_relative,
                }
            ),
            "services": ["fable-nerves.service"],
            "network": "none",
        },
        "skills": [
            {
                "id": route.skill_id,
                "version": "1",
                "sha256": skill_bundle_digest(route.skill_dir),
            }
        ],
        "allowed_tools": list(route.allowed_tools),
        "budgets": {
            "wall_seconds": route.wall_seconds,
            "max_attempts": 1,
            "token_budget": route.token_budget,
        },
        "expected_evidence": [
            "typed FABLE rigor evidence",
            "runtime trace hash",
            "deterministic parent verifier",
        ],
        "termination_conditions": [
            "one typed result submitted",
            "scope invalid and worker abstains",
            "budget exhausted and mission fails closed",
        ],
        "rollback": {
            "required": False,
            "plan": "A2 is non-mutating; retain evidence and stop the worker.",
        },
        "builder": "ADA@mission-core-v5",
        "verifier": "deterministic-parent",
        "confidence_prior": 0.5,
    }
    _validate_schema(mission, MISSION_SCHEMA, label="fable_mission")
    return mission


def compile_fable_rigor_mission(
    artifact_path: Path,
    *,
    route: NervesRouteConfig = FABLE_ROUTE,
    ledger_path: Path = DEFAULT_LEDGER,
    manifest_dir: Path = DEFAULT_MANIFEST_DIR,
    bundle_dir: Path | None = None,
    workspace_root: Path = ROOT,
) -> CompiledMission:
    episode = latest_actionable_fable_episode(artifact_path)
    if episode is None:
        raise AgentMissionError("fable_artifact_has_no_actionable_episode")
    record, anchor = episode
    mission = _compile_fable_envelope(
        record,
        artifact_path=artifact_path,
        episode_anchor=anchor,
        route=route,
        workspace_root=workspace_root,
    )
    opened = ShadowMissionLedger(ledger_path).open_or_join(mission)
    manifest_path = write_shadow_manifest(opened.mission, manifest_dir)
    mission_id = str(opened.mission["mission_id"])
    bundle_dir = bundle_dir or (
        ROOT / "research/flywire_results/nerves_evidence_bundles/FABLE"
    )
    _ensure_private_directory(bundle_dir)
    evidence_path = bundle_dir / f"{mission_id}.evidence.json"
    provenance_path = bundle_dir / f"{mission_id}.provenance.json"
    builder = _load_rigor_builder(route)
    record_digest = _record_sha256(record)
    evidence = builder.build_evidence(
        mission_id, record, expected_record_sha256=record_digest
    )
    _validate_schema(evidence, EVIDENCE_SCHEMA, label="fable_evidence")
    evidence_created = _write_private_json(evidence_path, evidence)
    evidence_raw = _canonical_bytes(evidence) + b"\n"
    provenance = {
        "schema": "seal.nerves.agent-provenance.v1",
        "mission_id": mission_id,
        "route_sha256": route.route_sha256,
        "manifest_sha256": _sha256(manifest_path.read_bytes()),
        "skill_bundle_sha256": skill_bundle_digest(route.skill_dir),
        "source": {
            "path": str(
                artifact_path.resolve(strict=True).relative_to(
                    workspace_root.resolve(strict=True)
                )
            ),
            "record_sha256": record_digest,
            "timestamp": str(record.get("ts") or ""),
            "correlation_id": opened.mission["correlation_id"],
            "nerve_fire_id": opened.mission["nerve_fire_id"],
        },
        "evidence_sha256": _sha256(evidence_raw),
    }
    provenance_created = _write_private_json(provenance_path, provenance)
    if not evidence_created:
        current, _ = _secure_json(evidence_path, label="fable_evidence")
        if current != evidence:
            raise AgentMissionError("existing_fable_evidence_mismatch")
    if not provenance_created:
        current, _ = _secure_json(
            provenance_path, label="fable_provenance"
        )
        if current != provenance:
            raise AgentMissionError("existing_fable_provenance_mismatch")
    return CompiledMission(
        opened.mission,
        manifest_path,
        evidence_path,
        provenance_path,
        opened.created,
    )


def _validated_bundle(
    compiled: CompiledMission, route: NervesRouteConfig
) -> tuple[dict[str, Any], dict[str, Any], str, str, str]:
    manifest, manifest_raw = _secure_json(
        compiled.manifest_path, label="manifest"
    )
    evidence, evidence_raw = _secure_json(
        compiled.evidence_path, label="evidence"
    )
    provenance, provenance_raw = _secure_json(
        compiled.provenance_path, label="provenance"
    )
    _validate_schema(manifest, MISSION_SCHEMA, label="mission")
    _validate_schema(evidence, EVIDENCE_SCHEMA, label="evidence")
    if manifest != compiled.mission:
        raise AgentMissionError("compiled_manifest_mismatch")
    if manifest.get("agent") != route.agent:
        raise AgentMissionError("mission_agent_route_mismatch")
    if manifest.get("risk_class") != route.risk_class:
        raise AgentMissionError("mission_risk_route_mismatch")
    if manifest.get("specialty") != route.specialty:
        raise AgentMissionError("mission_specialty_route_mismatch")
    if set(manifest.get("allowed_tools", [])) != set(route.allowed_tools):
        raise AgentMissionError("mission_tools_route_mismatch")
    if evidence.get("mission_id") != manifest["mission_id"]:
        raise AgentMissionError("evidence_mission_mismatch")
    if provenance.get("route_sha256") != route.route_sha256:
        raise AgentMissionError("provenance_route_mismatch")
    bindings = {
        "mission": provenance.get("manifest_sha256") == _sha256(manifest_raw),
        "skill": provenance.get("skill_bundle_sha256")
        == skill_bundle_digest(route.skill_dir),
        "evidence": provenance.get("evidence_sha256") == _sha256(evidence_raw),
        "source_record": provenance.get("source", {}).get("record_sha256")
        == evidence.get("source_record_sha256"),
    }
    failed = [name for name, ok in bindings.items() if not ok]
    if failed:
        raise AgentMissionError(
            "bundle_binding_mismatch:" + ",".join(failed)
        )
    return (
        evidence,
        provenance,
        _sha256(manifest_raw),
        _sha256(evidence_raw),
        _sha256(provenance_raw),
    )


def _handoff_document(
    compiled: CompiledMission,
    route: NervesRouteConfig,
    *,
    evidence_sha256: str,
    provenance_sha256: str,
    created_at: str,
) -> dict[str, Any]:
    mission = compiled.mission
    mission_id = str(mission["mission_id"])
    handoff_id = str(
        uuid.uuid5(
            HANDOFF_NAMESPACE,
            f"{mission_id}:{mission['idempotency_key']}:{route.route_sha256}",
        )
    )
    handoff_path = route.inbox_dir / f"{mission_id}.handoff.json"
    return {
        "schema": HANDOFF_SCHEMA,
        "handoff_id": handoff_id,
        "mission_id": mission_id,
        "idempotency_key": mission["idempotency_key"],
        "created_at": created_at,
        "authority": {
            **route.identity(),
            "network": "loopback_ollama_only",
            "max_attempts": 1,
            "subagents": 1,
            "sub_subagents": 0,
        },
        "bindings": {
            "manifest_path": str(compiled.manifest_path.resolve(strict=True)),
            "manifest_sha256": _sha256(compiled.manifest_path.read_bytes()),
            "evidence_path": str(compiled.evidence_path.resolve(strict=True)),
            "evidence_sha256": evidence_sha256,
            "provenance_path": str(
                compiled.provenance_path.resolve(strict=True)
            ),
            "provenance_sha256": provenance_sha256,
            "route_sha256": route.route_sha256,
        },
        "instruction": (
            "Validate custody, claim atomically, launch exactly one isolated "
            "read-only runtime adapter, and commit only a schema-valid receipt. "
            "The result is evidence and never grants repair authority."
        ),
        "receipt_contract": {
            "schema": RECEIPT_SCHEMA_ID,
            "schema_path": str(RECEIPT_SCHEMA),
            "result_schema_path": str(route.result_schema),
            "path": str(handoff_path.with_suffix(".receipt.json")),
            "mode": "0600",
            "worker_kind": "local_ollama_subagent",
        },
    }


def deliver_handoff(
    compiled: CompiledMission,
    *,
    route: NervesRouteConfig = ADA_ROUTE,
    now: datetime | None = None,
    notify_live: bool = True,
) -> DeliveryResult:
    evidence, _, _, evidence_sha256, provenance_sha256 = _validated_bundle(
        compiled, route
    )
    _ensure_private_directory(route.inbox_dir)
    current = now or datetime.now(timezone.utc)
    created_at = str(evidence.get("observed_at") or "")
    try:
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgentMissionError("evidence_observed_at_invalid") from exc
    handoff = _handoff_document(
        compiled,
        route,
        evidence_sha256=evidence_sha256,
        provenance_sha256=provenance_sha256,
        created_at=created_at,
    )
    mission_id = str(compiled.mission["mission_id"])
    idempotency_key = str(compiled.mission["idempotency_key"])
    handoff_path = route.inbox_dir / f"{mission_id}.handoff.json"
    receipt_path = handoff_path.with_suffix(".receipt.json")
    handoff_raw = _canonical_bytes(handoff) + b"\n"
    handoff_sha256 = _sha256(handoff_raw)

    lock_fd = _open_lock(
        route.state_path.with_suffix(route.state_path.suffix + ".lock")
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        state = _load_state(route.state_path)
        deliveries = dict(state["deliveries"])
        record = deliveries.get(mission_id)
        if record is not None:
            if not isinstance(record, dict):
                raise AgentMissionError("state_delivery_record_invalid")
            expected = {
                "idempotency_key": idempotency_key,
                "handoff_sha256": handoff_sha256,
                "evidence_sha256": evidence_sha256,
                "provenance_sha256": provenance_sha256,
                "route_sha256": route.route_sha256,
            }
            failed = [
                key for key, value in expected.items()
                if record.get(key) != value
            ]
            if failed:
                raise AgentMissionError(
                    "existing_delivery_binding_mismatch:" + ",".join(failed)
                )
            current_handoff, current_raw = _secure_json(
                handoff_path, label="handoff"
            )
            if current_handoff != handoff or _sha256(current_raw) != handoff_sha256:
                raise AgentMissionError("existing_handoff_mismatch")
            return DeliveryResult(
                mission_id,
                idempotency_key,
                handoff_path,
                receipt_path,
                handoff_sha256,
                False,
                str(record["status"]),
                bool(record.get("live_notified_at")),
            )

        created = _write_private_json(handoff_path, handoff)
        if not created:
            raise AgentMissionError("orphan_handoff_exists")
        record = {
            "mission_id": mission_id,
            "idempotency_key": idempotency_key,
            "inbox_path": str(handoff_path),
            "receipt_path": str(receipt_path),
            "handoff_sha256": handoff_sha256,
            "evidence_sha256": evidence_sha256,
            "provenance_sha256": provenance_sha256,
            "route_sha256": route.route_sha256,
            "status": "pending",
            "created_at": current.isoformat(),
            "live_notified_at": None,
            "claim": None,
            "receipt": None,
        }
        deliveries[mission_id] = record
        _write_state_atomic(route.state_path, _state_body(deliveries))

        notified = False
        if notify_live:
            event_id = f"nerves_agent_handoff_{handoff['handoff_id']}"
            if not _feed_contains_event(route.live_feed, event_id):
                _append_live_feed(
                    route.live_feed,
                    {
                        "id": event_id,
                        "from": "NERVES",
                        "to": route.agent,
                        "timestamp": current.isoformat(),
                        "type": "nerves_mission",
                        "channel": route.live_channel,
                        "message": (
                            f"NERVES {route.agent}: authenticated A2 handoff "
                            "pending. Validate, claim, delegate one isolated "
                            "read-only worker, then verify."
                        ),
                        "handoff_id": handoff["handoff_id"],
                        "handoff_path": str(handoff_path),
                        "handoff_sha256": handoff_sha256,
                        "mission_id": mission_id,
                    },
                )
            notified = True
            record["status"] = "live_notified"
            record["live_notified_at"] = current.isoformat()
            deliveries[mission_id] = record
            _write_state_atomic(route.state_path, _state_body(deliveries))
        return DeliveryResult(
            mission_id,
            idempotency_key,
            handoff_path,
            receipt_path,
            handoff_sha256,
            True,
            str(record["status"]),
            notified,
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def claim_handoff(
    mission_id: str,
    *,
    worker_id: str,
    route: NervesRouteConfig = ADA_ROUTE,
    now: datetime | None = None,
) -> ClaimResult:
    try:
        mission_id = str(uuid.UUID(mission_id))
    except ValueError as exc:
        raise AgentMissionError("mission_id_invalid") from exc
    if not worker_id or len(worker_id) > 160:
        raise AgentMissionError("worker_id_invalid")
    lock_fd = _open_lock(
        route.state_path.with_suffix(route.state_path.suffix + ".lock")
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        state = _load_state(route.state_path)
        deliveries = dict(state["deliveries"])
        record = deliveries.get(mission_id)
        if not isinstance(record, dict):
            raise AgentMissionError("claim_unknown_mission")
        if record.get("route_sha256") != route.route_sha256:
            raise AgentMissionError("claim_route_mismatch")
        if record.get("status") in STATE_TERMINAL:
            raise AgentMissionError("handoff_not_claimable")
        existing = record.get("claim")
        if existing is not None:
            if isinstance(existing, dict) and existing.get("worker_id") == worker_id:
                return ClaimResult(
                    mission_id,
                    str(existing["claim_id"]),
                    worker_id,
                    False,
                    str(record["status"]),
                    Path(str(record["receipt_path"])),
                )
            raise AgentMissionError("handoff_already_claimed")
        if record.get("status") not in {"pending", "live_notified"}:
            raise AgentMissionError("handoff_not_claimable")
        handoff_path = Path(str(record["inbox_path"]))
        _, handoff_raw = _secure_json(handoff_path, label="handoff")
        if _sha256(handoff_raw) != record["handoff_sha256"]:
            raise AgentMissionError("claim_handoff_hash_mismatch")
        claim_id = str(
            uuid.uuid5(
                CLAIM_NAMESPACE,
                f"{mission_id}:{record['handoff_sha256']}:{worker_id}",
            )
        )
        claimed_at = now or datetime.now(timezone.utc)
        record = dict(record)
        record["claim"] = {
            "claim_id": claim_id,
            "worker_id": worker_id,
            "worker_kind": "local_ollama_subagent",
            "runtime": route.runtime,
            "claimed_at": claimed_at.isoformat(),
            "lease_expires_at": (
                claimed_at + timedelta(seconds=route.claim_lease_seconds)
            ).isoformat(),
            "attempt": 1,
        }
        record["status"] = "claimed"
        deliveries[mission_id] = record
        _write_state_atomic(route.state_path, _state_body(deliveries))
        return ClaimResult(
            mission_id,
            claim_id,
            worker_id,
            True,
            "claimed",
            Path(str(record["receipt_path"])),
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def hold_handoff(
    mission_id: str,
    *,
    command: Mapping[str, Any],
    route: NervesRouteConfig = ADA_ROUTE,
    now: datetime | None = None,
) -> HoldResult:
    """Atomically stop a pending/claimed A2 mission after verified William HOLD.

    The caller must be the trusted command adapter.  This function still
    revalidates the adapter's typed authority evidence and persists the released
    claim for audit.  A held mission becomes terminal ``abstained``; no later
    receipt can be accepted and it cannot be claimed again.
    """
    try:
        mission_id = str(uuid.UUID(mission_id))
    except ValueError as exc:
        raise AgentMissionError("mission_id_invalid") from exc
    expected_keys = {
        "schema",
        "command_id",
        "command",
        "issuer",
        "source_channel",
        "source_ref",
        "authority_adapter",
        "authority_evidence_sha256",
    }
    if set(command) != expected_keys:
        raise AgentMissionError("hold_command_shape_invalid")
    if command["schema"] != "seal.nerves.hold-command.v1":
        raise AgentMissionError("hold_command_schema_invalid")
    if command["command"] not in {"HOLD", "STOP"}:
        raise AgentMissionError("hold_command_invalid")
    if command["issuer"] != "William":
        raise AgentMissionError("hold_issuer_invalid")
    if command["source_channel"] not in {"web_chat", "dm:ada:william"}:
        raise AgentMissionError("hold_source_channel_invalid")
    source_ref = command["source_ref"]
    if (
        not isinstance(source_ref, str)
        or not source_ref.startswith(("api_william_", "db_"))
    ):
        raise AgentMissionError("hold_source_ref_invalid")
    if command["authority_adapter"] not in {
        "seal_chat_session_identity",
        "seal_memory_session_identity",
    }:
        raise AgentMissionError("hold_authority_adapter_invalid")
    authority_hash = command["authority_evidence_sha256"]
    if (
        not isinstance(authority_hash, str)
        or len(authority_hash) != 64
        or any(char not in "0123456789abcdef" for char in authority_hash)
    ):
        raise AgentMissionError("hold_authority_evidence_invalid")
    command_id = command["command_id"]
    if not isinstance(command_id, str) or not command_id:
        raise AgentMissionError("hold_command_id_invalid")

    lock_fd = _open_lock(
        route.state_path.with_suffix(route.state_path.suffix + ".lock")
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        state = _load_state(route.state_path)
        deliveries = dict(state["deliveries"])
        record = deliveries.get(mission_id)
        if not isinstance(record, dict):
            raise AgentMissionError("hold_unknown_mission")
        if record.get("route_sha256") != route.route_sha256:
            raise AgentMissionError("hold_route_mismatch")
        prior_hold = record.get("hold")
        if prior_hold is not None:
            if prior_hold.get("command_id") != command_id:
                raise AgentMissionError("mission_already_held_by_other_command")
            return HoldResult(
                mission_id,
                command_id,
                str(record["status"]),
                False,
                bool(prior_hold.get("lease_released")),
                0,
            )
        if record.get("status") in STATE_TERMINAL:
            raise AgentMissionError("terminal_mission_not_holdable")
        held_at = now or datetime.now(timezone.utc)
        record = dict(record)
        claim = record.get("claim")
        released_claim = None
        if isinstance(claim, dict):
            released_claim = {
                **claim,
                "lease_released_at": held_at.isoformat(),
                "release_reason": str(command["command"]),
            }
        record["released_claim"] = released_claim
        record["claim"] = None
        record["status"] = "abstained"
        record["hold"] = {
            **dict(command),
            "command_sha256": _sha256(_canonical_bytes(dict(command))),
            "held_at": held_at.isoformat(),
            "lease_released": released_claim is not None,
            "accepted_effects_after_command": 0,
        }
        deliveries[mission_id] = record
        _write_state_atomic(route.state_path, _state_body(deliveries))
        return HoldResult(
            mission_id,
            command_id,
            "abstained",
            True,
            released_claim is not None,
            0,
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _validate_receipt_document(
    receipt: Mapping[str, Any],
    *,
    record: Mapping[str, Any],
    route: NervesRouteConfig,
) -> list[str]:
    receipt_schema = json.loads(RECEIPT_SCHEMA.read_text(encoding="utf-8"))
    reasoning_schema = json.loads(REASONING_SCHEMA.read_text(encoding="utf-8"))
    receipt_schema["properties"]["output"] = reasoning_schema
    errors = sorted(
        Draft202012Validator(
            receipt_schema, format_checker=FormatChecker()
        ).iter_errors(receipt),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        detail = ";".join(
            f"{'/'.join(map(str, error.absolute_path))}:{error.message}"
            for error in errors[:8]
        )
        raise AgentMissionError(f"receipt_schema_invalid:{detail}")
    claim = record.get("claim")
    if not isinstance(claim, dict):
        raise AgentMissionError("receipt_requires_claim")
    runtime = receipt["runtime_attestation"]
    checks = {
        "mission_id": receipt["mission_id"] == record["mission_id"],
        "idempotency_key": (
            receipt["idempotency_key"] == record["idempotency_key"]
        ),
        "handoff_sha256": (
            receipt["handoff_sha256"] == record["handoff_sha256"]
        ),
        "claim_id": receipt["claim_id"] == claim["claim_id"],
        "worker_id": receipt["worker_id"] == claim["worker_id"],
        "worker_kind": (
            receipt["worker_kind"] == claim["worker_kind"]
            == "local_ollama_subagent"
        ),
        "evidence_sha256": (
            receipt["evidence_sha256"] == record["evidence_sha256"]
        ),
        "provenance_sha256": (
            receipt["provenance_sha256"] == record["provenance_sha256"]
        ),
        "runtime": (
            runtime["platform"] == "ollama_generate_json"
            and route.runtime == "local_ollama_json_no_tools"
        ),
        "isolation": runtime["isolation"] == "no_tool_api",
        "zero_tools": runtime["tool_events"] == [],
        "verifier": (
            receipt["verifier"]["kind"] == "deterministic_parent"
            and receipt["verifier"]["verdict"] == "accepted"
        ),
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise AgentMissionError(
            "receipt_binding_mismatch:" + ",".join(failed)
        )

    started = datetime.fromisoformat(
        str(receipt["started_at"]).replace("Z", "+00:00")
    )
    finished = datetime.fromisoformat(
        str(receipt["finished_at"]).replace("Z", "+00:00")
    )
    claimed_at = datetime.fromisoformat(
        str(claim["claimed_at"]).replace("Z", "+00:00")
    )
    if started < claimed_at or finished < started:
        raise AgentMissionError("receipt_time_order_invalid")

    artifact_root = route.runtime_artifact_dir.resolve(strict=True)
    artifacts: dict[str, tuple[dict[str, Any], bytes]] = {}
    for label in ("preflight", "request", "response"):
        path = Path(str(runtime[f"{label}_path"])).resolve(strict=True)
        if not path.is_relative_to(artifact_root):
            raise AgentMissionError(f"{label}_artifact_outside_runtime_root")
        value, raw = _secure_json(path, label=f"runtime_{label}")
        if _sha256(raw) != runtime[f"{label}_sha256"]:
            raise AgentMissionError(f"{label}_artifact_digest_mismatch")
        artifacts[label] = (value, raw)

    preflight, _ = artifacts["preflight"]
    request, _ = artifacts["request"]
    response, _ = artifacts["response"]
    if preflight != {
        "endpoint": "http://127.0.0.1:11434/api/tags",
        "model": runtime["model"],
        "model_digest": runtime["model_digest"],
    }:
        raise AgentMissionError("runtime_preflight_contract_mismatch")
    prompt = request.get("prompt")
    if not isinstance(prompt, str):
        raise AgentMissionError("runtime_request_prompt_missing")
    if _sha256(prompt.encode("utf-8")) != runtime["prompt_sha256"]:
        raise AgentMissionError("runtime_prompt_digest_mismatch")
    expected_request = {
        "format": "json",
        "model": runtime["model"],
        "options": {
            "num_predict": 2048,
            "seed": 42,
            "temperature": 0,
        },
        "prompt": prompt,
        "stream": False,
    }
    if request != expected_request:
        raise AgentMissionError("runtime_request_contract_mismatch")

    output = dict(receipt["output"])
    if _sha256(_canonical_bytes(output)) != runtime["result_sha256"]:
        raise AgentMissionError("runtime_result_digest_mismatch")
    handoff, _ = _secure_json(
        Path(str(record["inbox_path"])), label="receipt_handoff"
    )
    evidence_path = Path(str(handoff["bindings"]["evidence_path"]))
    evidence, evidence_raw = _secure_json(
        evidence_path, label="receipt_evidence"
    )
    if _sha256(evidence_raw) != record["evidence_sha256"]:
        raise AgentMissionError("receipt_evidence_digest_mismatch")
    result_checks = validate_reasoning_result(
        output, evidence, label="receipt_output"
    )
    status = str(receipt["status"])
    if status == "completed":
        if runtime["http_status"] != 200 or response.get("done") is not True:
            raise AgentMissionError("runtime_success_transport_invalid")
        raw_result = response.get("response")
        if not isinstance(raw_result, str):
            raise AgentMissionError("runtime_response_result_missing")
        try:
            observed = json.loads(raw_result)
        except json.JSONDecodeError as exc:
            raise AgentMissionError("runtime_response_result_invalid") from exc
        if observed != output:
            raise AgentMissionError("runtime_response_result_mismatch")
        replay = _replay_local_ollama_request(
            request, timeout_seconds=route.wall_seconds
        )
        replay_raw = replay.get("response")
        try:
            replay_observed = (
                json.loads(replay_raw) if isinstance(replay_raw, str) else None
            )
        except json.JSONDecodeError:
            replay_observed = None
        if isinstance(replay_observed, dict):
            try:
                validate_reasoning_result(
                    replay_observed,
                    evidence,
                    label="runtime_replay",
                )
            except AgentMissionError as exc:
                raise AgentMissionError(
                    "runtime_independent_replay_mismatch"
                ) from exc
        observed_projection = _decision_projection(output)
        replay_projection = (
            _decision_projection(replay_observed)
            if isinstance(replay_observed, dict)
            else None
        )
        if (
            replay.get("done") is not True
            or replay.get("model") != runtime["model"]
            or replay_projection != observed_projection
        ):
            raise AgentMissionError("runtime_independent_replay_mismatch")
    elif output.get("verdict") != "abstain":
        raise AgentMissionError("runtime_failure_must_abstain")

    return [
        *[name for name, ok in checks.items() if ok],
        *result_checks,
        "time_order",
        "runtime_artifact_paths",
        "runtime_artifact_hashes",
        "preflight_contract",
        "request_contract",
        "result_hash",
        "transport_result_binding",
        "independent_transport_replay",
        "evidence_semantics",
    ]


def _decision_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    """Project model output onto the fields that can change mission authority.

    A seeded local model can legitimately vary explanatory prose, confidence,
    hypothesis count, evidence selection, and the wording of a recommendation
    between transport replays. Those fields remain schema/evidence validated
    separately. Recommendations are non-executable A2 evidence; actual
    authority is bounded by the risk class and the mandatory approval bit.
    Replay agreement is therefore required on the decision and those
    mechanically enforceable action boundaries, not on prose.
    """
    actions: list[dict[str, Any]] = []
    raw_actions = value.get("recommended_actions", [])
    if isinstance(raw_actions, list):
        for action in raw_actions:
            if not isinstance(action, Mapping):
                continue
            actions.append(
                {
                    "risk_class": action.get("risk_class"),
                    "requires_human_approval": action.get(
                        "requires_human_approval"
                    ),
                }
            )
    return {
        "schema": value.get("schema"),
        "verdict": value.get("verdict"),
        "severity": value.get("severity"),
        "recommended_actions": actions,
    }


def complete_handoff(
    receipt: Mapping[str, Any],
    *,
    route: NervesRouteConfig = ADA_ROUTE,
    now: datetime | None = None,
) -> CompletionResult:
    mission_id = str(receipt.get("mission_id") or "")
    lock_fd = _open_lock(
        route.state_path.with_suffix(route.state_path.suffix + ".lock")
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        state = _load_state(route.state_path)
        deliveries = dict(state["deliveries"])
        record = deliveries.get(mission_id)
        if not isinstance(record, dict):
            raise AgentMissionError("receipt_unknown_mission")
        receipt_path = Path(str(record["receipt_path"]))
        existing = record.get("receipt")
        if existing is not None:
            current, raw = _secure_json(receipt_path, label="receipt")
            if current != dict(receipt):
                raise AgentMissionError("terminal_receipt_mismatch")
            return CompletionResult(
                mission_id,
                str(record["status"]),
                _sha256(raw),
                False,
            )
        if record.get("status") != "claimed":
            raise AgentMissionError("receipt_mission_not_claimed")
        receipt_to_commit = dict(receipt)
        receipt_raw: bytes | None = None
        if receipt_path.exists():
            orphan, orphan_raw = _secure_json(
                receipt_path, label="orphan_receipt"
            )
            try:
                checks = _validate_receipt_document(
                    orphan, record=record, route=route
                )
            except AgentMissionError as exc:
                record = dict(record)
                record["status"] = "failed"
                record["receipt"] = {
                    "path": str(receipt_path),
                    "sha256": _sha256(orphan_raw),
                    "accepted_at": (
                        now or datetime.now(timezone.utc)
                    ).isoformat(),
                    "checks": ["invalid_orphan_receipt"],
                    "invalid_orphan": True,
                    "reason": str(exc)[:500],
                }
                deliveries[mission_id] = record
                _write_state_atomic(
                    route.state_path, _state_body(deliveries)
                )
                raise AgentMissionError(
                    "orphan_receipt_invalid_closed_failed"
                ) from exc
            receipt_to_commit = orphan
            receipt_raw = orphan_raw
        else:
            checks = _validate_receipt_document(
                receipt_to_commit, record=record, route=route
            )
        status = str(receipt_to_commit["status"])
        if status not in STATE_TERMINAL:
            raise AgentMissionError("receipt_status_not_terminal")
        if receipt_raw is None:
            created = _write_private_json(receipt_path, receipt_to_commit)
            if not created:
                raise AgentMissionError("receipt_create_race")
            raw = _canonical_bytes(receipt_to_commit) + b"\n"
        else:
            raw = receipt_raw
        receipt_sha256 = _sha256(raw)
        record = dict(record)
        record["status"] = status
        record["receipt"] = {
            "path": str(receipt_path),
            "sha256": receipt_sha256,
            "accepted_at": (now or datetime.now(timezone.utc)).isoformat(),
            "checks": checks,
            "adopted_orphan": receipt_raw is not None,
        }
        deliveries[mission_id] = record
        _write_state_atomic(route.state_path, _state_body(deliveries))
        return CompletionResult(
            mission_id, status, receipt_sha256, True
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def load_delivery(
    mission_id: str, *, route: NervesRouteConfig = ADA_ROUTE
) -> dict[str, Any]:
    state = _load_state(route.state_path)
    record = state["deliveries"].get(mission_id)
    if not isinstance(record, dict):
        raise AgentMissionError("delivery_not_found")
    return dict(record)


def stale_claim_mission_ids(
    *,
    route: NervesRouteConfig = ADA_ROUTE,
    now: datetime | None = None,
) -> list[str]:
    """Return expired claimed missions without mutating or reassigning them."""
    current = now or datetime.now(timezone.utc)
    state = _load_state(route.state_path)
    stale: list[tuple[str, str]] = []
    for mission_id, record in state.get("deliveries", {}).items():
        if not isinstance(record, dict) or record.get("status") != "claimed":
            continue
        claim = record.get("claim")
        if not isinstance(claim, dict):
            raise AgentMissionError("claimed_record_missing_claim")
        raw_expiry = str(claim.get("lease_expires_at") or "")
        try:
            expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AgentMissionError("claim_lease_invalid") from exc
        if expiry <= current:
            stale.append((str(claim.get("claimed_at") or ""), str(mission_id)))
    stale.sort()
    return [mission_id for _, mission_id in stale]
