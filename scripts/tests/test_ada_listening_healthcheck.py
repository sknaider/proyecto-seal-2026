from __future__ import annotations

import asyncio
import importlib.util
import json
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "ada_listening_healthcheck.py"
SPEC = importlib.util.spec_from_file_location("ada_listening_healthcheck", MODULE_PATH)
health = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(health)


def test_reply_coverage_tracks_public_and_dm_independently(monkeypatch):
    now = datetime.now(timezone.utc)

    class FakeConn:
        async def fetch(self, _query, start_id):
            assert start_id == 100
            return [
                {
                    "id": 101,
                    "channel": "web_chat",
                    "source_id": "api_william_101",
                    "age_seconds": 3.0,
                    "reply_id": 201,
                    "reply_latency_seconds": 1.2,
                    "substantive_reply_id": 202,
                    "substantive_latency_seconds": 2.0,
                },
                {
                    "id": 102,
                    "channel": "dm:ada:william",
                    "source_id": "102",
                    "age_seconds": 45.0,
                    "reply_id": None,
                    "reply_latency_seconds": None,
                    "substantive_reply_id": None,
                    "substantive_latency_seconds": None,
                },
            ]

        async def close(self):
            return None

    async def fake_connect(_dsn):
        return FakeConn()

    monkeypatch.setattr(health.asyncpg, "connect", fake_connect)
    result = asyncio.run(health.reply_coverage("dsn", 100, ack_sla_seconds=20))

    assert not result["ok"]
    assert result["channels"]["web_chat"] == {
        "total": 1,
        "answered": 1,
        "unanswered": 0,
    }
    assert result["channels"]["dm:ada:william"] == {
        "total": 1,
        "answered": 0,
        "unanswered": 1,
    }
    assert result["overdue"][0]["source_id"] == "102"
    assert result["next_start_id"] == 101


def test_recent_unanswered_message_stays_inside_sla(monkeypatch):
    class FakeConn:
        async def fetch(self, _query, _start_id):
            return [{
                "id": 102,
                "channel": "web_chat",
                "source_id": "api_william_102",
                "age_seconds": 5.0,
                "reply_id": None,
                "reply_latency_seconds": None,
                "substantive_reply_id": None,
                "substantive_latency_seconds": None,
            }]

        async def close(self):
            return None

    async def fake_connect(_dsn):
        return FakeConn()

    monkeypatch.setattr(health.asyncpg, "connect", fake_connect)
    result = asyncio.run(health.reply_coverage("dsn", 100, ack_sla_seconds=20))

    assert result["ok"]
    assert result["overdue"] == []
    assert result["pending_within_or_beyond_sla"][0]["id"] == 102
    assert result["next_start_id"] == 101


def test_late_reply_is_one_cycle_breach_and_can_advance(monkeypatch):
    class FakeConn:
        async def fetch(self, _query, _start_id):
            return [{
                "id": 103,
                "channel": "dm:ada:william",
                "source_id": "103",
                "age_seconds": 45.0,
                "reply_id": 203,
                "reply_latency_seconds": 31.0,
                "substantive_reply_id": 203,
                "substantive_latency_seconds": 31.0,
            }]

        async def close(self):
            return None

    async def fake_connect(_dsn):
        return FakeConn()

    monkeypatch.setattr(health.asyncpg, "connect", fake_connect)
    result = asyncio.run(health.reply_coverage("dsn", 100, ack_sla_seconds=20))

    assert not result["ok"]
    assert result["overdue"] == []
    assert result["late_replies"][0]["reply_latency_seconds"] == 31.0
    assert result["next_start_id"] == 103


def test_ack_without_substantive_update_breaches_progress_sla(monkeypatch):
    class FakeConn:
        async def fetch(self, _query, _start_id):
            return [{
                "id": 104,
                "channel": "web_chat",
                "source_id": "api_william_104",
                "age_seconds": 95.0,
                "reply_id": 204,
                "reply_latency_seconds": 0.5,
                "substantive_reply_id": None,
                "substantive_latency_seconds": None,
            }]

        async def close(self):
            return None

    async def fake_connect(_dsn):
        return FakeConn()

    monkeypatch.setattr(health.asyncpg, "connect", fake_connect)
    result = asyncio.run(
        health.reply_coverage(
            "dsn", 100, ack_sla_seconds=20, progress_sla_seconds=90
        )
    )

    assert not result["ok"]
    assert result["overdue"] == []
    assert result["ack_only_overdue"][0]["id"] == 104
    assert result["next_start_id"] == 104


def test_abandoned_unaccepted_route_is_terminal_not_repaired(monkeypatch, tmp_path):
    active = tmp_path / "active_task.json"
    active.write_text(
        json.dumps({
            "id": "chat_old",
            "status": "abandoned_unaccepted",
            "activated_at": (
                datetime.now(timezone.utc) - timedelta(hours=1)
            ).isoformat(),
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(health.poller, "ACTIVE_TASK_FILE", active)

    result = health.active_route_state()

    assert result["ok"]
    assert result["terminal"]
    assert result["status"] == "abandoned_unaccepted"


def test_busy_route_has_a_bounded_lease(monkeypatch, tmp_path):
    active = tmp_path / "active_task.json"
    active.write_text(
        json.dumps({
            "id": "chat_stuck",
            "status": "submitted",
            "activated_at": (
                datetime.now(timezone.utc) - timedelta(hours=1)
            ).isoformat(),
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(health.poller, "ACTIVE_TASK_FILE", active)
    monkeypatch.setattr(health.poller, "RESPONSES_DIR", tmp_path / "responses")
    monkeypatch.setattr(health.poller, "codex_is_busy", lambda: True)
    monkeypatch.setattr(health, "ACTIVE_ROUTE_BUSY_MAX_AGE_SECONDS", 900)

    result = health.active_route_state()

    assert not result["ok"]
    assert result["stale_busy"]
    assert result["busy_max_age_seconds"] == 900


def test_recent_busy_route_remains_valid(monkeypatch, tmp_path):
    active = tmp_path / "active_task.json"
    active.write_text(
        json.dumps({
            "id": "chat_current",
            "status": "submitted",
            "activated_at": (
                datetime.now(timezone.utc) - timedelta(seconds=30)
            ).isoformat(),
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(health.poller, "ACTIVE_TASK_FILE", active)
    monkeypatch.setattr(health.poller, "RESPONSES_DIR", tmp_path / "responses")
    monkeypatch.setattr(health.poller, "codex_is_busy", lambda: True)
    monkeypatch.setattr(health, "ACTIVE_ROUTE_BUSY_MAX_AGE_SECONDS", 900)

    result = health.active_route_state()

    assert result["ok"]
    assert not result["stale_busy"]


def test_delivery_route_uses_headless_failover_when_listener_releases_lease():
    states = {
        "ada-codex-remote-bridge.service": {
            "active": True,
            "enabled": True,
        }
    }

    result = health.delivery_route_state(
        states,
        {"ok": False, "status": "degraded"},
    )

    assert result == {
        "ok": True,
        "terminal_listener_ok": False,
        "headless_bridge_ok": True,
        "mode": "headless_failover",
    }


def test_healthcheck_repair_table_matches_ada_broker_policy():
    assert set(health.SERVICE_REPAIR_ACTIONS) == set(health.SERVICES)
    for unit, action in health.SERVICE_REPAIR_ACTIONS.items():
        assert health.seal_self_repair.AGENT_ACTIONS["ADA"][action] == unit


def test_repair_does_not_restart_active_poller_for_released_lease(monkeypatch):
    calls = []
    states = {
        "ada-codex-remote-bridge.service": {"active": True},
        "seal-ada-codex-poller.service": {"active": True},
        "seal-ada-codex-stream-relay.service": {"active": True},
        "seal-ada-codex-autostart.service": {"active": True},
    }
    monkeypatch.setattr(health, "quarantine_stale_route", lambda _route: None)
    monkeypatch.setattr(
        health,
        "_systemctl",
        lambda *args: calls.append(args),
    )

    repaired, quarantined, receipts, errors = health.repair_services(
        states,
        {"ok": False, "status": "degraded"},
        {"ok": True},
    )

    assert repaired == []
    assert quarantined is None
    assert receipts == []
    assert errors == []
    assert calls == []


def test_quarantined_route_does_not_restart_active_services(monkeypatch):
    states = {
        "ada-codex-remote-bridge.service": {"active": True},
        "seal-ada-codex-poller.service": {"active": True},
        "seal-ada-codex-stream-relay.service": {"active": True},
        "seal-ada-codex-autostart.service": {"active": True},
    }
    calls = []
    monkeypatch.setattr(
        health,
        "quarantine_stale_route",
        lambda _route: "/evidence/orphaned-active-task.json",
    )
    monkeypatch.setattr(
        health,
        "repair_service",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    repaired, quarantined, receipts, errors = health.repair_services(
        states,
        {"ok": False, "status": "degraded"},
        {"ok": False, "status": "pending_submit"},
    )

    assert repaired == []
    assert quarantined == "/evidence/orphaned-active-task.json"
    assert receipts == []
    assert errors == []
    assert calls == []


def test_inactive_poller_is_repaired_through_broker(monkeypatch):
    states = {
        "ada-codex-remote-bridge.service": {"active": True},
        "seal-ada-codex-poller.service": {"active": False},
        "seal-ada-codex-stream-relay.service": {"active": True},
        "seal-ada-codex-autostart.service": {"active": True},
    }
    calls = []
    evidence = {
        "unit": "seal-ada-codex-poller.service",
        "action": "visible_poller",
        "receipt": "/durable/receipt.json",
        "result": "verified",
    }
    monkeypatch.setattr(health, "quarantine_stale_route", lambda _route: None)
    monkeypatch.setattr(
        health,
        "repair_service",
        lambda name, *, reason: calls.append((name, reason)) or evidence,
    )
    monkeypatch.setattr(
        health,
        "service_state",
        lambda _name: {"active": True},
    )
    monkeypatch.setattr(health.time, "sleep", lambda _seconds: None)

    repaired, quarantined, receipts, errors = health.repair_services(
        states,
        {"ok": True},
        {"ok": True},
    )

    assert repaired == ["seal-ada-codex-poller.service"]
    assert quarantined is None
    assert receipts == [evidence]
    assert errors == []
    assert calls[0][0] == "seal-ada-codex-poller.service"


def test_repair_service_writes_verified_broker_receipt(monkeypatch, tmp_path):
    before = health.seal_self_repair.UnitSnapshot(
        unit="seal-ada-codex-poller.service",
        observed_at="2026-07-30T00:00:00+00:00",
        load_state="loaded",
        unit_type="simple",
        active_state="active",
        sub_state="running",
        main_pid=10,
        invocation_id="old",
        result="success",
    )
    after = health.seal_self_repair.UnitSnapshot(
        **{
            **before.__dict__,
            "observed_at": "2026-07-30T00:00:01+00:00",
            "main_pid": 11,
            "invocation_id": "new",
        }
    )
    receipt = health.seal_self_repair.RepairReceipt(
        schema="seal.autonomy.repair-receipt.v1",
        receipt_id="healthcheck-test",
        agent="ADA",
        operation="restart",
        action="visible_poller",
        unit="seal-ada-codex-poller.service",
        reason="healthcheck test",
        started_at=before.observed_at,
        finished_at=after.observed_at,
        opportunity={"command_invoked": True, "operation": "restart"},
        expected={"target_changed": True},
        before=before,
        after=after,
        negative_control_before=before,
        negative_control_after=before,
        checks={"passed": True},
        command=["systemctl", "--user", "restart", before.unit],
        returncode=0,
        output="",
        result="verified",
    )
    calls = []
    monkeypatch.setattr(
        health.seal_self_repair,
        "execute",
        lambda *args, **kwargs: calls.append((args, kwargs)) or receipt,
    )
    path = tmp_path / "receipt.json"
    monkeypatch.setattr(
        health.seal_self_repair,
        "write_receipt",
        lambda value: path if value is receipt else None,
    )

    result = health.repair_service(
        "seal-ada-codex-poller.service",
        reason="healthcheck test",
    )

    assert calls[0][0][:3] == ("ADA", "visible_poller", "restart")
    assert result["receipt"] == str(path)
    assert result["before_invocation_id"] == "old"
    assert result["after_invocation_id"] == "new"


def test_william_backlog_ignores_cursors_and_fails_on_unanswered(monkeypatch):
    class FakeConn:
        async def fetchrow(self, _query, horizon):
            assert horizon == 24
            return {
                "count": 1,
                "oldest_id": 117171,
                "oldest_age_seconds": 45.0,
            }

        async def close(self):
            return None

    async def fake_connect(_dsn):
        return FakeConn()

    monkeypatch.setattr(health.asyncpg, "connect", fake_connect)
    result = asyncio.run(health.william_reply_backlog("dsn", horizon_hours=24))

    assert not result["ok"]
    assert result["count"] == 1
    assert result["oldest_id"] == 117171


def test_failure_alert_replies_to_the_unanswered_source(monkeypatch):
    calls = []
    monkeypatch.setattr(
        health.bridge,
        "post_message",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True, "id": "ack"},
    )
    report = {
        "failures": ["reply_coverage"],
        "reply_coverage": {
            "overdue": [{
                "id": 117081,
                "channel": "web_chat",
                "source_id": "api_william_117081",
                "age_seconds": 25.0,
            }]
        },
    }

    assert health.alert_failure(report)
    assert calls[0][0][0] == "William"
    assert calls[0][0][3] == "web_chat"
    assert calls[0][1]["in_reply_to"] == "api_william_117081"
    assert calls[0][1]["idempotency_key"] == "ada_listening_guard_117081"


def test_monitor_cursor_is_private_and_monotonic(monkeypatch, tmp_path):
    state = tmp_path / "monitor.json"
    monkeypatch.setattr(health, "MONITOR_STATE_PATH", state)
    health._atomic_json(state, {"schema": "v1", "start_id": 100})

    health.advance_monitor_start_id(90)
    assert json.loads(state.read_text(encoding="utf-8"))["start_id"] == 100

    health.advance_monitor_start_id(120)
    assert json.loads(state.read_text(encoding="utf-8"))["start_id"] == 120
    assert stat.S_IMODE(state.stat().st_mode) == 0o600


def test_timer_contract_is_continuous_not_weekly():
    timer = (
        Path(__file__).resolve().parents[2]
        / "ops/systemd/ada-listening-healthcheck.timer"
    ).read_text(encoding="utf-8")

    assert "OnUnitActiveSec=60s" in timer
    assert "OnCalendar=Mon" not in timer
