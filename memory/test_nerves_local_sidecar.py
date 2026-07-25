from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading

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


def _terminal_record(
    tmp_path: Path,
    *,
    delivery_status: str,
    receipt_sha256: str | None = None,
) -> dict:
    receipt_path = tmp_path / "mission-1.receipt.json"
    receipt = {
        "mission_id": "mission-1",
        "status": delivery_status,
        "claim_id": "claim-1",
        "worker_id": "local-ollama:worker-1",
    }
    raw = (
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )
    receipt_path.write_bytes(raw)
    receipt_path.chmod(0o600)
    return {
        "mission_id": "mission-1",
        "status": delivery_status,
        "claim": {
            "claim_id": "claim-1",
            "worker_id": "local-ollama:worker-1",
        },
        "receipt_path": str(receipt_path),
        "receipt": {
            "path": str(receipt_path),
            "sha256": receipt_sha256 or hashlib.sha256(raw).hexdigest(),
        },
    }


@pytest.mark.parametrize("delivery_status", ["completed", "failed", "abstained"])
def test_claim_race_is_an_idempotent_join(
    monkeypatch, capsys, tmp_path, delivery_status
):
    route = _route(tmp_path)
    record = _terminal_record(
        tmp_path, delivery_status=delivery_status
    )

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
    assert output["receipt_sha256"] == record["receipt"]["sha256"]


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


def test_idle_reports_unmeasurable_claims_instead_of_plain_idle(
    monkeypatch, capsys, tmp_path
):
    route = _route(tmp_path)
    unmeasurable = [("mission-bad", "claim_lease_invalid")]

    monkeypatch.setattr(sidecar, "ROUTES", {"ADA": route})
    monkeypatch.setattr(sidecar, "recoverable_claim_mission_ids", lambda _: [])
    monkeypatch.setattr(sidecar, "stale_claim_mission_ids", lambda **_: [])
    monkeypatch.setattr(
        sidecar,
        "stale_claim_scan",
        lambda **_: ([], unmeasurable),
    )
    monkeypatch.setattr(sidecar, "pending_mission_ids", lambda _: [])

    assert sidecar.main(["--agent", "ADA"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "processed": 0,
        "status": "idle_with_unmeasurable",
        "unmeasurable_claims": [["mission-bad", "claim_lease_invalid"]],
    }


@pytest.mark.parametrize(
    "record",
    [
        {"status": "claimed", "claim": {"claim_id": "claim-1"}},
        {"status": "completed", "claim": {"claim_id": "claim-1"}},
    ],
)
def test_non_terminal_or_receiptless_conflict_is_not_joined(
    monkeypatch, capsys, tmp_path, record
):
    route = _route(tmp_path)
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


def test_terminal_join_rejects_corrupt_receipt_hash(
    monkeypatch, capsys, tmp_path
):
    route = _route(tmp_path)
    record = _terminal_record(
        tmp_path,
        delivery_status="completed",
        receipt_sha256="0" * 64,
    )
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


def test_completion_winning_claim_race_joins_after_durable_receipt(
    monkeypatch, capsys, tmp_path
):
    route = _route(tmp_path)
    record = {"status": "pending", "claim": None}
    entered = threading.Event()
    completed = threading.Event()

    def complete_other_worker():
        assert entered.wait(timeout=2)
        record.clear()
        record.update(
            _terminal_record(tmp_path, delivery_status="completed")
        )
        completed.set()

    def lose_claim(*_, **__):
        entered.set()
        assert completed.wait(timeout=2)
        raise AgentMissionError("handoff_not_claimable")

    monkeypatch.setattr(sidecar, "ROUTES", {"ADA": route})
    monkeypatch.setattr(sidecar, "recoverable_claim_mission_ids", lambda _: [])
    monkeypatch.setattr(sidecar, "stale_claim_mission_ids", lambda **_: [])
    monkeypatch.setattr(
        sidecar, "pending_mission_ids", lambda _: ["mission-1"]
    )
    monkeypatch.setattr(sidecar, "load_delivery", lambda *_, **__: dict(record))
    monkeypatch.setattr(sidecar, "run_ollama_mission", lose_claim)

    winner = threading.Thread(target=complete_other_worker)
    winner.start()
    assert sidecar.main(["--agent", "ADA"]) == 0
    winner.join(timeout=2)
    assert not winner.is_alive()
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "joined"
    assert output["delivery_status"] == "completed"
