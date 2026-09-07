from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("seal_agent_stability_guard.py")
SPEC = importlib.util.spec_from_file_location("seal_agent_stability_guard_cutover", MODULE_PATH)
guard = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


def test_alice_v2_switch_is_exact_and_fails_closed_to_v1(tmp_path):
    switch = tmp_path / "alice_cuerpo_activo"
    assert guard.alice_v2_is_active(switch) is False
    switch.write_text("ALICE\n", encoding="utf-8")
    assert guard.alice_v2_is_active(switch) is False
    switch.write_text("basura\n", encoding="utf-8")
    assert guard.alice_v2_is_active(switch) is False
    switch.write_text("alice-v2\n", encoding="utf-8")
    assert guard.alice_v2_is_active(switch) is True


def test_guard_does_not_revive_v1_units_during_v2_cutover(monkeypatch):
    monkeypatch.setattr(guard, "unit_load_state", lambda _unit: "loaded")
    monkeypatch.setattr(guard, "unit_active_state", lambda _unit: "inactive")
    restarted = []
    monkeypatch.setattr(
        guard,
        "restart_unit",
        lambda unit: (restarted.append(unit) or True, "restarted"),
    )

    result = guard.ensure_units(
        {"ALICE": "running"},
        units=("seal-bridge-alice.service", "seal-alice-dm-poller.service"),
        alice_v2_active=True,
    )

    assert restarted == []
    assert result["issues"] == []
    assert result["skipped"] == [
        "seal-bridge-alice.service:intentional-alice-v2-cutover",
        "seal-alice-dm-poller.service:intentional-alice-v2-cutover",
    ]


def test_guard_still_repairs_v1_units_when_v1_is_active(monkeypatch):
    state = {"seal-bridge-alice.service": "inactive"}
    monkeypatch.setattr(guard, "unit_load_state", lambda _unit: "loaded")
    monkeypatch.setattr(guard, "unit_active_state", lambda unit: state[unit])

    def restart(unit):
        state[unit] = "active"
        return True, "restarted"

    monkeypatch.setattr(guard, "restart_unit", restart)
    result = guard.ensure_units(
        {"ALICE": "running"},
        units=("seal-bridge-alice.service",),
        alice_v2_active=False,
    )

    assert result["fixes"] == ["seal-bridge-alice.service: inactive->active"]
    assert result["issues"] == []


def test_guard_repairs_v2_heartbeat_only_during_v2_cutover(monkeypatch):
    unit = "seal-alice-v2-heartbeat.timer"
    state = {unit: "inactive"}
    monkeypatch.setattr(guard, "unit_load_state", lambda _unit: "loaded")
    monkeypatch.setattr(guard, "unit_active_state", lambda item: state[item])

    def restart(item):
        state[item] = "active"
        return True, "restarted"

    monkeypatch.setattr(guard, "restart_unit", restart)
    active = guard.ensure_units(None, units=(unit,), alice_v2_active=True)
    assert active["fixes"] == [f"{unit}: inactive->active"]

    state[unit] = "inactive"
    inactive = guard.ensure_units(None, units=(unit,), alice_v2_active=False)
    assert inactive["fixes"] == []
    assert inactive["skipped"] == [f"{unit}:intentional-alice-v1-active"]


def test_expected_rosters_drop_only_alice_v1_during_cutover(monkeypatch):
    lifecycle = {
        "expected_agents": ["ALICE", "FABLE", "JARVIS", "NEXUS"],
    }
    monitors, heartbeats = guard.expected_runtime_agents(
        lifecycle, alice_v2_active=True
    )

    assert monitors == ("FABLE", "JARVIS", "NEXUS")
    assert heartbeats == ("ADA", "FABLE", "JARVIS", "NEXUS")

    monitors, heartbeats = guard.expected_runtime_agents(
        lifecycle, alice_v2_active=False
    )
    assert monitors == ("ALICE", "FABLE", "JARVIS", "NEXUS")
    assert heartbeats == ("ADA", "ALICE", "FABLE", "JARVIS", "NEXUS")


def test_expected_ws_roster_drops_only_alice_v1_during_cutover():
    assert guard.expected_ws_agents(alice_v2_active=True) == (
        "FABLE",
        "JARVIS",
        "NEXUS",
    )
    assert guard.expected_ws_agents(alice_v2_active=False) == guard.WS_AGENTS


def test_v2_heartbeat_result_is_checked_only_while_v2_is_active():
    assert guard.expected_oneshot_units(alice_v2_active=True) == (
        *guard.CRITICAL_ONESHOTS,
        guard.ALICE_V2_ONESHOT,
    )
    assert guard.expected_oneshot_units(
        alice_v2_active=False
    ) == guard.CRITICAL_ONESHOTS


def test_autonomy_rebaselines_v1_unit_during_v2_cutover(tmp_path, monkeypatch):
    unit = "seal-bridge-alice.service"
    state_path = tmp_path / "autonomy.json"
    state_path.write_text(
        json.dumps(
            {
                "schema": "seal.autonomy.invocation-state.v1",
                "boot_id": "boot-a",
                "updated_at": "2026-09-04T03:15:00+00:00",
                "units": {
                    unit: {
                        "agent": "ALICE",
                        "action": "bridge",
                        "accepted_invocation_id": "old",
                        "accepted_main_pid": 10,
                        "active_state": "active",
                        "pending": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        guard,
        "_autonomy_units",
        lambda: {unit: {"agent": "ALICE", "action": "bridge"}},
    )
    monkeypatch.setattr(guard, "AUTONOMY_CASCADE_ROOTS", ())
    monkeypatch.setattr(
        guard,
        "_inspect_autonomy_unit",
        lambda _unit: {
            "invocation_id": "new",
            "main_pid": 0,
            "active_state": "inactive",
            "active_enter_monotonic": 0,
            "part_of": [],
            "requires": [],
        },
    )
    monkeypatch.setattr(guard, "_load_autonomy_receipt_edges", lambda *_args: {})
    monkeypatch.setattr(
        guard, "_load_verified_cascade_parent_invocations", lambda *_args: {}
    )

    result = guard.check_autonomy_restart_provenance(
        state_path=state_path,
        receipt_root=tmp_path / "receipts",
        grace_seconds=0,
        now=datetime(2026, 9, 4, 3, 20, tzinfo=timezone.utc),
        boot_id="boot-a",
        lifecycle_states={"ALICE": "running"},
        alice_v2_active=True,
    )

    assert result["status"] == "healthy"
    assert result["issues"] == []
    assert result["lifecycle_skipped"] == [
        "seal-bridge-alice.service:intentional-alice-v2-cutover"
    ]
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["units"][unit]["accepted_invocation_id"] == "new"
