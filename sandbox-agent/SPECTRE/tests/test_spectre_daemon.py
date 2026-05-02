"""SPECTRE daemon launcher tests — fcntl lock + PID lifecycle.

D1: _acquire_lock succeeds on first call, second call → sys.exit(1)
D2: _write_pid writes current PID to PID_PATH
D3: _clear_pid removes PID file without error (missing_ok)
D4: _daemon_status prints "not running" when no PID file exists
D5: _daemon_status detects stale PID (process not alive) → clears it
D6: _daemon_stop sends SIGTERM to correct PID
D7: _update_daemon_state writes daemon section to working_state.json
D8: argparse --stop flag triggers _daemon_stop path (not asyncio.run)
"""
from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))

import spectre_daemon as daemon

_pass = 0
_fail = 0


def test(name: str, fn) -> None:
    global _pass, _fail
    num = _pass + _fail + 1
    try:
        fn()
        print(f"  ✅ D{num}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ D{num}: {name} — {e}")
        _fail += 1


print("\n=== TEST SPECTRE DAEMON ===")


# ─────────────────────────────────────────────────────────────────────────────
# D1: _acquire_lock — first call succeeds, second → sys.exit(1)
# ─────────────────────────────────────────────────────────────────────────────

def d1_lock_exclusive():
    with tempfile.TemporaryDirectory() as d:
        lock_path = Path(d) / "test.lock"
        orig_lock = daemon.LOCK_PATH
        daemon.LOCK_PATH = lock_path
        try:
            fd = daemon._acquire_lock()
            assert fd >= 0, "First lock should succeed"

            # Second attempt must sys.exit(1)
            exited = False
            try:
                daemon._acquire_lock()
            except SystemExit as e:
                assert e.code == 1, f"Expected exit code 1, got {e.code}"
                exited = True
            assert exited, "Second lock attempt should call sys.exit(1)"

            os.close(fd)
        finally:
            daemon.LOCK_PATH = orig_lock

test("_acquire_lock: first succeeds, second → sys.exit(1)", d1_lock_exclusive)


# ─────────────────────────────────────────────────────────────────────────────
# D2: _write_pid writes current PID
# ─────────────────────────────────────────────────────────────────────────────

def d2_write_pid():
    with tempfile.TemporaryDirectory() as d:
        pid_path = Path(d) / "test.pid"
        orig = daemon.PID_PATH
        daemon.PID_PATH = pid_path
        try:
            daemon._write_pid()
            assert pid_path.exists(), "PID file should be created"
            written = int(pid_path.read_text().strip())
            assert written == os.getpid(), f"Expected {os.getpid()}, got {written}"
        finally:
            daemon.PID_PATH = orig

test("_write_pid: writes current PID to PID_PATH", d2_write_pid)


# ─────────────────────────────────────────────────────────────────────────────
# D3: _clear_pid removes file, also no-ops when missing
# ─────────────────────────────────────────────────────────────────────────────

def d3_clear_pid():
    with tempfile.TemporaryDirectory() as d:
        pid_path = Path(d) / "test.pid"
        orig = daemon.PID_PATH
        daemon.PID_PATH = pid_path
        try:
            pid_path.write_text("12345")
            daemon._clear_pid()
            assert not pid_path.exists(), "PID file should be removed"

            # Should not raise when file is already gone
            daemon._clear_pid()
        finally:
            daemon.PID_PATH = orig

test("_clear_pid: removes PID file, no-op if already missing", d3_clear_pid)


# ─────────────────────────────────────────────────────────────────────────────
# D4: _daemon_status prints "not running" when no PID file
# ─────────────────────────────────────────────────────────────────────────────

def d4_status_not_running():
    with tempfile.TemporaryDirectory() as d:
        pid_path = Path(d) / "missing.pid"
        orig = daemon.PID_PATH
        daemon.PID_PATH = pid_path
        try:
            output = []
            with mock.patch("builtins.print", side_effect=lambda *a, **kw: output.append(str(a))):
                daemon._daemon_status()
            assert any("not running" in o for o in output), f"Expected 'not running', got: {output}"
        finally:
            daemon.PID_PATH = orig

test("_daemon_status: prints 'not running' when no PID file", d4_status_not_running)


# ─────────────────────────────────────────────────────────────────────────────
# D5: _daemon_status detects stale PID (dead process) → clears it
# ─────────────────────────────────────────────────────────────────────────────

def d5_status_stale_pid():
    with tempfile.TemporaryDirectory() as d:
        pid_path = Path(d) / "stale.pid"
        pid_path.write_text("999999999")  # non-existent PID
        orig = daemon.PID_PATH
        daemon.PID_PATH = pid_path
        try:
            output = []
            with mock.patch("builtins.print", side_effect=lambda *a, **kw: output.append(str(a))):
                daemon._daemon_status()
            assert not pid_path.exists(), "Stale PID file should be cleared"
            assert any("stale" in o.lower() or "not alive" in o.lower() for o in output), \
                f"Expected stale message, got: {output}"
        finally:
            daemon.PID_PATH = orig

test("_daemon_status: stale PID → 'not alive' message + file cleared", d5_status_stale_pid)


# ─────────────────────────────────────────────────────────────────────────────
# D6: _daemon_stop sends SIGTERM to correct PID
# ─────────────────────────────────────────────────────────────────────────────

def d6_stop_sends_sigterm():
    with tempfile.TemporaryDirectory() as d:
        pid_path = Path(d) / "running.pid"
        target_pid = 54321
        pid_path.write_text(str(target_pid))
        orig = daemon.PID_PATH
        daemon.PID_PATH = pid_path
        try:
            killed = []
            with mock.patch("os.kill", side_effect=lambda pid, sig: killed.append((pid, sig))):
                daemon._daemon_stop()
            assert len(killed) == 1, f"Expected 1 kill call, got {killed}"
            assert killed[0] == (target_pid, signal.SIGTERM), f"Expected SIGTERM to {target_pid}, got {killed[0]}"
        finally:
            daemon.PID_PATH = orig

test("_daemon_stop: sends SIGTERM to PID from PID file", d6_stop_sends_sigterm)


# ─────────────────────────────────────────────────────────────────────────────
# D7: _update_daemon_state writes correct fields to working_state.json
# ─────────────────────────────────────────────────────────────────────────────

def d7_update_daemon_state():
    with tempfile.TemporaryDirectory() as d:
        state_path = Path(d) / "working_state.json"
        state_path.write_text(json.dumps({"agent": "SPECTRE", "sandbox": True}))
        orig = daemon.STATE_PATH
        daemon.STATE_PATH = state_path
        daemon._start_time = __import__("time").monotonic()
        try:
            daemon._update_daemon_state("running")
            state = json.loads(state_path.read_text())

            assert "daemon" in state, "working_state must have 'daemon' section"
            d_info = state["daemon"]
            assert d_info["status"] == "running"
            assert d_info["pid"] == os.getpid()
            assert "started_at" in d_info
            assert "uptime_s" in d_info
            assert "last_heartbeat" in d_info
            assert state.get("agent") == "SPECTRE", "Existing fields must be preserved"
        finally:
            daemon.STATE_PATH = orig

test("_update_daemon_state: writes status/pid/uptime/heartbeat to working_state", d7_update_daemon_state)


# ─────────────────────────────────────────────────────────────────────────────
# D8: --stop flag routes to _daemon_stop, does not call asyncio.run
# ─────────────────────────────────────────────────────────────────────────────

def d8_stop_flag_no_asyncio():
    with tempfile.TemporaryDirectory() as d:
        pid_path = Path(d) / "test.pid"
        pid_path.write_text(str(os.getpid()))
        orig = daemon.PID_PATH
        daemon.PID_PATH = pid_path
        try:
            run_called = []
            stop_called = []

            with mock.patch("asyncio.run", side_effect=lambda *a, **kw: run_called.append(1)):
                with mock.patch("os.kill", side_effect=lambda *a: stop_called.append(1)):
                    with mock.patch("sys.argv", ["spectre_daemon.py", "--stop"]):
                        daemon.main()

            assert len(run_called) == 0, "asyncio.run must NOT be called with --stop"
            assert len(stop_called) >= 1, "_daemon_stop should call os.kill"
        finally:
            daemon.PID_PATH = orig

test("--stop flag: calls _daemon_stop, asyncio.run NOT invoked", d8_stop_flag_no_asyncio)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Spectre Daemon Tests: {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
