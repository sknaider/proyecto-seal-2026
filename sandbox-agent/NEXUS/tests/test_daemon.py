"""Tests for NEXUS nexus_daemon.

Covers: env loading, fcntl lock acquisition, PID file management,
_update_state state file refresh, and _heartbeat_loop tick behavior.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import signal
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

_NEXUS_HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_NEXUS_HOME))
sys.path.insert(0, str(_NEXUS_HOME / "kernel"))
sys.path.insert(0, str(_NEXUS_HOME / "handlers"))

import nexus_daemon  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """Redirect LOCK_PATH, PID_PATH, STATE_PATH to tmp."""
    monkeypatch.setattr(nexus_daemon, "LOCK_PATH", tmp_path / "nexus.lock")
    monkeypatch.setattr(nexus_daemon, "PID_PATH", tmp_path / "nexus.pid")
    monkeypatch.setattr(nexus_daemon, "STATE_PATH", tmp_path / "state" / "working_state.json")
    monkeypatch.setattr(nexus_daemon, "_start_time", time.monotonic())
    yield tmp_path


# ── _load_env ─────────────────────────────────────────────────────────────────

def test_load_env_reads_keys(tmp_path, monkeypatch):
    env_file = tmp_path / "nexus.env"
    env_file.write_text("# comment\nNEXUS_TEST_KEY=hello\nEMPTY_LINE_OK=\n")
    monkeypatch.setattr(nexus_daemon, "_NEXUS_HOME", tmp_path)
    # Clear key first to ensure setdefault behavior
    if "NEXUS_TEST_KEY" in os.environ:
        del os.environ["NEXUS_TEST_KEY"]
    nexus_daemon._load_env()
    assert os.environ.get("NEXUS_TEST_KEY") == "hello"


def test_load_env_setdefault_does_not_overwrite(tmp_path, monkeypatch):
    """Pre-existing env vars should be preserved (setdefault semantics)."""
    env_file = tmp_path / "nexus.env"
    env_file.write_text("PRE_SET=newvalue\n")
    monkeypatch.setattr(nexus_daemon, "_NEXUS_HOME", tmp_path)
    os.environ["PRE_SET"] = "original"
    try:
        nexus_daemon._load_env()
        assert os.environ["PRE_SET"] == "original"
    finally:
        del os.environ["PRE_SET"]


def test_load_env_no_file_returns_silently(tmp_path, monkeypatch):
    """Missing nexus.env should not raise."""
    monkeypatch.setattr(nexus_daemon, "_NEXUS_HOME", tmp_path)
    nexus_daemon._load_env()  # no exception expected


# ── PID file management ───────────────────────────────────────────────────────

def test_write_pid_writes_current_pid(isolated_paths):
    nexus_daemon._write_pid()
    content = nexus_daemon.PID_PATH.read_text().strip()
    assert int(content) == os.getpid()


def test_clear_pid_removes_file(isolated_paths):
    nexus_daemon.PID_PATH.write_text("12345")
    nexus_daemon._clear_pid()
    assert not nexus_daemon.PID_PATH.exists()


def test_clear_pid_idempotent(isolated_paths):
    """Calling clear when no PID file should not raise."""
    nexus_daemon._clear_pid()  # no exception


# ── fcntl lock ────────────────────────────────────────────────────────────────

def test_acquire_lock_succeeds_first_time(isolated_paths):
    """First _acquire_lock should not exit; returns FD."""
    fd = nexus_daemon._acquire_lock()
    try:
        assert fd > 0
        assert nexus_daemon.LOCK_PATH.exists()
    finally:
        os.close(fd)
        nexus_daemon.LOCK_PATH.unlink(missing_ok=True)


def test_acquire_lock_second_attempt_fails(isolated_paths):
    """A second concurrent _acquire_lock should sys.exit(1)."""
    fd1 = nexus_daemon._acquire_lock()
    try:
        with pytest.raises(SystemExit) as excinfo:
            nexus_daemon._acquire_lock()
        assert excinfo.value.code == 1
    finally:
        os.close(fd1)
        nexus_daemon.LOCK_PATH.unlink(missing_ok=True)


# ── _update_state ─────────────────────────────────────────────────────────────

def test_update_state_creates_dir_and_file(isolated_paths):
    nexus_daemon._update_state("running")
    assert nexus_daemon.STATE_PATH.exists()
    state = json.loads(nexus_daemon.STATE_PATH.read_text())
    assert state["daemon"]["status"] == "running"
    assert state["daemon"]["pid"] == os.getpid()
    assert "last_heartbeat" in state["daemon"]
    assert "last_updated" in state


def test_update_state_preserves_other_keys(isolated_paths):
    """If state file already has unrelated keys, they survive update."""
    nexus_daemon.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    nexus_daemon.STATE_PATH.write_text(json.dumps({
        "extra": "preserve_me",
        "daemon": {"old": "value"},
    }))
    nexus_daemon._update_state("running")
    state = json.loads(nexus_daemon.STATE_PATH.read_text())
    assert state["extra"] == "preserve_me"
    assert state["daemon"]["status"] == "running"


def test_update_state_overwrites_status_each_call(isolated_paths):
    nexus_daemon._update_state("starting")
    nexus_daemon._update_state("running")
    nexus_daemon._update_state("stopped")
    state = json.loads(nexus_daemon.STATE_PATH.read_text())
    assert state["daemon"]["status"] == "stopped"


# ── _heartbeat_loop ───────────────────────────────────────────────────────────

def test_heartbeat_loop_writes_state_then_stops(isolated_paths):
    """heartbeat_loop should write state and exit when stop_event is set."""
    async def run_with_quick_stop():
        stop = asyncio.Event()

        async def stopper():
            await asyncio.sleep(0.05)
            stop.set()

        await asyncio.gather(
            nexus_daemon._heartbeat_loop(stop, interval_s=10.0),
            stopper(),
        )

    asyncio.run(run_with_quick_stop())
    assert nexus_daemon.STATE_PATH.exists()
    state = json.loads(nexus_daemon.STATE_PATH.read_text())
    assert state["daemon"]["status"] == "running"


def test_heartbeat_loop_exits_immediately_if_stop_already_set(isolated_paths):
    async def run():
        stop = asyncio.Event()
        stop.set()
        await nexus_daemon._heartbeat_loop(stop, interval_s=10.0)

    # Should return almost instantly
    start = time.monotonic()
    asyncio.run(run())
    elapsed = time.monotonic() - start
    assert elapsed < 0.5


# ── Signal handler ────────────────────────────────────────────────────────────

def test_handle_sigterm_sets_stop_event(monkeypatch):
    stop = asyncio.Event()
    monkeypatch.setattr(nexus_daemon, "_stop_event", stop)
    nexus_daemon._handle_sigterm()
    assert stop.is_set()


def test_handle_sigterm_no_stop_event_no_crash(monkeypatch):
    """If _stop_event is None, signal handler should not raise."""
    monkeypatch.setattr(nexus_daemon, "_stop_event", None)
    nexus_daemon._handle_sigterm()  # no exception


# ── _daemon_status ────────────────────────────────────────────────────────────

def test_daemon_status_no_pid_file(isolated_paths, capsys):
    """Status with no PID file should print 'not running'."""
    nexus_daemon._daemon_status()
    captured = capsys.readouterr()
    assert "not running" in captured.out


def test_daemon_status_corrupt_pid_file(isolated_paths, capsys):
    nexus_daemon.PID_PATH.write_text("not_a_number")
    nexus_daemon._daemon_status()
    captured = capsys.readouterr()
    assert "corrupt" in captured.out


def test_daemon_status_stale_pid_file_cleans(isolated_paths, capsys):
    """Status with PID of dead process should clean the file."""
    # PID 1 = init, but os.kill(1, 0) requires root. Use an unlikely PID.
    nexus_daemon.PID_PATH.write_text("99999999")
    nexus_daemon._daemon_status()
    captured = capsys.readouterr()
    assert "stale" in captured.out
    assert not nexus_daemon.PID_PATH.exists()


def test_daemon_status_alive_pid(isolated_paths, capsys):
    nexus_daemon.PID_PATH.write_text(str(os.getpid()))
    nexus_daemon._daemon_status()
    captured = capsys.readouterr()
    assert "running" in captured.out
    assert str(os.getpid()) in captured.out
