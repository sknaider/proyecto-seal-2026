"""ALICE daemon — async loop for monitoring + on-demand analysis dispatch.

Different from NEXUS daemon:
  - No shell execution (analyst, not executor)
  - Lighter polling (analysis is on-demand, not real-time alerts)
  - Health monitor + alert engine run in background

Lock: /tmp/alice_daemon_ALICE.lock (fcntl exclusive, 1 instance only).
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import os
import signal
import sys
from pathlib import Path

_ALICE_HOME = Path(__file__).resolve().parent
_KERNEL = _ALICE_HOME / "kernel"
_HANDLERS = _ALICE_HOME / "handlers"
sys.path.insert(0, str(_KERNEL))
sys.path.insert(0, str(_HANDLERS))

from health_monitor import health_loop  # noqa: E402
from alice_handlers import event_loop  # noqa: E402

LOCK_PATH = Path("/tmp/alice_daemon_ALICE.lock")
PID_PATH = Path("/tmp/alice_daemon_ALICE.pid")


def _acquire_lock() -> int:
    fd = os.open(str(LOCK_PATH), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"[alice/daemon] another instance holds {LOCK_PATH} — exiting", flush=True)
        os.close(fd)
        sys.exit(2)
    PID_PATH.write_text(str(os.getpid()))
    return fd


def _release_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        if PID_PATH.exists():
            PID_PATH.unlink()
    except Exception:
        pass


async def _main_loop(stop_event: asyncio.Event) -> None:
    print(f"[alice/daemon] started pid={os.getpid()}", flush=True)
    health_task = asyncio.create_task(
        health_loop(
            interval_s=int(os.environ.get("ALICE_HEALTH_INTERVAL", "900")),
            stop_event=stop_event,
        )
    )
    handler_task = asyncio.create_task(
        event_loop(
            stop_event=stop_event,
            poll_interval_s=float(os.environ.get("ALICE_POLL_INTERVAL", "3.0")),
        )
    )
    await stop_event.wait()
    for t in (health_task, handler_task):
        t.cancel()
    for t in (health_task, handler_task):
        try:
            await t
        except asyncio.CancelledError:
            pass
    print(f"[alice/daemon] stopped", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop", action="store_true", help="signal running daemon to exit")
    parser.add_argument("--status", action="store_true", help="check if running")
    args = parser.parse_args()

    if args.stop:
        if PID_PATH.exists():
            try:
                pid = int(PID_PATH.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                print(f"[alice/daemon] SIGTERM sent to {pid}")
            except (ProcessLookupError, ValueError):
                print("[alice/daemon] no running process")
        else:
            print("[alice/daemon] no pid file")
        return

    if args.status:
        if PID_PATH.exists():
            try:
                pid = int(PID_PATH.read_text().strip())
                os.kill(pid, 0)
                print(f"[alice/daemon] running pid={pid}")
                return
            except (ProcessLookupError, ValueError):
                pass
        print("[alice/daemon] not running")
        return

    fd = _acquire_lock()
    stop_event = asyncio.Event()

    def _shutdown(*_a):
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        asyncio.run(_main_loop(stop_event))
    finally:
        _release_lock(fd)


if __name__ == "__main__":
    main()
