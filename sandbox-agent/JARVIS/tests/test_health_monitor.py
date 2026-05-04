"""Tests for JARVIS health_monitor — snapshot helpers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_KERNEL = Path(__file__).resolve().parent.parent / "kernel"
sys.path.insert(0, str(_KERNEL))

import health_monitor  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_path(tmp_path, monkeypatch):
    monkeypatch.setattr(health_monitor, "HEALTH_PATH", tmp_path / "health.json")
    yield tmp_path


def test_snapshot_shape():
    snap = health_monitor.snapshot()
    assert snap["agent"] == "JARVIS"
    assert isinstance(snap["uptime_s"], (int, float))
    assert isinstance(snap["pid"], int)
    assert "ts" in snap and "T" in snap["ts"]
    assert "ocean" in snap
    assert "reasoning" in snap


def test_write_snapshot_creates_file(isolated_path):
    path = health_monitor.write_snapshot()
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["agent"] == "JARVIS"


def test_snapshot_uptime_monotonic():
    import time
    s1 = health_monitor.snapshot()
    time.sleep(0.05)
    s2 = health_monitor.snapshot()
    assert s2["uptime_s"] >= s1["uptime_s"]


def test_snapshot_includes_violation_count():
    snap = health_monitor.snapshot()
    assert "identity_violations_24h" in snap
    assert isinstance(snap["identity_violations_24h"], int)
