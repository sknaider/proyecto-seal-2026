"""Tests for NEXUS health_monitor.

Covers individual checks (mocked I/O) + aggregate run_check_once + log writing.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_KERNEL = Path(__file__).resolve().parent.parent / "kernel"
sys.path.insert(0, str(_KERNEL))

import health_monitor  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_logs(tmp_path, monkeypatch):
    """Redirect HEALTH_LOG and HEALTH_STATE to tmp."""
    monkeypatch.setattr(health_monitor, "HEALTH_LOG", tmp_path / "health.jsonl")
    monkeypatch.setattr(health_monitor, "HEALTH_STATE", tmp_path / "health_state.json")
    yield tmp_path


# ── _check_disk ───────────────────────────────────────────────────────────────

def test_check_disk_returns_tuple_of_three():
    name, ok, detail = asyncio.run(health_monitor._check_disk())
    assert name.startswith("Disk")
    assert isinstance(ok, bool)
    assert "%" in detail


def test_check_disk_warn_threshold(monkeypatch):
    """Disk at 91% should mark NOT ok and include WARN."""
    class FakeUsage:
        used = 91
        total = 100

    monkeypatch.setattr(health_monitor.shutil, "disk_usage", lambda p: FakeUsage)
    name, ok, detail = asyncio.run(health_monitor._check_disk())
    assert ok is False
    assert "WARN" in detail or "CRITICAL" in detail


def test_check_disk_critical_threshold(monkeypatch):
    class FakeUsage:
        used = 96
        total = 100

    monkeypatch.setattr(health_monitor.shutil, "disk_usage", lambda p: FakeUsage)
    _, ok, detail = asyncio.run(health_monitor._check_disk())
    assert ok is False
    assert "CRITICAL" in detail


def test_check_disk_normal(monkeypatch):
    class FakeUsage:
        used = 50
        total = 100

    monkeypatch.setattr(health_monitor.shutil, "disk_usage", lambda p: FakeUsage)
    _, ok, _ = asyncio.run(health_monitor._check_disk())
    assert ok is True


# ── _check_postgres ───────────────────────────────────────────────────────────

def test_check_postgres_unreachable_returns_false(monkeypatch):
    async def fake_open(_h, _p):
        raise ConnectionRefusedError("nope")

    monkeypatch.setattr(health_monitor.asyncio, "open_connection", fake_open)
    name, ok, detail = asyncio.run(health_monitor._check_postgres())
    assert name == "PostgreSQL SOUL"
    assert ok is False
    assert "5433" in detail


# ── _check_process ────────────────────────────────────────────────────────────

def test_check_process_finds_pids(monkeypatch):
    class FakeProc:
        async def communicate(self):
            return (b"12345\n67890", b"")

    async def fake_create(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(health_monitor.asyncio, "create_subprocess_exec", fake_create)
    name, ok, detail = asyncio.run(health_monitor._check_process("Test", "test_pattern"))
    assert ok is True
    assert "12345" in detail


def test_check_process_no_match_no_flag(monkeypatch):
    class FakeProc:
        async def communicate(self):
            return (b"", b"")

    async def fake_create(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(health_monitor.asyncio, "create_subprocess_exec", fake_create)
    _, ok, detail = asyncio.run(health_monitor._check_process("X", "nonexistent_pattern_xyz"))
    assert ok is False
    assert "not running" in detail


def test_check_process_intentional_stop_flag(monkeypatch, tmp_path):
    """If pattern not found but a stop flag exists, treat as ok."""
    class FakeProc:
        async def communicate(self):
            return (b"", b"")

    async def fake_create(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(health_monitor.asyncio, "create_subprocess_exec", fake_create)
    flag_path = Path("/tmp/myproc_intentional_stop")
    flag_path.touch()
    try:
        _, ok, detail = asyncio.run(health_monitor._check_process("MyProc", "myproc"))
        assert ok is True
        assert "intentionally" in detail
    finally:
        flag_path.unlink(missing_ok=True)


# ── run_check_once ────────────────────────────────────────────────────────────

def test_run_check_once_returns_all_results(monkeypatch):
    """Mock all checks to return predictable values; verify count and shape."""
    fake_checks = [
        AsyncMock(return_value=("CheckA", True, "ok")),
        AsyncMock(return_value=("CheckB", False, "fail")),
    ]
    monkeypatch.setattr(health_monitor, "CHECKS", fake_checks)
    results = asyncio.run(health_monitor.run_check_once())
    assert len(results) == 2
    assert results[0] == ("CheckA", True, "ok")
    assert results[1] == ("CheckB", False, "fail")


def test_run_check_once_handles_check_exception(monkeypatch):
    """A check that raises should produce a (unknown_check, False, str(ex)) entry."""
    async def boom():
        raise RuntimeError("synthetic")

    monkeypatch.setattr(health_monitor, "CHECKS", [boom])
    results = asyncio.run(health_monitor.run_check_once())
    assert len(results) == 1
    name, ok, detail = results[0]
    assert ok is False
    assert "synthetic" in detail


# ── Logging ───────────────────────────────────────────────────────────────────

def test_log_tick_writes_jsonl_and_state(isolated_logs):
    results = [("a", True, "ok"), ("b", False, "fail")]
    health_monitor._log_tick(results)
    assert health_monitor.HEALTH_LOG.exists()
    line = health_monitor.HEALTH_LOG.read_text().strip()
    entry = json.loads(line)
    assert entry["agent"] == "NEXUS"
    assert len(entry["checks"]) == 2
    state = json.loads(health_monitor.HEALTH_STATE.read_text())
    assert state["agent"] == "NEXUS"


def test_log_tick_appends_entries(isolated_logs):
    health_monitor._log_tick([("a", True, "ok")])
    health_monitor._log_tick([("b", True, "ok")])
    lines = health_monitor.HEALTH_LOG.read_text().splitlines()
    assert len(lines) == 2
