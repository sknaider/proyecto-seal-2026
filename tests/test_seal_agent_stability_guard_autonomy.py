from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import seal_agent_stability_guard as guard


def test_fable_memory_repo_boundary_accepts_local_repo_without_remote(
    tmp_path,
) -> None:
    repo = tmp_path / "memory"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    result = guard.check_fable_memory_repo_boundary(repo)

    assert result["ok"] is True
    assert result["status"] == "healthy"
    assert result["remote_count"] == 0
    assert result["remote_names"] == []


def test_fable_memory_repo_boundary_detects_real_remote(tmp_path) -> None:
    repo = tmp_path / "memory"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "origin",
            "file:///blocked",
        ],
        check=True,
    )

    result = guard.check_fable_memory_repo_boundary(repo)

    assert result["ok"] is False
    assert result["status"] == "drift"
    assert result["remote_count"] == 1
    assert result["remote_names"] == ["origin"]
    assert any("remotos prohibidos: origin" in issue for issue in result["issues"])


def test_fable_memory_repo_boundary_rejects_missing_repo(tmp_path) -> None:
    result = guard.check_fable_memory_repo_boundary(tmp_path / "missing")

    assert result["ok"] is False
    assert result["status"] == "drift"
    assert result["remote_count"] is None
    assert any("ausente" in issue for issue in result["issues"])


def policy_literal(name: str, value: object) -> str:
    return f"{name} = {value!r}"


def write_valid_files(root) -> None:
    (root / "scripts").mkdir()
    (root / "CLAUDE.md").write_text(
        "\n".join(guard.AUTONOMY_REQUIRED_CLAUSES), encoding="utf-8"
    )
    (root / "scripts/seal_send.py").write_text(
        "AUTONOMY BLOCKED approval_gate", encoding="utf-8"
    )
    (root / "scripts/seal_autonomy_guard.py").write_text(
        "def autonomy_warning(): pass", encoding="utf-8"
    )
    (root / "scripts/seal_self_repair.py").write_text(
        "\n".join(
            (
                policy_literal("AGENT_ACTIONS", guard.EXPECTED_SELF_REPAIR_ACTIONS),
                policy_literal("CONTROL_UNITS", guard.EXPECTED_SELF_REPAIR_CONTROLS),
                "# SEAL_AGENT is required; identity is fail-closed",
                "# negative_control_other_agent_unchanged",
                "# subject_matches_policy",
                "# autonomy_receipts",
                "# v1 only repairs continuously ",
                "# supervised Type=simple services",
            )
        ),
        encoding="utf-8",
    )


def test_autonomy_files_green_when_contract_and_blocker_are_present(tmp_path) -> None:
    write_valid_files(tmp_path)
    result = guard.check_autonomy_files(tmp_path)
    assert result == {"ok": True, "status": "healthy", "issues": []}


def test_autonomy_files_fail_on_legacy_rule(tmp_path) -> None:
    write_valid_files(tmp_path)
    contract = tmp_path / "CLAUDE.md"
    contract.write_text(
        contract.read_text(encoding="utf-8") + "\n" + guard.AUTONOMY_LEGACY_PHRASES[0],
        encoding="utf-8",
    )
    result = guard.check_autonomy_files(tmp_path)
    assert result["ok"] is False
    assert result["status"] == "drift"
    assert any("legacy" in issue for issue in result["issues"])


def test_autonomy_files_fail_when_self_repair_loses_negative_control(tmp_path) -> None:
    write_valid_files(tmp_path)
    repair = tmp_path / "scripts/seal_self_repair.py"
    repair.write_text(
        repair.read_text(encoding="utf-8").replace(
            "negative_control_other_agent_unchanged", "control_removed"
        ),
        encoding="utf-8",
    )

    result = guard.check_autonomy_files(tmp_path)

    assert result["ok"] is False
    assert any(
        "negative_control_other_agent_unchanged" in issue
        for issue in result["issues"]
    )


def test_autonomy_files_fail_when_action_authority_expands(tmp_path) -> None:
    write_valid_files(tmp_path)
    repair = tmp_path / "scripts/seal_self_repair.py"
    expanded = {
        **guard.EXPECTED_SELF_REPAIR_ACTIONS,
        "ADA": {
            **guard.EXPECTED_SELF_REPAIR_ACTIONS["ADA"],
            "foreign": "seal-chat.service",
        },
    }
    repair.write_text(
        repair.read_text(encoding="utf-8").replace(
            policy_literal("AGENT_ACTIONS", guard.EXPECTED_SELF_REPAIR_ACTIONS),
            policy_literal("AGENT_ACTIONS", expanded),
        ),
        encoding="utf-8",
    )

    result = guard.check_autonomy_files(tmp_path)

    assert result["ok"] is False
    assert any("tabla AGENT_ACTIONS cambió" in issue for issue in result["issues"])


def test_autonomy_contract_combines_file_and_database_drift(tmp_path, monkeypatch) -> None:
    write_valid_files(tmp_path)

    async def fake_governance():
        return {"ok": False, "status": "drift", "issues": ["db drift"]}

    monkeypatch.setattr(guard, "check_autonomy_governance", fake_governance)
    monkeypatch.setattr(
        guard,
        "check_autonomy_restart_provenance",
        lambda: {"ok": True, "status": "healthy", "issues": []},
    )
    result = asyncio.run(guard.check_autonomy_contract(tmp_path))
    assert result["ok"] is False
    assert result["status"] == "drift"
    assert result["issues"] == ["db drift"]


def test_autonomy_contract_surfaces_runtime_grace_as_pending(
    tmp_path, monkeypatch
) -> None:
    write_valid_files(tmp_path)

    async def fake_governance():
        return {"ok": True, "status": "healthy", "issues": []}

    monkeypatch.setattr(guard, "check_autonomy_governance", fake_governance)
    monkeypatch.setattr(
        guard,
        "check_autonomy_restart_provenance",
        lambda: {
            "ok": True,
            "status": "pending",
            "pending": ["seal-channel-monitor@ADA.service"],
            "issues": [],
        },
    )

    result = asyncio.run(guard.check_autonomy_contract(tmp_path))

    assert result["ok"] is True
    assert result["status"] == "pending"
    assert result["issues"] == []


def _runtime_snapshot(invocation_id: str, pid: int = 10) -> dict[str, object]:
    return {
        "load_state": "loaded",
        "active_state": "active",
        "sub_state": "running",
        "main_pid": pid,
        "invocation_id": invocation_id,
        "result": "success",
    }


def _write_verified_receipt(
    root: Path,
    *,
    agent: str,
    unit: str,
    before: str,
    after: str,
) -> None:
    directory = root / agent
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / f"{before}-{after}.json"
    path.write_text(
        json.dumps(
            {
                "schema": "seal.autonomy.repair-receipt.v1",
                "agent": agent,
                "unit": unit,
                "operation": "restart",
                "result": "verified",
                "before": {"invocation_id": before},
                "after": {"invocation_id": after},
                "checks": {
                    name: True for name in guard.AUTONOMY_RECEIPT_REQUIRED_CHECKS
                },
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_restart_provenance_initializes_private_baseline(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        guard,
        "_inspect_autonomy_unit",
        lambda unit: _runtime_snapshot(f"baseline-{unit}", 10),
    )
    state = tmp_path / "state" / "invocations.json"

    result = guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=tmp_path / "receipts",
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
        boot_id="boot-a",
    )

    assert result["ok"] is True
    assert result["baseline"] is True
    assert state.stat().st_mode & 0o777 == 0o600
    assert state.parent.stat().st_mode & 0o777 == 0o700


def test_restart_provenance_detects_direct_restart_after_grace(tmp_path, monkeypatch) -> None:
    state = tmp_path / "state.json"
    receipts = tmp_path / "receipts"
    current = {"value": "before"}
    monkeypatch.setattr(
        guard,
        "_inspect_autonomy_unit",
        lambda unit: _runtime_snapshot(f"{current['value']}-{unit}", 10),
    )
    started = datetime(2026, 7, 30, tzinfo=timezone.utc)
    guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=receipts,
        now=started,
        boot_id="boot-a",
        grace_seconds=30,
    )
    current["value"] = "after"

    pending = guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=receipts,
        now=started + timedelta(seconds=1),
        boot_id="boot-a",
        grace_seconds=30,
    )
    detected = guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=receipts,
        now=started + timedelta(seconds=31),
        boot_id="boot-a",
        grace_seconds=30,
    )

    assert pending["ok"] is True
    assert pending["status"] == "pending"
    assert len(pending["pending"]) == len(guard._autonomy_units())
    assert detected["ok"] is False
    assert len(detected["out_of_band"]) == len(guard._autonomy_units())
    assert all("reinicio sin recibo" in issue for issue in detected["issues"])


def test_restart_churn_cannot_reset_grace_window(tmp_path, monkeypatch) -> None:
    state = tmp_path / "state.json"
    receipts = tmp_path / "receipts"
    unit = guard.EXPECTED_SELF_REPAIR_ACTIONS["ADA"]["stream_relay"]
    current = {"value": "accepted"}
    monkeypatch.setattr(
        guard,
        "_inspect_autonomy_unit",
        lambda candidate: _runtime_snapshot(
            current["value"] if candidate == unit else f"stable-{candidate}",
            30,
        ),
    )
    started = datetime(2026, 7, 30, tzinfo=timezone.utc)
    guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=receipts,
        now=started,
        boot_id="boot-a",
        grace_seconds=30,
    )

    current["value"] = "restart-1"
    first = guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=receipts,
        now=started + timedelta(seconds=1),
        boot_id="boot-a",
        grace_seconds=30,
    )
    current["value"] = "restart-2"
    second = guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=receipts,
        now=started + timedelta(seconds=20),
        boot_id="boot-a",
        grace_seconds=30,
    )
    current["value"] = "restart-3"
    detected = guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=receipts,
        now=started + timedelta(seconds=32),
        boot_id="boot-a",
        grace_seconds=30,
    )

    assert first["status"] == "pending"
    assert second["status"] == "pending"
    assert detected["status"] == "drift"
    assert detected["out_of_band"] == [unit]
    payload = json.loads(state.read_text(encoding="utf-8"))["units"][unit]["pending"]
    assert payload["first_seen"] == (started + timedelta(seconds=1)).isoformat()
    assert payload["first_invocation_id"] == "restart-1"
    assert payload["invocation_id"] == "restart-3"
    assert payload["invocation_changes"] == 2
    assert payload["age_seconds"] == 31


def test_restart_provenance_accepts_verified_receipt_chain(tmp_path, monkeypatch) -> None:
    state = tmp_path / "state.json"
    receipts = tmp_path / "receipts"
    unit = guard.EXPECTED_SELF_REPAIR_ACTIONS["ADA"]["channel_monitor"]
    current = {"value": "old"}
    monkeypatch.setattr(
        guard,
        "_inspect_autonomy_unit",
        lambda candidate: _runtime_snapshot(
            current["value"] if candidate == unit else f"stable-{candidate}",
            11,
        ),
    )
    started = datetime(2026, 7, 30, tzinfo=timezone.utc)
    guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=receipts,
        now=started,
        boot_id="boot-a",
    )
    _write_verified_receipt(
        receipts,
        agent="ADA",
        unit=unit,
        before="old",
        after="middle",
    )
    _write_verified_receipt(
        receipts,
        agent="ADA",
        unit=unit,
        before="middle",
        after="new",
    )
    current["value"] = "new"

    result = guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=receipts,
        now=started + timedelta(seconds=60),
        boot_id="boot-a",
        grace_seconds=0,
    )

    assert result["ok"] is True
    assert result["authorized"] == [unit]
    assert result["out_of_band"] == []


def test_restart_provenance_rejects_plausible_but_invalid_receipt(tmp_path, monkeypatch) -> None:
    state = tmp_path / "state.json"
    receipts = tmp_path / "receipts"
    unit = guard.EXPECTED_SELF_REPAIR_ACTIONS["ADA"]["channel_monitor"]
    current = {"value": "old"}
    monkeypatch.setattr(
        guard,
        "_inspect_autonomy_unit",
        lambda candidate: _runtime_snapshot(
            current["value"] if candidate == unit else f"stable-{candidate}",
            12,
        ),
    )
    started = datetime(2026, 7, 30, tzinfo=timezone.utc)
    guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=receipts,
        now=started,
        boot_id="boot-a",
    )
    _write_verified_receipt(
        receipts,
        agent="ADA",
        unit=unit,
        before="old",
        after="new",
    )
    receipt = next((receipts / "ADA").glob("*.json"))
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["checks"]["by_effect_target_changed"] = "ok"
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    receipt.chmod(0o600)
    current["value"] = "new"

    result = guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=receipts,
        now=started + timedelta(seconds=1),
        boot_id="boot-a",
        grace_seconds=0,
    )

    assert result["ok"] is False
    assert result["out_of_band"] == [unit]


def test_restart_provenance_rebaselines_after_host_boot(tmp_path, monkeypatch) -> None:
    state = tmp_path / "state.json"
    current = {"value": "boot-a"}
    monkeypatch.setattr(
        guard,
        "_inspect_autonomy_unit",
        lambda unit: _runtime_snapshot(f"{current['value']}-{unit}", 13),
    )
    started = datetime(2026, 7, 30, tzinfo=timezone.utc)
    guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=tmp_path / "receipts",
        now=started,
        boot_id="boot-a",
    )
    current["value"] = "boot-b"

    result = guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=tmp_path / "receipts",
        now=started + timedelta(days=1),
        boot_id="boot-b",
        grace_seconds=0,
    )

    assert result["ok"] is True
    assert result["baseline"] is True
    assert result["out_of_band"] == []


def test_bypass_acknowledgement_is_owner_bound_durable_and_rearms(
    tmp_path, monkeypatch
) -> None:
    state = tmp_path / "state.json"
    receipts = tmp_path / "receipts"
    acknowledgements = tmp_path / "acknowledgements"
    unit = guard.EXPECTED_SELF_REPAIR_ACTIONS["ADA"]["channel_monitor"]
    current = {"value": "old"}
    monkeypatch.setattr(
        guard,
        "_inspect_autonomy_unit",
        lambda candidate: _runtime_snapshot(
            current["value"] if candidate == unit else f"stable-{candidate}",
            21,
        ),
    )
    started = datetime(2026, 7, 30, tzinfo=timezone.utc)
    guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=receipts,
        now=started,
        boot_id="boot-a",
    )
    current["value"] = "new"
    drift = guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=receipts,
        now=started + timedelta(seconds=31),
        boot_id="boot-a",
        grace_seconds=0,
    )
    assert drift["out_of_band"] == [unit]

    acknowledged = guard.acknowledge_autonomy_bypass(
        unit,
        reason="canario deliberado revisado por el owner",
        expected_accepted_invocation_id="old",
        expected_observed_invocation_id="new",
        actor="ADA",
        state_path=state,
        acknowledgement_root=acknowledgements,
        grace_seconds=30,
        now=started + timedelta(seconds=62),
    )

    ack_path = Path(acknowledged["acknowledgement"])
    assert acknowledged["result"] == "acknowledged"
    assert ack_path.stat().st_mode & 0o777 == 0o600
    assert ack_path.parent.stat().st_mode & 0o777 == 0o700
    payload = json.loads(ack_path.read_text(encoding="utf-8"))
    assert payload["accepted_invocation_id"] == "old"
    assert payload["observed_invocation_id"] == "new"

    healthy = guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=receipts,
        now=started + timedelta(seconds=63),
        boot_id="boot-a",
        grace_seconds=0,
    )
    assert healthy["status"] == "healthy"
    assert healthy["out_of_band"] == []

    current["value"] = "newer"
    second_bypass = guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=receipts,
        now=started + timedelta(seconds=64),
        boot_id="boot-a",
        grace_seconds=0,
    )
    assert second_bypass["status"] == "drift"
    assert second_bypass["out_of_band"] == [unit]


def test_bypass_acknowledgement_rejects_foreign_owner_without_mutation(
    tmp_path, monkeypatch
) -> None:
    state = tmp_path / "state.json"
    unit = guard.EXPECTED_SELF_REPAIR_ACTIONS["ADA"]["channel_monitor"]
    current = {"value": "old"}
    monkeypatch.setattr(
        guard,
        "_inspect_autonomy_unit",
        lambda candidate: _runtime_snapshot(
            current["value"] if candidate == unit else f"stable-{candidate}",
            22,
        ),
    )
    started = datetime(2026, 7, 30, tzinfo=timezone.utc)
    guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=tmp_path / "receipts",
        now=started,
        boot_id="boot-a",
    )
    current["value"] = "new"
    guard.check_autonomy_restart_provenance(
        state_path=state,
        receipt_root=tmp_path / "receipts",
        now=started + timedelta(seconds=60),
        boot_id="boot-a",
        grace_seconds=0,
    )
    before = state.read_bytes()

    try:
        guard.acknowledge_autonomy_bypass(
            unit,
            reason="intento desde principal ajeno",
            expected_accepted_invocation_id="old",
            expected_observed_invocation_id="new",
            actor="JARVIS",
            state_path=state,
            acknowledgement_root=tmp_path / "acknowledgements",
            grace_seconds=0,
            now=started + timedelta(seconds=61),
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("foreign owner acknowledgement was accepted")

    assert state.read_bytes() == before
    assert not (tmp_path / "acknowledgements").exists()


def test_stable_hash_changes_when_autonomy_drifts() -> None:
    base = {
        "status": "GREEN",
        "issues": [],
        "fixes": [],
        "ws": [],
        "auth_guard": {"status": "healthy", "issues": []},
        "autonomy_contract": {"status": "healthy", "issues": []},
        "mcp_postgres_boundary": {"status": "healthy", "issues": []},
    }
    healthy = guard.stable_hash(base)
    base["autonomy_contract"] = {"status": "drift", "issues": ["lost clause"]}
    assert guard.stable_hash(base) != healthy


def test_postgres_mcp_boundary_accepts_observer_secret_file(tmp_path) -> None:
    secret = tmp_path / "observer.env"
    secret.write_text(
        "POSTGRES_MCP_DSN='postgresql://mcp_observer:unique@localhost/db'\n",
        encoding="utf-8",
    )
    secret.chmod(0o600)
    project = tmp_path / "project.json"
    project.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "postgres": {
                        "command": "/bin/bash",
                        "args": ["source mcp_postgres_observer.env"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    global_config = tmp_path / "global.json"
    global_config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    result = guard.check_mcp_postgres_boundary(
        (project,), secret, (global_config,)
    )
    assert result == {"ok": True, "status": "healthy", "issues": []}


def test_postgres_mcp_boundary_rejects_embedded_superuser_dsn(tmp_path) -> None:
    secret = tmp_path / "observer.env"
    secret.write_text("POSTGRES_MCP_DSN='postgresql://mcp_observer:x@localhost/db'\n")
    secret.chmod(0o600)
    config = tmp_path / "global.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "postgres": {
                        "args": ["postgresql://seal:shared@localhost/db"]
                    }
                }
            }
        )
    )
    result = guard.check_mcp_postgres_boundary((config,), secret, ())
    assert result["ok"] is False
    assert any("embeber una DSN" in issue for issue in result["issues"])


def test_postgres_mcp_boundary_rejects_shadow_config_with_dsn(tmp_path) -> None:
    secret = tmp_path / "observer.env"
    secret.write_text("POSTGRES_MCP_DSN='postgresql://mcp_observer:x@localhost/db'\n")
    secret.chmod(0o600)
    project = tmp_path / "project.json"
    project.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "postgres": {
                        "args": ["source mcp_postgres_observer.env"]
                    }
                }
            }
        )
    )
    shadow = tmp_path / "global.json"
    shadow.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "postgres": {
                        "args": ["postgresql://seal:shared@localhost/db"]
                    }
                }
            }
        )
    )
    result = guard.check_mcp_postgres_boundary((project,), secret, (shadow,))
    assert result["ok"] is False
    assert any(str(shadow) in issue and "embeber una DSN" in issue for issue in result["issues"])
