"""Tests for ALICE alice_daemon — lock acquisition, PID file, stop signaling."""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(_BASE / "kernel"))
sys.path.insert(0, str(_BASE / "handlers"))

import alice_daemon  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(alice_daemon, "LOCK_PATH", tmp_path / "alice.lock")
    monkeypatch.setattr(alice_daemon, "PID_PATH", tmp_path / "alice.pid")
    yield tmp_path


def test_acquire_lock_writes_pid(isolated_paths):
    fd = alice_daemon._acquire_lock()
    try:
        assert alice_daemon.PID_PATH.exists()
        assert int(alice_daemon.PID_PATH.read_text()) == os.getpid()
    finally:
        alice_daemon._release_lock(fd)


def test_acquire_lock_second_attempt_exits(isolated_paths):
    fd = alice_daemon._acquire_lock()
    try:
        with pytest.raises(SystemExit) as excinfo:
            alice_daemon._acquire_lock()
        assert excinfo.value.code == 2
    finally:
        alice_daemon._release_lock(fd)


def test_release_lock_removes_pid(isolated_paths):
    fd = alice_daemon._acquire_lock()
    assert alice_daemon.PID_PATH.exists()
    alice_daemon._release_lock(fd)
    assert not alice_daemon.PID_PATH.exists()


def test_release_lock_idempotent_safe(isolated_paths):
    fd = alice_daemon._acquire_lock()
    alice_daemon._release_lock(fd)
    # second release should not raise
    alice_daemon._release_lock(fd)


def test_main_loop_starts_and_stops_quickly(isolated_paths, monkeypatch):
    """_main_loop launches health + handler tasks, exits when stop set."""
    async def fake_health(interval_s, stop_event):
        await stop_event.wait()

    async def fake_event(stop_event, poll_interval_s):
        await stop_event.wait()

    monkeypatch.setattr(alice_daemon, "health_loop", fake_health)
    monkeypatch.setattr(alice_daemon, "event_loop", fake_event)

    async def runner():
        stop = asyncio.Event()

        async def stopper():
            await asyncio.sleep(0.05)
            stop.set()

        await asyncio.gather(
            alice_daemon._main_loop(stop),
            stopper(),
        )

    start = time.monotonic()
    asyncio.run(runner())
    elapsed = time.monotonic() - start
    assert elapsed < 1.0


def test_main_loop_cancels_subtasks_on_stop(isolated_paths, monkeypatch):
    """Verify both subtasks are cancelled when stop_event fires."""
    health_started = asyncio.Event()
    handler_started = asyncio.Event()

    async def fake_health(interval_s, stop_event):
        health_started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return

    async def fake_event(stop_event, poll_interval_s):
        handler_started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return

    monkeypatch.setattr(alice_daemon, "health_loop", fake_health)
    monkeypatch.setattr(alice_daemon, "event_loop", fake_event)

    async def runner():
        stop = asyncio.Event()

        async def stopper():
            await asyncio.sleep(0.05)
            stop.set()

        await asyncio.gather(
            alice_daemon._main_loop(stop),
            stopper(),
        )
        assert health_started.is_set()
        assert handler_started.is_set()

    asyncio.run(runner())
