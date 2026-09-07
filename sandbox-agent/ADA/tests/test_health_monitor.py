"""Tests for ADA health_monitor — snapshot + GPU stats."""
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
    assert snap["agent"] == "ADA"
    assert "ts" in snap
    assert "uptime_s" in snap
    assert "ocean" in snap
    assert "gpu" in snap


def test_write_snapshot_creates_file(isolated_path):
    path = health_monitor.write_snapshot()
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["agent"] == "ADA"


def test_snapshot_includes_violation_count():
    snap = health_monitor.snapshot()
    assert "identity_violations_24h" in snap


def test_gpu_stats_returns_dict():
    """gpu_stats() returns dict — either real GPU values or {error: ...}."""
    stats = health_monitor.gpu_stats()
    assert isinstance(stats, dict)
    # Either has real fields or has an 'error' key
    assert "error" in stats or all(k in stats for k in ("temp_c", "util_pct", "mem_used_mb"))


def test_gpu_stats_handles_missing_nvidia_smi(monkeypatch):
    """If nvidia-smi not installed, returns {error: '...'} without raising."""
    import subprocess
    def fake_check_output(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi not found")
    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    stats = health_monitor.gpu_stats()
    assert "error" in stats


def test_snapshot_uptime_monotonic():
    import time
    s1 = health_monitor.snapshot()
    time.sleep(0.05)
    s2 = health_monitor.snapshot()
    assert s2["uptime_s"] >= s1["uptime_s"]
