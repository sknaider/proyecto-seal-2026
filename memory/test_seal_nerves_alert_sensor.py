"""Regression tests for NERVES alert sensing and persistent dedup."""

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import seal_nerves


def test_presense_does_not_consume_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(seal_nerves, "_ALERT_LOG_DIR", tmp_path)
    line = "2026-07-22 ERROR test connector failed"

    assert not seal_nerves._is_alert_duplicate(
        line, "error", "ADA", mark_seen=False
    )
    assert not seal_nerves._alert_seen_path("ADA").exists()

    seal_nerves._mark_alert_context_seen(
        "ADA", {"errors": [{"line": line, "severity": "error"}]}
    )

    assert seal_nerves._is_alert_duplicate(
        line, "error", "ADA", mark_seen=False
    )


def test_critical_health_failure_is_immediate_service_down_stimulus():
    stimulus = seal_nerves._alert_stimulus(
        {
            "errors": [
                {
                    "line": "SOUL API health failed",
                    "severity": "error",
                    "type": "native_api_down",
                }
            ]
        }
    )

    assert stimulus == ("service_down", 1.0)
    assert seal_nerves.STIMULI[stimulus[0]] >= seal_nerves.TANKS["alert_drive"]["threshold"]


def test_agent_specific_sensor_is_used_before_threshold(monkeypatch):
    expected = {
        "errors": [{"line": "ERROR audit mismatch", "severity": "error"}],
        "count": 1,
    }

    async def fake_nexus(*, mark_seen=True):
        assert mark_seen is False
        return expected

    monkeypatch.setattr(seal_nerves, "_sense_alert_drive_nexus", fake_nexus)

    result = asyncio.run(seal_nerves._sense_alert_context("NEXUS", mark_seen=False))

    assert result is expected


def test_jarvis_task_drive_has_effective_cooldown():
    override = seal_nerves.AGENT_TANK_OVERRIDES["JARVIS"]["task_drive"]

    assert override["cooldown_s"] >= 30 * 60


def test_tail_log_rejects_stale_file_and_old_timestamp(tmp_path):
    path = tmp_path / "service.log"
    old = datetime.now().astimezone() - timedelta(hours=2)
    path.write_text(f"{old:%Y-%m-%d %H:%M:%S} ERROR historical\n")
    os.utime(path, (time.time(), time.time()))

    assert seal_nerves._tail_log(str(path), max_age_s=60) == []

    path.write_text("ERROR unstructured but file is stale\n")
    stale = time.time() - 3600
    os.utime(path, (stale, stale))

    assert seal_nerves._tail_log(str(path), max_age_s=60) == []


def test_agent_chat_transcripts_are_not_error_log_sources():
    all_sources = {
        *seal_nerves.LOG_SOURCES_ADA.values(),
        *seal_nerves.LOG_SOURCES_ALICE.values(),
        *seal_nerves.LOG_SOURCES_NEXUS.values(),
        *seal_nerves.LOG_SOURCES_DUM.values(),
    }

    assert not any(path.endswith("_messages.jsonl") for path in all_sources)


def test_task_sensor_stimulates_only_new_or_transitioned_state(tmp_path, monkeypatch):
    monkeypatch.setattr(seal_nerves, "_ALERT_LOG_DIR", tmp_path)
    baseline = [
        {"id": "1", "title": "stable", "deadline_state": "none"},
        {"id": "2", "title": "due later", "deadline_state": "due_24h"},
    ]

    assert not any(seal_nerves._task_state_changes("ADA", baseline).values())
    assert not any(seal_nerves._task_state_changes("ADA", baseline).values())

    changed = [
        {"id": "1", "title": "stable", "deadline_state": "overdue_1h"},
        {"id": "2", "title": "due later", "deadline_state": "due_24h"},
        {"id": "3", "title": "new", "deadline_state": "none"},
    ]
    delta = seal_nerves._task_state_changes("ADA", changed)

    assert delta["new_pending"] == 1
    assert delta["overdue_1h"] == 1
    assert sum(delta.values()) == 2
