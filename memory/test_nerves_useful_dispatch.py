from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seal_nerves as nerves
import nerves_maintenance_alice as alice_maintenance
import nerves_maintenance_jarvis as jarvis_maintenance


def test_useful_curiosity_executes_maintenance_without_public_post(monkeypatch):
    engine = nerves.MotivationEngine("ADA")
    called = []

    async def maintenance(received):
        called.append(received.agent)
        return None

    async def forbidden_post(*_args, **_kwargs):
        raise AssertionError("useful curiosity must not create a nerves_fire row")

    monkeypatch.setattr(nerves, "NERVES_USEFUL", True)
    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(engine, "_post_chat", forbidden_post)

    result = asyncio.run(engine._fire_curiosity(51.0))
    assert result == "maintenance_fired:clean_silent:ADA"
    assert called == ["ADA"]


def test_historical_tanks_are_not_part_of_live_state():
    now = datetime.now(timezone.utc)

    class Conn:
        async def fetch(self, *_args):
            return [
                {"tank": "curiosity", "value": 10.0, "last_update": now,
                 "last_fired": None, "fire_count": 1},
                {"tank": "boredom", "value": 99.0, "last_update": now,
                 "last_fired": None, "fire_count": 99},
            ]

    class Acquire:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *_args):
            return False

    class Pool:
        def acquire(self):
            return Acquire()

    engine = nerves.MotivationEngine("ADA")
    engine.pool = Pool()
    states = asyncio.run(engine.get_states())
    assert set(states) == {"curiosity"}


def test_jarvis_curiosity_has_real_cooldown():
    assert nerves.AGENT_TANK_OVERRIDES["JARVIS"]["curiosity"]["cooldown_s"] == 90 * 60


def test_jarvis_connector_invokes_watch_action_and_persists_green(monkeypatch, tmp_path):
    artifact = tmp_path / "jarvis.jsonl"
    monkeypatch.setattr(jarvis_maintenance, "ARTIFACT", artifact)
    monkeypatch.setattr(
        jarvis_maintenance,
        "_watch_check",
        lambda: ("GREEN", [], "diff=ok identity=ok absolute=ok"),
    )
    assert jarvis_maintenance._run() is None
    payload = json.loads(artifact.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["state"] == "GREEN"
    assert payload["action_source"] == "tools/jarvis_nerves_watch.py"
    assert artifact.stat().st_mode & 0o077 == 0


def test_jarvis_connector_fails_loud_on_real_finding(monkeypatch, tmp_path):
    artifact = tmp_path / "jarvis.jsonl"
    monkeypatch.setattr(jarvis_maintenance, "ARTIFACT", artifact)
    monkeypatch.setattr(
        jarvis_maintenance,
        "_watch_check",
        lambda: ("FINDING", ["synthetic mismatch"], "identity fail"),
    )
    result = jarvis_maintenance._run()
    assert result is not None
    assert "FINDING" in result
    assert "synthetic mismatch" in result


def test_useful_maintenance_alerts_once_and_deduplicates_same_tick(monkeypatch):
    engine = nerves.MotivationEngine("NEXUS")
    calls = []
    posts = []

    async def maintenance(received):
        calls.append(received.agent)
        return "security_probe_failed"

    async def post(message, to="equipo"):
        posts.append((message, to))

    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(engine, "_post_chat", post)

    first = asyncio.run(engine._fire_useful_maintenance())
    second = asyncio.run(engine._fire_useful_maintenance())
    assert first == second == "maintenance_fired:value:NEXUS"
    assert calls == ["NEXUS"]
    assert len(posts) == 1
    assert posts[0][1] == "William"
    assert "CRITICAL" in posts[0][0]


def test_useful_maintenance_exception_is_not_clean(monkeypatch):
    engine = nerves.MotivationEngine("ADA")

    async def broken(_engine):
        raise RuntimeError("synthetic")

    # Exercise the cache/alert boundary with the same failure artifact returned
    # by the dispatcher: exceptions must never be labelled clean_silent.
    async def failed(_engine):
        try:
            await broken(_engine)
        except RuntimeError:
            return "maintenance_failed:ADA:RuntimeError"

    posts = []

    async def post(message, to="equipo"):
        posts.append((message, to))

    monkeypatch.setattr(nerves, "_run_maintenance_action", failed)
    monkeypatch.setattr(engine, "_post_chat", post)
    with pytest.raises(nerves.NervesActionError):
        asyncio.run(engine._fire_useful_maintenance())
    assert posts == []


def test_delivery_failure_is_fail_closed(monkeypatch):
    engine = nerves.MotivationEngine("NEXUS")

    async def fail_send(*_args, **_kwargs):
        raise RuntimeError("synthetic 503")

    monkeypatch.setattr(nerves, "send_agent_message", fail_send)
    with pytest.raises(nerves.NervesDeliveryError):
        asyncio.run(engine._post_chat("critical", to="William"))


def test_messages_keep_exact_recipients_without_batch_collapse(monkeypatch):
    engine = nerves.MotivationEngine("NEXUS")
    delivered = []

    async def record(sender, to, message, **kwargs):
        delivered.append((sender, to, message, kwargs["channel"]))

    monkeypatch.setattr(nerves, "send_agent_message", record)
    asyncio.run(engine._post_chat("critical", to="William"))
    asyncio.run(engine._post_chat("coordination", to="ALICE"))
    assert delivered == [
        ("NEXUS", "William", "critical", "web_chat"),
        ("NEXUS", "ALICE", "coordination", "dm:alice:nexus"),
    ]


def test_every_agent_delivery_has_idempotency_key(monkeypatch):
    engine = nerves.MotivationEngine("NEXUS", run_id="run-test")
    keys = []

    async def record(*_args, **kwargs):
        keys.append(kwargs.get("idempotency_key"))

    monkeypatch.setattr(nerves, "send_agent_message", record)
    asyncio.run(engine._post_chat("critical", to="William"))
    assert keys and keys[0].startswith("nerves_nexus_run-test_")


def test_failed_effect_is_logged_retryable_and_tank_is_not_reset(monkeypatch):
    engine = nerves.MotivationEngine("ADA", run_id="failure-run")
    metrics = []
    persisted = []
    ledger = []

    async def no_suppress():
        return False

    async def no_flush():
        return None

    async def states():
        return {
            "curiosity": {
                "value": 99.0,
                "threshold": 25.0,
                "last_update": datetime.now(timezone.utc),
                "last_fired": None,
                "fire_count": 7,
                "above_threshold": True,
                "in_cooldown": False,
            }
        }

    async def fail_fire(*_args):
        raise RuntimeError("synthetic")

    async def log_metric(*args, **kwargs):
        metrics.append((args, kwargs))

    async def persist(current):
        persisted.append(current)

    monkeypatch.setattr(engine, "_should_suppress", no_suppress)
    monkeypatch.setattr(engine, "_flush_queue_if_idle", no_flush)
    monkeypatch.setattr(engine, "get_states", states)
    monkeypatch.setattr(engine, "_fire", fail_fire)
    monkeypatch.setattr(engine, "_log_metric", log_metric)
    monkeypatch.setattr(engine, "_persist_decay", persist)
    monkeypatch.setattr(nerves, "_append_action_ledger", ledger.append)

    with pytest.raises(nerves.NervesActionError):
        asyncio.run(engine._tick_locked())
    assert metrics[0][1]["effect_verified"] is False
    assert metrics[0][1]["reset_outcome"] == "preserved_for_retry"
    assert metrics[0][1]["fired"] is False
    assert [row["status"] for row in ledger] == ["claimed", "failed_retryable"]
    assert persisted and persisted[0]["curiosity"]["value"] == 99.0


def test_run_tick_singleflight_wraps_sensor_pipeline(monkeypatch, tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()
    sensed = []

    class FakeEngine:
        def __init__(self, agent):
            self.agent = agent

        async def connect(self):
            return None

        async def close(self):
            return None

        async def _tick_locked(self):
            return []

        async def get_states(self):
            return {}

        def status_report(self, _states):
            return "ok"

    async def slow_sensor(engine):
        sensed.append(engine.agent)
        entered.set()
        await release.wait()

    monkeypatch.setattr(nerves, "_ALERT_LOG_DIR", tmp_path)
    monkeypatch.setattr(nerves, "MotivationEngine", FakeEngine)
    monkeypatch.setattr(nerves, "sense_environment", slow_sensor)

    async def scenario():
        first = asyncio.create_task(nerves.run_tick("ADA"))
        await entered.wait()
        second = await nerves.run_tick("ADA")
        release.set()
        await first
        return second

    assert asyncio.run(scenario()) == ({}, [])
    assert sensed == ["ADA"]


def test_task_dedup_is_not_committed_before_delivery(monkeypatch):
    engine = nerves.MotivationEngine("NEXUS")
    saved = []

    async def fail_post(*_args, **_kwargs):
        raise nerves.NervesDeliveryError("synthetic")

    monkeypatch.setattr(nerves, "_task_drive_context", {
        "overdue_3h": 1,
        "overdue_1h": 0,
        "pending": 1,
        "task_list": [{"id": "task-1", "title": "real task", "deadline_state": "overdue_3h"}],
        "state_changes": {"overdue_3h": 1},
    })
    monkeypatch.setattr(nerves, "_load_alert_log", lambda _agent: {})
    monkeypatch.setattr(nerves, "_save_alert_log", lambda *_args: saved.append(True))
    monkeypatch.setattr(engine, "_post_chat", fail_post)
    with pytest.raises(nerves.NervesDeliveryError):
        asyncio.run(engine._fire_task_drive(99.0))
    assert saved == []


def test_alert_seen_is_not_committed_before_delivery(monkeypatch):
    engine = nerves.MotivationEngine("ADA")
    marks = []

    async def fail_post(*_args, **_kwargs):
        raise nerves.NervesDeliveryError("synthetic")

    nerves._alert_drive_context["ADA"] = {
        "errors": [{"source": "tests", "severity": "critical", "line": "ADA test critical"}],
        "test_failures": [],
        "critical": [],
        "count": 1,
    }
    monkeypatch.setattr(nerves, "_mark_alert_context_seen", lambda *_args: marks.append(True))
    monkeypatch.setattr(engine, "_post_chat", fail_post)
    with pytest.raises(nerves.NervesDeliveryError):
        asyncio.run(engine._fire_alert_drive(99.0))
    assert marks == []


def test_distinct_stable_task_ids_do_not_collapse_same_title():
    log = {}
    tasks = [
        {"id": "task-a", "title": "same"},
        {"id": "task-b", "title": "same"},
    ]
    fresh = nerves._dedupe_task_alerts(tasks, "overdue_3h", 100.0, log, 3600.0)
    assert fresh == tasks
    assert len(log) == 2


def test_singleflight_skips_overlapping_tick(monkeypatch, tmp_path):
    engine_a = nerves.MotivationEngine("ADA")
    engine_b = nerves.MotivationEngine("ADA")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_tick():
        entered.set()
        await release.wait()
        return [{"ok": True}]

    async def forbidden_tick():
        raise AssertionError("second invocation must not run")

    monkeypatch.setattr(nerves, "_ALERT_LOG_DIR", tmp_path)
    monkeypatch.setattr(engine_a, "_tick_locked", slow_tick)
    monkeypatch.setattr(engine_b, "_tick_locked", forbidden_tick)

    async def scenario():
        first = asyncio.create_task(engine_a.tick())
        await entered.wait()
        second = await engine_b.tick()
        release.set()
        return await first, second

    first, second = asyncio.run(scenario())
    assert first == [{"ok": True}]
    assert second == []


def test_old_multiline_error_is_not_revived_by_fresh_append(tmp_path):
    log_path = tmp_path / "events.log"
    old = "2026-04-28 01:02:03 ERROR historical failure\nTraceback: old detail\n"
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    log_path.write_text(old + f"{now} INFO current healthy event\n  current detail\n")
    rows = nerves._tail_log(str(log_path), lines=20, max_age_s=900)
    assert not any("historical" in row or "old detail" in row for row in rows)
    assert any("current healthy" in row for row in rows)


def test_fired_metric_failure_is_fail_closed():
    class Conn:
        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("disk/db unavailable")

    class Acquire:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *_args):
            return False

    class Pool:
        def acquire(self):
            return Acquire()

    engine = nerves.MotivationEngine("ADA")
    engine.pool = Pool()
    state = {"value": 99.0, "threshold": 25.0, "fire_count": 1}
    with pytest.raises(nerves.NervesPersistenceError):
        asyncio.run(engine._log_metric("curiosity", state, fired=True,
                                       action_result="maintenance_fired:clean_silent:ADA"))


def test_alice_connector_consumes_fresh_ok_artifact(monkeypatch, tmp_path):
    status = tmp_path / "orion_nerve_status.log"
    status.write_text(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": "OK",
        "db_user": "svc_orion_exam",
        "login_http": 200,
        "fails": [],
    }) + "\n", encoding="utf-8")
    status.chmod(0o600)
    monkeypatch.setattr(alice_maintenance, "STATUS_LOG", status)
    assert alice_maintenance._run() is None


def test_alice_connector_fails_closed_on_stale_or_failed_artifact(monkeypatch, tmp_path):
    status = tmp_path / "orion_nerve_status.log"
    status.write_text(json.dumps({
        "ts": "2020-01-01T00:00:00+00:00",
        "status": "FAIL",
        "db_user": "seal",
        "login_http": 0,
        "fails": ["synthetic failure"],
    }) + "\n", encoding="utf-8")
    status.chmod(0o600)
    monkeypatch.setattr(alice_maintenance, "STATUS_LOG", status)
    result = alice_maintenance._run()
    assert result is not None
    assert "stale" in result or "synthetic failure" in result
