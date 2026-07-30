from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import seal_self_repair
from seal_self_repair import AGENT_ACTIONS, UnitSnapshot, execute, resolve_agent, resolve_unit


def test_identity_is_fail_closed() -> None:
    with pytest.raises(PermissionError, match="SEAL_AGENT is required"):
        resolve_agent({})


def test_each_supported_agent_has_only_fixed_own_units() -> None:
    for agent, actions in AGENT_ACTIONS.items():
        assert actions
        for unit in actions.values():
            assert agent.casefold() in unit.casefold()
            assert unit.endswith(".service")


def test_arbitrary_unit_is_denied() -> None:
    with pytest.raises(PermissionError, match="not allowed"):
        resolve_unit("ADA", "../../ssh.service")


def test_cross_agent_action_is_not_reachable_from_ada_policy() -> None:
    assert "seal-channel-monitor@JARVIS.service" not in AGENT_ACTIONS["ADA"].values()
    with pytest.raises(PermissionError):
        resolve_unit("ADA", "jarvis_channel_monitor")


def test_known_action_resolves_to_exact_unit() -> None:
    assert resolve_unit("NEXUS", "visible_terminal") == "nexus-terminal.service"


def test_default_receipt_root_is_durable_and_honors_xdg(tmp_path) -> None:
    root = seal_self_repair.default_receipt_root(
        {"XDG_DATA_HOME": str(tmp_path / "data")}
    )
    assert root == tmp_path / "data" / "seal" / "autonomy_receipts"


def test_oneshot_actions_are_excluded_from_v1() -> None:
    assert "visible_terminal" not in AGENT_ACTIONS["ALICE"]
    assert "visible_terminal" not in AGENT_ACTIONS["FABLE"]
    assert "nerves" not in AGENT_ACTIONS["FABLE"]


def _snapshot(unit: str, pid: int, invocation: str, state: str = "active") -> UnitSnapshot:
    return UnitSnapshot(
        unit=unit,
        observed_at=datetime.now(timezone.utc).isoformat(),
        load_state="loaded",
        unit_type="simple",
        active_state=state,
        sub_state="running" if state == "active" else "failed",
        main_pid=pid,
        invocation_id=invocation,
        result="success" if state == "active" else "exit-code",
    )


def test_restart_receipt_requires_target_change_and_unchanged_control(monkeypatch) -> None:
    target = AGENT_ACTIONS["ADA"]["channel_monitor"]
    control = seal_self_repair.CONTROL_UNITS["ADA"]
    snapshots = iter(
        [
            _snapshot(target, 10, "target-old"),
            _snapshot(control, 20, "control-same"),
            _snapshot(target, 11, "target-new"),
            _snapshot(control, 20, "control-same"),
        ]
    )
    monkeypatch.setattr(seal_self_repair, "snapshot_unit", lambda _unit: next(snapshots))
    monkeypatch.setattr(
        seal_self_repair,
        "_run",
        lambda _argv, timeout=15: CompletedProcess(_argv, 0, "", ""),
    )

    receipt = execute("ADA", "channel_monitor", "restart", "test", timeout=0.01)

    assert receipt.result == "verified"
    assert all(receipt.checks.values())


def test_restart_receipt_fails_when_negative_control_changes(monkeypatch) -> None:
    target = AGENT_ACTIONS["ADA"]["channel_monitor"]
    control = seal_self_repair.CONTROL_UNITS["ADA"]
    snapshots = iter(
        [
            _snapshot(target, 10, "target-old"),
            _snapshot(control, 20, "control-old"),
            _snapshot(target, 11, "target-new"),
            _snapshot(control, 21, "control-new"),
        ]
    )
    monkeypatch.setattr(seal_self_repair, "snapshot_unit", lambda _unit: next(snapshots))
    monkeypatch.setattr(
        seal_self_repair,
        "_run",
        lambda _argv, timeout=15: CompletedProcess(_argv, 0, "", ""),
    )

    receipt = execute("ADA", "channel_monitor", "restart", "test", timeout=0.01)

    assert receipt.result == "failed"
    assert receipt.checks["negative_control_other_agent_unchanged"] is False


def test_restart_rejects_oneshot_even_if_policy_drifts(monkeypatch) -> None:
    target = AGENT_ACTIONS["ADA"]["channel_monitor"]
    control = seal_self_repair.CONTROL_UNITS["ADA"]
    oneshot = _snapshot(target, 0, "oneshot")
    object.__setattr__(oneshot, "unit_type", "oneshot")
    snapshots = iter([oneshot, _snapshot(control, 20, "control")])
    monkeypatch.setattr(seal_self_repair, "snapshot_unit", lambda _unit: next(snapshots))

    with pytest.raises(RuntimeError, match="only repairs continuously supervised"):
        execute("ADA", "channel_monitor", "restart", "test")


def test_receipt_is_written_to_private_durable_tree(tmp_path, monkeypatch) -> None:
    receipt = seal_self_repair.RepairReceipt(
        schema="seal.autonomy.repair-receipt.v1",
        receipt_id="durable-test",
        agent="ADA",
        operation="restart",
        action="channel_monitor",
        unit=AGENT_ACTIONS["ADA"]["channel_monitor"],
        reason="test",
        started_at="2026-07-30T00:00:00+00:00",
        finished_at="2026-07-30T00:00:01+00:00",
        opportunity={"command_invoked": True, "operation": "restart"},
        expected={"target_active": True},
        before=_snapshot("target", 1, "old"),
        after=_snapshot("target", 2, "new"),
        negative_control_before=_snapshot("control", 3, "same"),
        negative_control_after=_snapshot("control", 3, "same"),
        checks={"passed": True},
        command=["systemctl", "--user", "restart", "target"],
        returncode=0,
        output="",
        result="verified",
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    path = seal_self_repair.write_receipt(receipt)

    assert path == tmp_path / "data" / "seal" / "autonomy_receipts" / "ADA" / "durable-test.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.parent.parent.stat().st_mode & 0o777 == 0o700
