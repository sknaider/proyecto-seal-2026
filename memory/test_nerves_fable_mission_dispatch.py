from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from memory.nerves_agent_mission_core import (
    FABLE_ROUTE,
    AgentMissionError,
    compile_fable_rigor_mission,
    deliver_handoff,
)


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _event(
    *,
    ts: str,
    state: str = "FINDING",
    reasons: tuple[str, ...] = ("receipt_hash_mismatch",),
) -> dict:
    status = "clean" if state == "GREEN" else "issue"
    checks = []
    for index, reason in enumerate(reasons):
        checks.append(
            {
                "evidence_id": f"rigor:receipt:ada:m{index}",
                "kind": "direct_effect",
                "required": True,
                "status": "FAIL" if state == "FINDING" else "PASS",
                "reason_code": reason,
                "expected": {"sha256_match": True},
                "observed": {"sha256_match": state == "GREEN"},
                "source_ref": (
                    "research/flywire_results/"
                    f"nerves_orchestrator_inbox/ADA/m{index}.receipt.json"
                ),
            }
        )
    event = {
        "schema": "seal.fable.rigor-claim-audit.v2",
        "ts": ts,
        "agent": "FABLE",
        "action": "rigor_pulse",
        "target": "rigor_claim_audit",
        "action_source": "fable/rigor_claim_audit.py",
        "contract_rev": "2",
        "run_id": ts,
        "state": state,
        "status": status,
        "claim": {
            "claim_id": "a" * 64,
            "kind": "nerves_receipt_integrity",
            "subject_ref": (
                "research/flywire_results/nerves_orchestrator_inbox"
            ),
            "source_record_sha256": "b" * 64,
            "assertion": "Bounded claim.",
        },
        "calibration": {
            "probe_id": "receipt-hash-discriminant-v1",
            "probe_sha256": "c" * 64,
            "status": "PASS",
            "positive_control_id": "known-canonical-receipt",
            "negative_control_id": "single-byte-corruption",
        },
        "checks": checks,
        "findings": sorted(reasons) if state == "FINDING" else [],
        "broken": [],
        "detail": f"state={state}",
    }
    event["event_id"] = hashlib.sha256(_canonical(event)).hexdigest()
    return event


def _write(path: Path, events: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"".join(_canonical(event) + b"\n" for event in events)
    )
    path.chmod(0o600)
    return path


def _route(tmp_path: Path):
    return replace(
        FABLE_ROUTE,
        inbox_dir=tmp_path / "inbox",
        runtime_artifact_dir=tmp_path / "runtime",
        state_path=tmp_path / "state.json",
        live_feed=tmp_path / "live.jsonl",
    )


def _compile(tmp_path: Path, events: list[dict], suffix: str):
    workspace = tmp_path / f"workspace-{suffix}"
    workspace.mkdir()
    route = _route(tmp_path / suffix)
    artifact = _write(workspace / "research/rigor.jsonl", events)
    compiled = compile_fable_rigor_mission(
        artifact,
        route=route,
        ledger_path=tmp_path / suffix / "missions.jsonl",
        manifest_dir=tmp_path / suffix / "manifests",
        bundle_dir=tmp_path / suffix / "bundles",
        workspace_root=workspace,
    )
    return route, compiled


def test_fable_compile_delivery_is_private_toolless_and_idempotent(tmp_path):
    route, compiled = _compile(
        tmp_path,
        [_event(ts="2026-07-24T03:00:00+00:00")],
        "base",
    )
    assert compiled.mission["agent"] == "FABLE"
    assert compiled.mission["drive"] == "proactive"
    assert compiled.mission["allowed_tools"] == []
    first = deliver_handoff(compiled, route=route, notify_live=False)
    second = deliver_handoff(compiled, route=route, notify_live=False)
    assert first.created is True
    assert second.created is False
    assert first.handoff_path.stat().st_mode & 0o777 == 0o600


def test_fable_transition_dedup_join_change_and_clean_reopen(tmp_path):
    a1 = _event(ts="2026-07-24T03:00:00+00:00")
    a2 = _event(ts="2026-07-24T03:05:00+00:00")
    _, repeated = _compile(tmp_path, [a1, a2], "repeat")
    _, first = _compile(tmp_path, [a1], "first")
    assert repeated.mission["mission_id"] == first.mission["mission_id"]

    changed = _event(
        ts="2026-07-24T03:10:00+00:00",
        reasons=("receipt_hash_mismatch", "verifier_not_accepted"),
    )
    _, changed_compiled = _compile(
        tmp_path, [a1, a2, changed], "changed"
    )
    assert changed_compiled.mission["mission_id"] != first.mission["mission_id"]

    clean = _event(
        ts="2026-07-24T03:15:00+00:00",
        state="GREEN",
        reasons=("receipt_consistent",),
    )
    reopened = _event(ts="2026-07-24T03:20:00+00:00")
    _, reopened_compiled = _compile(
        tmp_path, [a1, clean, reopened], "reopened"
    )
    assert reopened_compiled.mission["mission_id"] != first.mission["mission_id"]


def test_fable_clean_is_silent_and_weak_file_modes_fail_closed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    route = _route(tmp_path)
    artifact = _write(
        workspace / "research/rigor.jsonl",
        [
            _event(
                ts="2026-07-24T03:00:00+00:00",
                state="GREEN",
                reasons=("receipt_consistent",),
            )
        ],
    )
    with pytest.raises(
        AgentMissionError, match="no_actionable_episode"
    ):
        compile_fable_rigor_mission(
            artifact,
            route=route,
            ledger_path=tmp_path / "missions.jsonl",
            manifest_dir=tmp_path / "manifests",
            bundle_dir=tmp_path / "bundles",
            workspace_root=workspace,
        )
    artifact.chmod(0o644)
    with pytest.raises(AgentMissionError, match="mode_must_be_600"):
        compile_fable_rigor_mission(
            artifact,
            route=route,
            ledger_path=tmp_path / "missions2.jsonl",
            manifest_dir=tmp_path / "manifests2",
            bundle_dir=tmp_path / "bundles2",
            workspace_root=workspace,
        )
