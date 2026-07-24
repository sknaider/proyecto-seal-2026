from __future__ import annotations

from dataclasses import replace
import json

import pytest

from memory import nerves_local_sidecar as sidecar
from memory.nerves_agent_mission_core import ADA_ROUTE, AgentMissionError


def _route(tmp_path):
    return replace(
        ADA_ROUTE,
        inbox_dir=tmp_path / "inbox",
        runtime_artifact_dir=tmp_path / "runtime",
        state_path=tmp_path / "state.json",
        live_feed=tmp_path / "live.jsonl",
    )


@pytest.mark.parametrize("delivery_status", ["claimed", "completed", "failed"])
def test_claim_race_is_an_idempotent_join(
    monkeypatch, capsys, tmp_path, delivery_status
):
    route = _route(tmp_path)
    record = {
        "status": delivery_status,
        "claim": {"claim_id": "claim-1"},
    }
    if delivery_status != "claimed":
        record["receipt"] = {"receipt_sha256": "a" * 64}

    monkeypatch.setattr(sidecar, "ROUTES", {"ADA": route})
    monkeypatch.setattr(sidecar, "recoverable_claim_mission_ids", lambda _: [])
    monkeypatch.setattr(sidecar, "stale_claim_mission_ids", lambda **_: [])
    monkeypatch.setattr(
        sidecar, "pending_mission_ids", lambda _: ["mission-1"]
    )
    monkeypatch.setattr(sidecar, "load_delivery", lambda *_, **__: record)
    monkeypatch.setattr(
        sidecar,
        "run_ollama_mission",
        lambda *_, **__: (_ for _ in ()).throw(
            AgentMissionError("handoff_not_claimable")
        ),
    )
    monkeypatch.setattr(
        sidecar,
        "fail_ollama_claim_validation",
        lambda *_, **__: pytest.fail("a joined claim must not be failed"),
    )

    assert sidecar.main(["--agent", "ADA"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["status"] == "joined"
    assert output["delivery_status"] == delivery_status
    assert output["joined_existing_claim"] is True


def test_non_claim_error_remains_fail_closed(monkeypatch, capsys, tmp_path):
    route = _route(tmp_path)
    record = {"status": "completed", "claim": {"claim_id": "claim-1"}}

    monkeypatch.setattr(sidecar, "ROUTES", {"ADA": route})
    monkeypatch.setattr(sidecar, "recoverable_claim_mission_ids", lambda _: [])
    monkeypatch.setattr(sidecar, "stale_claim_mission_ids", lambda **_: [])
    monkeypatch.setattr(
        sidecar, "pending_mission_ids", lambda _: ["mission-1"]
    )
    monkeypatch.setattr(sidecar, "load_delivery", lambda *_, **__: record)
    monkeypatch.setattr(
        sidecar,
        "run_ollama_mission",
        lambda *_, **__: (_ for _ in ()).throw(
            AgentMissionError("bundle_binding_mismatch")
        ),
    )

    assert sidecar.main(["--agent", "ADA"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["error"] == "bundle_binding_mismatch"


def test_claim_conflict_without_durable_claim_remains_fail_closed(
    monkeypatch, capsys, tmp_path
):
    route = _route(tmp_path)
    record = {"status": "pending", "claim": None}

    monkeypatch.setattr(sidecar, "ROUTES", {"ADA": route})
    monkeypatch.setattr(sidecar, "recoverable_claim_mission_ids", lambda _: [])
    monkeypatch.setattr(sidecar, "stale_claim_mission_ids", lambda **_: [])
    monkeypatch.setattr(
        sidecar, "pending_mission_ids", lambda _: ["mission-1"]
    )
    monkeypatch.setattr(sidecar, "load_delivery", lambda *_, **__: record)
    monkeypatch.setattr(
        sidecar,
        "run_ollama_mission",
        lambda *_, **__: (_ for _ in ()).throw(
            AgentMissionError("handoff_not_claimable")
        ),
    )

    assert sidecar.main(["--agent", "ADA"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["error"] == "handoff_not_claimable"
