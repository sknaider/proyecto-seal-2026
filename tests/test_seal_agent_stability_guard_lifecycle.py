from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from scripts import seal_agent_stability_guard as guard


class _FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    async def fetch(self, _query, agents):
        assert tuple(agents) == guard.LIFECYCLE_MANAGED_AGENTS
        return self.rows

    async def close(self):
        self.closed = True


class _FakeAsyncpg:
    def __init__(self, connection):
        self.connection = connection

    async def connect(self, _dsn, timeout):
        assert timeout == 5
        return self.connection


def test_lifecycle_manual_fable_changes_expected_denominators(monkeypatch):
    connection = _FakeConnection(
        [
            {"agent_name": "ALICE", "desired_state": "running"},
            {"agent_name": "FABLE", "desired_state": "manual"},
            {"agent_name": "JARVIS", "desired_state": "running"},
            {"agent_name": "NEXUS", "desired_state": "running"},
        ]
    )
    monkeypatch.setattr(guard, "asyncpg", _FakeAsyncpg(connection))
    monkeypatch.setattr(guard, "read_private_dsn", lambda *_args: "opaque")

    intent = asyncio.run(guard.load_lifecycle_intent())
    monitors, heartbeats = guard.expected_runtime_agents(intent)

    assert intent["ok"] is True
    assert intent["states"]["FABLE"] == "manual"
    assert monitors == ("ALICE", "JARVIS", "NEXUS")
    assert heartbeats == ("ADA", "ALICE", "JARVIS", "NEXUS")
    assert connection.closed is True


def test_manual_fable_units_are_observed_but_never_restarted(monkeypatch):
    restarted: list[str] = []
    monkeypatch.setattr(guard, "unit_load_state", lambda _unit: "loaded")
    monkeypatch.setattr(guard, "unit_active_state", lambda _unit: "inactive")

    def restart(unit):
        restarted.append(unit)
        return True, "restarted"

    monkeypatch.setattr(guard, "restart_unit", restart)
    units = (
        "seal-channel-monitor@FABLE.service",
        "seal-fable-heartbeat.timer",
        "seal-channel-monitor@ALICE.service",
    )

    result = guard.ensure_units(
        {"FABLE": "manual", "ALICE": "running"},
        units,
    )

    assert restarted == ["seal-channel-monitor@ALICE.service"]
    assert result["issues"] == [
        "seal-channel-monitor@ALICE.service: inactive->inactive (restarted)"
    ]
    assert result["skipped"] == [
        "seal-channel-monitor@FABLE.service:intentional-manual",
        "seal-fable-heartbeat.timer:intentional-manual",
    ]


def test_unverifiable_lifecycle_fails_closed_without_restart(monkeypatch):
    restarted: list[str] = []
    monkeypatch.setattr(guard, "unit_load_state", lambda _unit: "loaded")
    monkeypatch.setattr(guard, "unit_active_state", lambda _unit: "inactive")
    monkeypatch.setattr(
        guard,
        "restart_unit",
        lambda unit: (restarted.append(unit) is None, "restarted"),
    )

    result = guard.ensure_units(
        None,
        ("seal-channel-monitor@FABLE.service", "seal-fable-heartbeat.timer"),
    )

    assert restarted == []
    assert result["issues"] == []
    assert result["skipped"] == [
        "seal-channel-monitor@FABLE.service:lifecycle-unverifiable",
        "seal-fable-heartbeat.timer:lifecycle-unverifiable",
    ]


def test_manual_fable_transition_is_not_reported_as_restart_bypass(
    tmp_path, monkeypatch
):
    state_path = tmp_path / "autonomy.json"
    units = guard._autonomy_units()
    state_path.write_text(
        json.dumps(
            {
                "schema": "seal.autonomy.invocation-state.v1",
                "boot_id": "boot-a",
                "updated_at": "2026-09-03T17:40:00+00:00",
                "units": {
                    unit: {
                        **owner,
                        "accepted_invocation_id": f"old-{index}",
                        "accepted_main_pid": index + 10,
                        "active_state": "active",
                        "pending": None,
                    }
                    for index, (unit, owner) in enumerate(units.items())
                },
            }
        ),
        encoding="utf-8",
    )

    def inspect(unit):
        if unit == "seal-channel-monitor@FABLE.service":
            return {
                "invocation_id": "",
                "main_pid": 0,
                "active_state": "inactive",
                "active_enter_monotonic": 0,
                "part_of": [],
                "requires": [],
            }
        return {
            "invocation_id": units[unit].get("invocation_id", "") or "stable",
            "main_pid": 20,
            "active_state": "active",
            "active_enter_monotonic": 1,
            "part_of": [],
            "requires": [],
        }

    monkeypatch.setattr(guard, "_inspect_autonomy_unit", inspect)
    monkeypatch.setattr(guard, "_load_autonomy_receipt_edges", lambda *_args: {})
    monkeypatch.setattr(
        guard, "_load_verified_cascade_parent_invocations", lambda *_args: {}
    )

    result = guard.check_autonomy_restart_provenance(
        state_path=state_path,
        receipt_root=tmp_path / "receipts",
        grace_seconds=0,
        now=datetime(2026, 9, 3, 17, 45, tzinfo=timezone.utc),
        boot_id="boot-a",
        lifecycle_states={
            "ALICE": "running",
            "FABLE": "manual",
            "JARVIS": "running",
            "NEXUS": "running",
        },
    )

    assert "seal-channel-monitor@FABLE.service" not in result["out_of_band"]
    assert not any("FABLE" in issue for issue in result["issues"])
    assert result["lifecycle_skipped"] == [
        "seal-channel-monitor@FABLE.service:intentional-manual"
    ]
