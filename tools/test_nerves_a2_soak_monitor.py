from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from tools import nerves_a2_soak_monitor as soak


NOW = datetime(2026, 7, 24, 18, 0, tzinfo=timezone.utc)


def _write_private(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _root(tmp_path: Path) -> Path:
    for relative in soak.RELEASE_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative + "\n", encoding="utf-8")
    return tmp_path


def _show(_unit: str, prop: str) -> str:
    return {
        "ActiveState": "active",
        "UnitFileState": "enabled",
        "LastTriggerUSec": "Fri 2026-07-24 17:59:00 UTC",
        "Result": "success",
        "ExecMainStatus": "0",
        "FragmentPath": f"/unit/{_unit}",
        "DropInPaths": "",
        "Environment": "SEAL_NERVES_USEFUL=1",
        "EnvironmentFiles": "",
    }[prop]


def _cat(unit: str) -> str:
    return f"# {unit}\n[Unit]\nDescription={unit}\n"


def _route_receipt(
    root: Path,
    *,
    agent: str,
    mission_id: str,
    started_at: datetime,
) -> Path:
    runtime = (
        {
            "platform": "claude_code_agent_tool",
            "profile": "nerves-jarvis-reasoner",
            "tools_configured": ["SendMessage"],
            "tool_events": ["SendMessage:main"],
            "skills_configured": [],
            "mcp_servers_configured": [],
            "max_turns": 1,
        }
        if agent == "JARVIS"
        else {
            "platform": "ollama_generate_json",
            "isolation": "no_tool_api",
            "tool_events": [],
            "endpoint": "http://127.0.0.1:11434/api/generate",
        }
    )
    return _write_private(
        root / "attestations" / f"{agent.lower()}.receipt.json",
        {
            "schema": (
                "seal.nerves.orchestrator-receipt.v1"
                if agent == "JARVIS"
                else "seal.nerves.orchestrator-receipt.v3"
            ),
            "mission_id": mission_id,
            "status": "completed",
            "started_at": started_at.isoformat(),
            "finished_at": (started_at + timedelta(seconds=1)).isoformat(),
            "worker_kind": (
                "native_subagent"
                if agent == "JARVIS"
                else "local_ollama_subagent"
            ),
            "tools_used": ["SendMessage"] if agent == "JARVIS" else [],
            "verifier_verdict": (
                {"verdict": "accepted"} if agent == "JARVIS" else {}
            ),
            "runtime_attestation": runtime,
        },
    )


def _canary(
    path: Path,
    root: Path,
    fingerprint: str,
    recorded_at: datetime,
    *,
    agent: str = "ADA",
    harm_caused: int = 0,
) -> None:
    mission_id = {
        "ADA": "11111111-1111-4111-8111-111111111111",
        "ALICE": "22222222-2222-4222-8222-222222222222",
        "NEXUS": "33333333-3333-4333-8333-333333333333",
        "FABLE": "44444444-4444-4444-8444-444444444444",
        "JARVIS": "55555555-5555-4555-8555-555555555555",
    }[agent]
    receipt = _route_receipt(
        root,
        agent=agent,
        mission_id=mission_id,
        started_at=recorded_at,
    )
    receipt_sha = hashlib.sha256(receipt.read_bytes()).hexdigest()
    value = {
        "schema": soak.CANARY_SCHEMA,
        "canary_id": f"real-a2-canary-{agent.lower()}",
        "release_fingerprint": fingerprint,
        "recorded_at": (recorded_at + timedelta(seconds=2)).isoformat(),
        "kind": "A2_ROUTE_CANARY",
        "ok": True,
        "risk_class": "A2_READ_ONLY",
        "mutations": 0,
        "evidence_sha256": receipt_sha,
        "mission_id": mission_id,
        "agent": agent,
        "signals_seen": 1,
        "missions_created": 1,
        "missions_coalesced": 0,
        "duplicate_side_effects": 0,
        "worker_successes": 1,
        "worker_failures": 0,
        "evidence_attempts": 1,
        "evidence_rejections": 0,
        "false_wakes": 0,
        "dead_letters": 0,
        "verified_effects": 1,
        "estimated_cost_units": 1,
        "predicted_utility": 1.0,
        "actual_utility": 1.0,
        "utility_basis": "controlled_route_completion",
        "confidence": 1.0,
        "outcome": 1,
        "human_corrections": 0,
        "harm_avoided": 0,
        "harm_caused": harm_caused,
        "harm_avoided_method": "sentinel_hash_unchanged",
        "principal_ack_latency_ms": None,
        "attestation_kind": (
            "jarvis_native_receipt"
            if agent == "JARVIS"
            else "local_ollama_receipt"
        ),
        "attestation_path": str(receipt.resolve()),
        "attestation_sha256": receipt_sha,
        "attested_started_at": recorded_at.isoformat(),
        "attested_finished_at": (recorded_at + timedelta(seconds=1)).isoformat(),
    }
    _write_private(path, value)


def _ack(
    path: Path,
    root: Path,
    fingerprint: str,
    recorded_at: datetime,
) -> None:
    evidence = _write_private(
        root / "attestations" / "ack.json",
        {
            "schema": "seal.nerves.principal-ack-evidence.v1",
            "channel": "web_chat",
            "request_id": 1,
            "request_legacy_id": "request",
            "request_created_at": recorded_at.isoformat(),
            "ack_id": 2,
            "ack_legacy_id": "ack",
            "ack_created_at": (recorded_at + timedelta(milliseconds=474)).isoformat(),
            "in_reply_to": "request",
        },
    )
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    value = {
        "schema": soak.CANARY_SCHEMA,
        "canary_id": "principal-ack",
        "release_fingerprint": fingerprint,
        "recorded_at": (recorded_at + timedelta(seconds=1)).isoformat(),
        "kind": "PRINCIPAL_ACK_CANARY",
        "ok": True,
        "risk_class": "A2_READ_ONLY",
        "mutations": 0,
        "evidence_sha256": digest,
        "mission_id": None,
        "agent": "ADA",
        "signals_seen": 1,
        "missions_created": 0,
        "missions_coalesced": 0,
        "duplicate_side_effects": 0,
        "worker_successes": 0,
        "worker_failures": 0,
        "evidence_attempts": 1,
        "evidence_rejections": 0,
        "false_wakes": 0,
        "dead_letters": 0,
        "verified_effects": 1,
        "estimated_cost_units": 0,
        "predicted_utility": 1.0,
        "actual_utility": 1.0,
        "utility_basis": "measured_principal_ack_latency",
        "confidence": 1.0,
        "outcome": 1,
        "human_corrections": 0,
        "harm_avoided": 0,
        "harm_caused": 0,
        "harm_avoided_method": "principal_ack_under_2s",
        "principal_ack_latency_ms": 474,
        "attestation_kind": "principal_ack_evidence",
        "attestation_path": str(evidence.resolve()),
        "attestation_sha256": digest,
        "attested_started_at": recorded_at.isoformat(),
        "attested_finished_at": (
            recorded_at + timedelta(milliseconds=474)
        ).isoformat(),
    }
    _write_private(path, value)


def test_soak_starts_private_and_passes_only_after_24h_with_canaries(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path / "workspace")
    state_path = root / "state/state.json"
    canary_dir = root / "canaries"
    first = soak.sample(
        release_id="release-1",
        state_path=state_path,
        canary_dir=canary_dir,
        root=root,
        show=_show,
        cat_unit=_cat,
        now=NOW,
    )
    assert first["status"] == "SOAKING"
    assert state_path.stat().st_mode & 0o777 == 0o600
    for agent in soak.REQUIRED_A2_ROUTES:
        _canary(
            canary_dir / f"{agent.lower()}.json",
            root,
            first["release_fingerprint"],
            NOW,
            agent=agent,
        )
    _ack(canary_dir / "ack.json", root, first["release_fingerprint"], NOW)
    final = soak.sample(
        release_id="release-1",
        state_path=state_path,
        canary_dir=canary_dir,
        root=root,
        show=_show,
        cat_unit=_cat,
        now=NOW + timedelta(hours=24, seconds=1),
    )
    assert final["status"] == "PASSED"
    assert final["last_sample"]["non_vacuous_canaries"] == 6
    assert final["last_sample"]["metrics"]["principal_ack_p95_ms"] == 474
    assert final["last_sample"]["metrics"]["routes_observed"] == [
        "ADA",
        "ALICE",
        "FABLE",
        "JARVIS",
        "NEXUS",
    ]


def test_failed_unit_is_sticky(tmp_path: Path) -> None:
    root = _root(tmp_path / "workspace")
    state_path = root / "state/state.json"

    def failed_show(unit: str, prop: str) -> str:
        if unit == "seal-nerves-daemon.service" and prop == "Result":
            return "exit-code"
        return _show(unit, prop)

    state = soak.sample(
        release_id="release-1",
        state_path=state_path,
        canary_dir=root / "canaries",
        root=root,
        show=failed_show,
        cat_unit=_cat,
        now=NOW,
    )
    assert state["status"] == "FAILED"
    later = soak.sample(
        release_id="release-1",
        state_path=state_path,
        canary_dir=root / "canaries",
        root=root,
        show=_show,
        cat_unit=_cat,
        now=NOW + timedelta(minutes=5),
    )
    assert later["status"] == "FAILED"


def test_release_and_runtime_drift_fail_closed(tmp_path: Path) -> None:
    root = _root(tmp_path / "workspace")
    state_path = root / "state/state.json"
    soak.sample(
        release_id="release-1",
        state_path=state_path,
        canary_dir=root / "canaries",
        root=root,
        show=_show,
        cat_unit=_cat,
        now=NOW,
    )
    (root / soak.RELEASE_FILES[0]).write_text("drift\n", encoding="utf-8")
    with pytest.raises(soak.SoakError, match="release_or_runtime_changed"):
        soak.sample(
            release_id="release-1",
            state_path=state_path,
            canary_dir=root / "canaries",
            root=root,
            show=_show,
            cat_unit=_cat,
            now=NOW + timedelta(minutes=5),
        )

    state_path.unlink()
    (root / soak.RELEASE_FILES[0]).write_text(
        soak.RELEASE_FILES[0] + "\n",
        encoding="utf-8",
    )
    soak.sample(
        release_id="release-2",
        state_path=state_path,
        canary_dir=root / "canaries",
        root=root,
        show=_show,
        cat_unit=_cat,
        now=NOW,
    )
    with pytest.raises(soak.SoakError, match="release_or_runtime_changed"):
        soak.sample(
            release_id="release-2",
            state_path=state_path,
            canary_dir=root / "canaries",
            root=root,
            show=_show,
            cat_unit=lambda unit: _cat(unit) + "# drift\n",
            now=NOW + timedelta(minutes=5),
        )


def test_release_fingerprint_covers_monitor_and_recorder(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path / "workspace")
    before = soak.release_fingerprint(root)
    monitor = root / "tools/nerves_a2_soak_monitor.py"
    monitor.write_text("changed monitor\n", encoding="utf-8")
    after_monitor = soak.release_fingerprint(root)
    assert after_monitor != before
    recorder = root / "tools/nerves_a2_canary_record.py"
    recorder.write_text("changed recorder\n", encoding="utf-8")
    assert soak.release_fingerprint(root) != after_monitor


def test_private_canary_boundary_and_attestation_are_required(tmp_path: Path) -> None:
    root = _root(tmp_path / "workspace")
    fingerprint = soak.release_fingerprint(root)
    canary = root / "canaries/bad.json"
    _canary(canary, root, fingerprint, NOW)
    value = json.loads(canary.read_text(encoding="utf-8"))
    value["mutations"] = 1
    _write_private(canary, value)
    with pytest.raises(soak.SoakError, match="canary_boundary_failed"):
        soak._canaries(NOW, fingerprint, canary_dir=canary.parent, root=root)

    value["mutations"] = 0
    value["attestation_sha256"] = "0" * 64
    _write_private(canary, value)
    with pytest.raises(soak.SoakError, match="attestation_digest"):
        soak._canaries(NOW, fingerprint, canary_dir=canary.parent, root=root)


def test_deadline_rejects_bad_metrics(tmp_path: Path) -> None:
    root = _root(tmp_path / "workspace")
    state_path = root / "state/state.json"
    canary_dir = root / "canaries"
    first = soak.sample(
        release_id="release-1",
        state_path=state_path,
        canary_dir=canary_dir,
        root=root,
        show=_show,
        cat_unit=_cat,
        now=NOW,
    )
    for agent in soak.REQUIRED_A2_ROUTES:
        _canary(
            canary_dir / f"{agent.lower()}.json",
            root,
            first["release_fingerprint"],
            NOW,
            agent=agent,
            harm_caused=1 if agent == "JARVIS" else 0,
        )
    _ack(canary_dir / "ack.json", root, first["release_fingerprint"], NOW)
    final = soak.sample(
        release_id="release-1",
        state_path=state_path,
        canary_dir=canary_dir,
        root=root,
        show=_show,
        cat_unit=_cat,
        now=NOW + timedelta(hours=24, seconds=1),
    )
    assert final["status"] == "FAILED"
    assert "harm_caused_nonzero" in final["last_sample"]["failures"]
    assert final["last_sample"]["metrics"]["harm_caused"] == 1


def test_terminal_worker_failure_after_start_is_sticky(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path / "workspace")
    state_path = root / "state/state.json"
    delivery_state = _write_private(
        root / "delivery/ADA.state.json",
        {
            "deliveries": {
                "11111111-1111-4111-8111-111111111111": {
                    "created_at": (NOW + timedelta(seconds=1)).isoformat(),
                    "status": "failed",
                }
            }
        },
    )
    result = soak.sample(
        release_id="release-1",
        state_path=state_path,
        canary_dir=root / "canaries",
        root=root,
        show=_show,
        cat_unit=_cat,
        delivery_states={"ADA": delivery_state},
        now=NOW,
    )
    assert result["status"] == "FAILED"
    assert result["last_sample"]["terminal_worker_failures"] == [
        "ADA:11111111-1111-4111-8111-111111111111"
    ]
