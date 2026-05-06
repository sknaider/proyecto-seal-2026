"""NEXUS daemon — persistent autonomous agent.

Spec: spec_nexus_kernel_soul_v1.md §3
Lock invariant: only one instance per agent_id (fcntl exclusive).

Usage:
  python3 nexus_daemon.py             # start
  python3 nexus_daemon.py --stop      # graceful shutdown via PID file
  python3 nexus_daemon.py --status    # check if running

Lock:   /tmp/nexus_daemon_NEXUS.lock
PID:    /tmp/nexus_daemon_NEXUS.pid
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_NEXUS_HOME = Path(__file__).resolve().parent
_KERNEL = _NEXUS_HOME / "kernel"
_HANDLERS = _NEXUS_HOME / "handlers"
sys.path.insert(0, str(_KERNEL))
sys.path.insert(0, str(_HANDLERS))

AGENT_ID = "NEXUS"
LOCK_PATH = Path(f"/tmp/nexus_daemon_{AGENT_ID}.lock")
PID_PATH = Path(f"/tmp/nexus_daemon_{AGENT_ID}.pid")
STATE_PATH = _NEXUS_HOME / "state" / "working_state.json"

_start_time: float = 0.0
_stop_event: asyncio.Event | None = None


def _load_env() -> None:
    env_path = _NEXUS_HOME / "nexus.env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


# ── Lock management ──────────────────────────────────────────────────────────

def _acquire_lock() -> int:
    fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(
            f"[NEXUS/daemon] FATAL: another daemon instance is already running "
            f"(lock held at {LOCK_PATH}). Use --stop to terminate it first.",
            file=sys.stderr,
        )
        os.close(fd)
        sys.exit(1)
    return fd


def _write_pid() -> None:
    PID_PATH.write_text(str(os.getpid()))


def _clear_pid() -> None:
    try:
        PID_PATH.unlink(missing_ok=True)
    except Exception:
        pass


# ── Shutdown ─────────────────────────────────────────────────────────────────

def _handle_sigterm(*_: object) -> None:
    print("\n[NEXUS/daemon] SIGTERM received — shutting down gracefully", flush=True)
    if _stop_event:
        _stop_event.set()


# ── Status ───────────────────────────────────────────────────────────────────

def _daemon_status() -> None:
    if not PID_PATH.exists():
        print("[NEXUS/daemon] not running (no PID file)")
        return
    try:
        pid = int(PID_PATH.read_text().strip())
    except Exception:
        print("[NEXUS/daemon] PID file corrupt")
        return
    try:
        os.kill(pid, 0)
        print(f"[NEXUS/daemon] running — PID={pid}, lock={LOCK_PATH}")
    except ProcessLookupError:
        print(f"[NEXUS/daemon] stale PID file (pid={pid} not alive) — cleaning")
        _clear_pid()


def _daemon_stop() -> None:
    if not PID_PATH.exists():
        print("[NEXUS/daemon] not running")
        return
    try:
        pid = int(PID_PATH.read_text().strip())
    except Exception:
        print("[NEXUS/daemon] PID file corrupt — clearing")
        _clear_pid()
        return
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"[NEXUS/daemon] sent SIGTERM to PID={pid}")
    except ProcessLookupError:
        print(f"[NEXUS/daemon] PID={pid} not alive — clearing stale PID file")
        _clear_pid()


# ── Working state ────────────────────────────────────────────────────────────

def _update_state(status: str) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        state: dict = {}
        if STATE_PATH.exists():
            try:
                state = json.loads(STATE_PATH.read_text())
            except Exception:
                state = {}
        state.setdefault("daemon", {})
        state["daemon"].update({
            "status": status,
            "pid": os.getpid(),
            "started_at": datetime.fromtimestamp(_start_time, tz=timezone.utc).isoformat(),
            "uptime_s": round(time.monotonic() - _start_time, 1),
            "execute_mode": os.environ.get("NEXUS_EXECUTE_MODE", "propose"),
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        })
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    except Exception as ex:
        print(f"[NEXUS/daemon] state update failed: {ex}", flush=True)


# ── Heartbeat ────────────────────────────────────────────────────────────────

async def _heartbeat_loop(stop_event: asyncio.Event, interval_s: float = 30.0) -> None:
    while not stop_event.is_set():
        _update_state("running")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


# ── Main runner ──────────────────────────────────────────────────────────────

async def _run(poll_interval_s: float) -> None:
    global _stop_event
    _stop_event = asyncio.Event()

    from nexus_handlers import event_loop
    from health_monitor import health_loop

    _update_state("starting")
    print(
        f"[NEXUS/daemon] {AGENT_ID} starting — PID={os.getpid()}, "
        f"poll={poll_interval_s}s, mode={os.environ.get('NEXUS_EXECUTE_MODE', 'propose')}",
        flush=True,
    )

    health_interval = float(os.environ.get("NEXUS_HEALTH_INTERVAL", 900))

    try:
        await asyncio.gather(
            event_loop(stop_event=_stop_event, poll_interval_s=poll_interval_s),
            health_loop(stop_event=_stop_event, interval_s=health_interval),
            _heartbeat_loop(stop_event=_stop_event, interval_s=30.0),
        )
    except asyncio.CancelledError:
        pass
    finally:
        _update_state("stopped")
        print(
            f"[NEXUS/daemon] all loops stopped — "
            f"uptime={time.monotonic() - _start_time:.1f}s",
            flush=True,
        )


def main() -> None:
    global _start_time

    parser = argparse.ArgumentParser(description=f"NEXUS daemon — {AGENT_ID}")
    parser.add_argument("--poll", type=float, default=3.0, help="Event poll interval (seconds)")
    parser.add_argument("--stop", action="store_true", help="Stop running daemon")
    parser.add_argument("--status", action="store_true", help="Show daemon status")
    args = parser.parse_args()

    if args.status:
        _daemon_status()
        return

    if args.stop:
        _daemon_stop()
        return

    _load_env()

    lock_fd = _acquire_lock()
    _start_time = time.monotonic()
    _write_pid()

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    print(
        f"[NEXUS/daemon] lock acquired — PID={os.getpid()}, "
        f"lock={LOCK_PATH}, pid_file={PID_PATH}",
        flush=True,
    )

    try:
        asyncio.run(_run(poll_interval_s=args.poll))
    finally:
        _clear_pid()
        try:
            os.close(lock_fd)
        except Exception:
            pass
        LOCK_PATH.unlink(missing_ok=True)
        print("[NEXUS/daemon] lock released, PID file cleared", flush=True)


if __name__ == "__main__":
    main()
