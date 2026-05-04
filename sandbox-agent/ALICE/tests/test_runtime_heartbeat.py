"""Tests for ALICE runtime_heartbeat — public liveness signal."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE / "kernel"))

import runtime_heartbeat  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_path(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_heartbeat, "MESSAGES_DIR", tmp_path)
    monkeypatch.setattr(runtime_heartbeat, "HEARTBEAT_PATH", tmp_path / "alice_runtime_heartbeat.json")
    yield tmp_path


def test_write_beat_creates_file(isolated_path):
    assert runtime_heartbeat.write_beat() is True
    assert runtime_heartbeat.HEARTBEAT_PATH.exists()


def test_write_beat_payload_shape(isolated_path):
    runtime_heartbeat.write_beat()
    data = json.loads(runtime_heartbeat.HEARTBEAT_PATH.read_text())
    assert data["agent"] == "ALICE"
    assert data["alive"] is True
    assert data["source"] == "runtime_daemon"
    assert isinstance(data["pid"], int) and data["pid"] > 0
    assert isinstance(data["uptime_s"], (int, float))
    assert "timestamp" in data and "T" in data["timestamp"]


def test_write_beat_extra_fields_merged(isolated_path):
    runtime_heartbeat.write_beat({"ocean_temperature": 0.4, "reasoning_total": 7})
    data = json.loads(runtime_heartbeat.HEARTBEAT_PATH.read_text())
    assert data["ocean_temperature"] == 0.4
    assert data["reasoning_total"] == 7
    assert data["agent"] == "ALICE"  # core fields preserved


def test_write_beat_returns_true_when_only_event_log_works(tmp_path, monkeypatch):
    """Dual-write contract: True if either JSON OR event_log beat succeeds."""
    bogus = tmp_path / "does_not_exist"
    monkeypatch.setattr(runtime_heartbeat, "MESSAGES_DIR", bogus)
    monkeypatch.setattr(runtime_heartbeat, "HEARTBEAT_PATH", bogus / "x.json")
    monkeypatch.setattr(runtime_heartbeat, "_beat_event_log", lambda extra=None: True)
    assert runtime_heartbeat.write_beat() is True


def test_write_beat_returns_true_when_only_json_works(isolated_path, monkeypatch):
    monkeypatch.setattr(runtime_heartbeat, "_beat_event_log", lambda extra=None: False)
    assert runtime_heartbeat.write_beat() is True
    assert runtime_heartbeat.HEARTBEAT_PATH.exists()


def test_write_beat_returns_false_when_both_channels_fail(tmp_path, monkeypatch):
    bogus = tmp_path / "does_not_exist"
    monkeypatch.setattr(runtime_heartbeat, "MESSAGES_DIR", bogus)
    monkeypatch.setattr(runtime_heartbeat, "HEARTBEAT_PATH", bogus / "x.json")
    monkeypatch.setattr(runtime_heartbeat, "_beat_event_log", lambda extra=None: False)
    assert runtime_heartbeat.write_beat() is False


def test_mark_dead_writes_alive_false(isolated_path):
    runtime_heartbeat.write_beat()
    assert json.loads(runtime_heartbeat.HEARTBEAT_PATH.read_text())["alive"] is True
    assert runtime_heartbeat.mark_dead() is True
    after = json.loads(runtime_heartbeat.HEARTBEAT_PATH.read_text())
    assert after["alive"] is False
    assert after["source"] == "runtime_daemon_shutdown"


def test_uptime_increases(isolated_path):
    import time
    runtime_heartbeat.write_beat()
    first = json.loads(runtime_heartbeat.HEARTBEAT_PATH.read_text())["uptime_s"]
    time.sleep(0.05)
    runtime_heartbeat.write_beat()
    second = json.loads(runtime_heartbeat.HEARTBEAT_PATH.read_text())["uptime_s"]
    assert second >= first
