from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from memory.nerves_agent_mission_core import (
    NEXUS_ROUTE,
    AgentMissionError,
    claim_handoff,
    compile_nexus_security_mission,
    deliver_handoff,
)
from memory.nerves_local_sidecar import recoverable_claim_mission_ids

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seal_nerves as nerves


def _route(tmp_path: Path):
    return replace(
        NEXUS_ROUTE,
        inbox_dir=tmp_path / "inbox",
        runtime_artifact_dir=tmp_path / "runtime",
        state_path=tmp_path / "state.json",
        live_feed=tmp_path / "live.jsonl",
    )


def _record(*, findings=None, state="FINDING", ts="2026-07-24T03:00:00+00:00"):
    return {
        "ts": ts,
        "agent": "NEXUS",
        "action": "security_pulse",
        "action_source": "tools/nexus_nerves_watch.py",
        "state": state,
        "status": "issue",
        "findings": findings or ["security daemon inactive"],
        "broken": ["instrument unavailable"] if state == "BROKEN" else [],
        "detail": (
            "controles=3/3 daemons=1/2 "
            "memory_monitor=healthy cred=0o600"
        ),
    }


def _artifact(workspace: Path, records: list[dict]) -> Path:
    path = workspace / "research/nexus-security.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    return path


def _compile(tmp_path: Path, records: list[dict]):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    route = _route(tmp_path)
    compiled = compile_nexus_security_mission(
        _artifact(workspace, records),
        route=route,
        ledger_path=tmp_path / "ledger.jsonl",
        manifest_dir=tmp_path / "manifests",
        bundle_dir=tmp_path / "bundles",
        workspace_root=workspace,
    )
    return route, compiled


def test_nexus_compile_delivery_is_idempotent_and_route_isolated(tmp_path):
    route, compiled = _compile(tmp_path, [_record()])
    first = deliver_handoff(compiled, route=route, notify_live=False)
    second = deliver_handoff(compiled, route=route, notify_live=False)
    assert compiled.mission["agent"] == "NEXUS"
    assert compiled.mission["allowed_tools"] == []
    assert first.created is True
    assert second.created is False
    with pytest.raises(AgentMissionError, match="route_mismatch"):
        deliver_handoff(compiled, route=replace(route, agent="ALICE"))


def test_nexus_green_is_silent_and_changed_finding_creates_transition(tmp_path):
    clean = _record() | {
        "state": "GREEN",
        "status": "clean",
        "findings": [],
        "broken": [],
        "detail": (
            "controles=3/3 daemons=2/2 "
            "memory_monitor=healthy cred=0o600"
        ),
    }
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    route = _route(tmp_path)
    path = _artifact(workspace, [clean])
    with pytest.raises(
        AgentMissionError, match="nexus_artifact_has_no_actionable_episode"
    ):
        compile_nexus_security_mission(
            path,
            route=route,
            ledger_path=tmp_path / "ledger.jsonl",
            manifest_dir=tmp_path / "manifests",
            bundle_dir=tmp_path / "bundles",
            workspace_root=workspace,
        )

    first_record = _record()
    path = _artifact(workspace, [clean, first_record, first_record])
    first = compile_nexus_security_mission(
        path,
        route=route,
        ledger_path=tmp_path / "ledger.jsonl",
        manifest_dir=tmp_path / "manifests",
        bundle_dir=tmp_path / "bundles",
        workspace_root=workspace,
    )
    changed = _record(
        findings=["security daemon inactive", "control marker absent"],
        ts="2026-07-24T03:01:00+00:00",
    )
    path = _artifact(workspace, [clean, first_record, first_record, changed])
    second = compile_nexus_security_mission(
        path,
        route=route,
        ledger_path=tmp_path / "ledger.jsonl",
        manifest_dir=tmp_path / "manifests",
        bundle_dir=tmp_path / "bundles",
        workspace_root=workspace,
    )
    assert first.mission["mission_id"] != second.mission["mission_id"]


def test_nexus_claim_with_response_is_recoverable(tmp_path):
    route, compiled = _compile(tmp_path, [_record()])
    delivery = deliver_handoff(compiled, route=route, notify_live=False)
    claim_handoff(
        delivery.mission_id,
        worker_id="local-ollama:nexus-recovery",
        route=route,
    )
    run_dir = route.runtime_artifact_dir / delivery.mission_id
    run_dir.mkdir(parents=True, mode=0o700)
    response = run_dir / "ollama-response.json"
    response.write_text('{"done":true}\n', encoding="utf-8")
    response.chmod(0o600)
    assert recoverable_claim_mission_ids(route) == [delivery.mission_id]


def test_nexus_dispatch_alert_uses_stable_mission_idempotency(monkeypatch):
    engine = nerves.MotivationEngine("NEXUS")
    sent = []

    async def maintenance(received):
        return "FINDING: bounded security finding"

    def dispatch():
        return (
            SimpleNamespace(
                mission={"mission_id": "11111111-1111-4111-8111-111111111111"},
                evidence={"checks": [{"value": "FINDING"}]},
            ),
            SimpleNamespace(created=True),
        )

    async def send(*args, **kwargs):
        sent.append((args, kwargs))
        return {"ok": True}

    monkeypatch.setattr(nerves, "NERVES_AGENT_HANDOFF", True)
    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(nerves, "_dispatch_nexus_read_only_mission", dispatch)
    monkeypatch.setattr(nerves, "send_agent_message", send)
    result = asyncio.run(engine._fire_useful_maintenance())
    assert result == "maintenance_fired:value:NEXUS"
    assert len(sent) == 1
    assert sent[0][1]["idempotency_key"] == (
        "nerves_nexus_security_11111111-1111-4111-8111-111111111111"
    )


def test_nexus_alert_failure_fails_closed(monkeypatch):
    engine = nerves.MotivationEngine("NEXUS")

    async def maintenance(received):
        return "FINDING: bounded security finding"

    def dispatch():
        return (
            SimpleNamespace(
                mission={"mission_id": "11111111-1111-4111-8111-111111111111"}
            ),
            SimpleNamespace(created=True),
        )

    async def send(*args, **kwargs):
        raise RuntimeError("synthetic delivery failure")

    monkeypatch.setattr(nerves, "NERVES_AGENT_HANDOFF", True)
    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(nerves, "_dispatch_nexus_read_only_mission", dispatch)
    monkeypatch.setattr(nerves, "send_agent_message", send)
    with pytest.raises(nerves.NervesDeliveryError):
        asyncio.run(engine._fire_useful_maintenance())
    assert engine._maintenance_tick_result == (
        "maintenance_failed:NEXUS:mission_handoff"
    )


def test_nexus_joined_handoff_does_not_retry_public_alert(monkeypatch):
    engine = nerves.MotivationEngine("NEXUS")
    sent = []

    async def maintenance(received):
        return "FINDING: already delivered security finding"

    def dispatch():
        return (
            SimpleNamespace(
                mission={"mission_id": "11111111-1111-4111-8111-111111111111"},
                evidence={"checks": [{"value": "FINDING"}]},
            ),
            SimpleNamespace(created=False),
        )

    async def send(*args, **kwargs):
        sent.append((args, kwargs))

    monkeypatch.setattr(nerves, "NERVES_AGENT_HANDOFF", True)
    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(nerves, "_dispatch_nexus_read_only_mission", dispatch)
    monkeypatch.setattr(nerves, "send_agent_message", send)
    assert asyncio.run(engine._fire_useful_maintenance()) == (
        "maintenance_fired:value:NEXUS"
    )
    assert sent == []
