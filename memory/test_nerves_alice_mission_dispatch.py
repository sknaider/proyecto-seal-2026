from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

from memory.nerves_agent_mission_core import (
    ALICE_ROUTE,
    AgentMissionError,
    compile_alice_orion_mission,
    deliver_handoff,
    claim_handoff,
    load_delivery,
)
from memory.nerves_local_sidecar import (
    pending_mission_ids,
    recoverable_claim_mission_ids,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seal_nerves as nerves


def _artifact(workspace: Path, record: dict) -> Path:
    path = workspace / "research/orion-status.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return path


def _route(tmp_path: Path):
    return replace(
        ALICE_ROUTE,
        inbox_dir=tmp_path / "inbox",
        runtime_artifact_dir=tmp_path / "runtime",
        state_path=tmp_path / "state.json",
        live_feed=tmp_path / "live.jsonl",
    )


def _failure() -> dict:
    return {
        "ts": "2026-07-24T03:00:00+00:00",
        "status": "FAIL",
        "service": "inactive",
        "login_http": 0,
        "backup_age_min": 10.0,
        "db_user": "unknown",
        "counts": {},
        "baseline": {},
        "fails": ["servicio orion-exam no activo (inactive)"],
    }


def test_alice_compile_delivery_is_idempotent_and_route_isolated(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    route = _route(tmp_path)
    compiled = compile_alice_orion_mission(
        _artifact(workspace, _failure()),
        route=route,
        ledger_path=tmp_path / "ledger.jsonl",
        manifest_dir=tmp_path / "manifests",
        bundle_dir=tmp_path / "bundles",
        workspace_root=workspace,
    )
    assert compiled.mission["agent"] == "ALICE"
    assert compiled.mission["specialty"] == "orion_product_reliability"
    assert compiled.mission["allowed_tools"] == []
    evidence = json.loads(compiled.evidence_path.read_text())
    assert evidence["action"] == "orion_product_pulse"
    first = deliver_handoff(compiled, route=route, notify_live=False)
    second = deliver_handoff(compiled, route=route, notify_live=False)
    assert first.created is True
    assert second.created is False
    assert pending_mission_ids(route) == [first.mission_id]
    assert load_delivery(first.mission_id, route=route)["status"] == "pending"


def test_alice_clean_does_not_compile_and_remediated_requires_diagnosis(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    route = _route(tmp_path)
    clean = _failure() | {
        "status": "OK",
        "service": "active",
        "login_http": 200,
        "db_user": "svc_orion_exam",
        "fails": [],
    }
    with pytest.raises(
        AgentMissionError, match="alice_artifact_has_no_actionable_episode"
    ):
        compile_alice_orion_mission(
            _artifact(workspace, clean),
            route=route,
            ledger_path=tmp_path / "ledger-clean.jsonl",
            manifest_dir=tmp_path / "manifests-clean",
            bundle_dir=tmp_path / "bundles-clean",
            workspace_root=workspace,
        )

    remediated = {
        "ts": "2026-07-24T03:01:00+00:00",
        "status": "REMEDIATED",
        "service": "active",
        "login_http": 200,
        "backup_age_min": 10.0,
        "counts": {},
        "baseline": {},
        "post_remediation": True,
        "actions": [
            {
                "issue": "service down",
                "action": "restart orion-exam",
                "verified_ok": True,
            }
        ],
        "escalations": [],
        "fails_original": ["service down"],
    }
    compiled = compile_alice_orion_mission(
        _artifact(workspace, remediated),
        route=route,
        ledger_path=tmp_path / "ledger-remediated.jsonl",
        manifest_dir=tmp_path / "manifests-remediated",
        bundle_dir=tmp_path / "bundles-remediated",
        workspace_root=workspace,
    )
    evidence = json.loads(compiled.evidence_path.read_text())
    identity = next(
        item
        for item in evidence["checks"]
        if item["evidence_id"] == "orion:db-identity"
    )
    assert identity == {
        "evidence_id": "orion:db-identity",
        "kind": "database_identity",
        "ok": False,
        "value": "not_rechecked",
    }


def test_alice_handoff_dispatches_once_and_stays_publicly_silent(
    monkeypatch,
):
    engine = nerves.MotivationEngine("ALICE")
    maintenance_calls = []
    dispatch_calls = []
    posts = []

    async def maintenance(received):
        maintenance_calls.append(received.agent)
        return "entrega ORION: servicio caído"

    def dispatch():
        dispatch_calls.append("delivered")
        return object()

    async def post(message, to="equipo"):
        posts.append((message, to))

    monkeypatch.setattr(nerves, "NERVES_AGENT_HANDOFF", True)
    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(nerves, "_dispatch_alice_read_only_mission", dispatch)
    monkeypatch.setattr(engine, "_post_chat", post)

    first = asyncio.run(engine._fire_useful_maintenance())
    second = asyncio.run(engine._fire_useful_maintenance())
    assert first == second == "maintenance_fired:value:ALICE"
    assert maintenance_calls == ["ALICE"]
    assert dispatch_calls == ["delivered"]
    assert posts == []


def test_alice_claim_with_private_response_is_recoverable(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    route = _route(tmp_path)
    compiled = compile_alice_orion_mission(
        _artifact(workspace, _failure()),
        route=route,
        ledger_path=tmp_path / "ledger.jsonl",
        manifest_dir=tmp_path / "manifests",
        bundle_dir=tmp_path / "bundles",
        workspace_root=workspace,
    )
    delivery = deliver_handoff(compiled, route=route, notify_live=False)
    claim_handoff(
        delivery.mission_id,
        worker_id="local-ollama:recoverable-worker",
        route=route,
    )
    run_dir = route.runtime_artifact_dir / delivery.mission_id
    run_dir.mkdir(parents=True, mode=0o700)
    response = run_dir / "ollama-response.json"
    response.write_text('{"done":true}\n', encoding="utf-8")
    response.chmod(0o600)
    assert recoverable_claim_mission_ids(route) == [delivery.mission_id]
    assert pending_mission_ids(route) == []
