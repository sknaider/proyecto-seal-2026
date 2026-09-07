"""SPECTRE daemon launcher — persistent Nivel 2 + Nivel 3 process.

Ref: spec_spectre_contract_v2_1_addendum.md F5 (fcntl lock invariant):
  "El daemon SOLO puede tener una instancia activa por agent_id.
   Lock obligatorio antes de iniciar event loop."

Usage:
  python3 spectre_daemon.py              # start (default poll=5s, max=3/tick)
  python3 spectre_daemon.py --poll 2    # faster polling
  python3 spectre_daemon.py --stop      # graceful shutdown via PID file
  python3 spectre_daemon.py --status    # print PID + uptime if running

Lock:   /tmp/spectre_daemon_SPECTRE.lock
PID:    /tmp/spectre_daemon_SPECTRE.pid
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

_KERNEL = Path(__file__).parent / "kernel"
_HANDLERS = Path(__file__).parent / "handlers"
sys.path.insert(0, str(_KERNEL))
sys.path.insert(0, str(_HANDLERS))

AGENT_ID = "SPECTRE"
LOCK_PATH = Path(f"/tmp/spectre_daemon_{AGENT_ID}.lock")
PID_PATH = Path(f"/tmp/spectre_daemon_{AGENT_ID}.pid")
STATE_PATH = Path(__file__).parent / "state" / "working_state.json"

_start_time: float = 0.0
_stop_event: asyncio.Event | None = None


# ── Lock management ───────────────────────────────────────────────────────────

def _acquire_lock() -> int:
    """Acquire exclusive fcntl lock. Returns fd on success, exits on collision."""
    fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(
            f"[SPECTRE/daemon] FATAL: another daemon instance is already running "
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


# ── Shutdown wiring ───────────────────────────────────────────────────────────

def _handle_sigterm(*_: object) -> None:
    print(f"\n[SPECTRE/daemon] SIGTERM received — shutting down gracefully", flush=True)
    if _stop_event:
        _stop_event.set()


# ── Status helpers ────────────────────────────────────────────────────────────

def _daemon_status() -> None:
    if not PID_PATH.exists():
        print("[SPECTRE/daemon] not running (no PID file)")
        return
    pid = int(PID_PATH.read_text().strip())
    try:
        os.kill(pid, 0)  # probe — raises if process not alive
        print(f"[SPECTRE/daemon] running — PID={pid}, lock={LOCK_PATH}")
    except ProcessLookupError:
        print(f"[SPECTRE/daemon] stale PID file (pid={pid} not alive) — cleaning up")
        _clear_pid()


def _daemon_stop() -> None:
    if not PID_PATH.exists():
        print("[SPECTRE/daemon] not running")
        return
    pid = int(PID_PATH.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"[SPECTRE/daemon] sent SIGTERM to PID={pid}")
    except ProcessLookupError:
        print(f"[SPECTRE/daemon] PID={pid} not alive — clearing stale PID file")
        _clear_pid()


# ── Working state helpers ─────────────────────────────────────────────────────

def _update_daemon_state(status: str) -> None:
    try:
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
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        })
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    except Exception as ex:
        print(f"[SPECTRE/daemon] state update failed: {ex}", flush=True)


# ── Heartbeat ─────────────────────────────────────────────────────────────────

async def _heartbeat_loop(interval_s: float = 30.0) -> None:
    """Periodically write uptime to working_state so DUM can monitor liveness."""
    while True:
        if _stop_event and _stop_event.is_set():
            break
        _update_daemon_state("running")
        await asyncio.sleep(interval_s)


# ── Main runner ───────────────────────────────────────────────────────────────

async def _run(poll_interval_s: float, max_per_tick: int) -> None:
    global _stop_event
    _stop_event = asyncio.Event()

    # Import Nivel 2 + Nivel 3 here so path setup is already done
    from spectre_handlers import event_bus_daemon  # Nivel 2
    from cortex import cortex_loop                  # Nivel 3

    _update_daemon_state("starting")
    print(
        f"[SPECTRE/daemon] {AGENT_ID} starting — "
        f"PID={os.getpid()}, poll={poll_interval_s}s, max={max_per_tick}/tick",
        flush=True,
    )

    try:
        await asyncio.gather(
            event_bus_daemon(stop_event=_stop_event),
            cortex_loop(
                poll_interval_s=poll_interval_s,
                max_per_tick=max_per_tick,
                stop_event=_stop_event,
            ),
            _heartbeat_loop(interval_s=30.0),
        )
    except asyncio.CancelledError:
        pass
    finally:
        _update_daemon_state("stopped")
        print(f"[SPECTRE/daemon] all loops stopped — uptime={time.monotonic() - _start_time:.1f}s", flush=True)


def main() -> None:
    global _start_time

    parser = argparse.ArgumentParser(description=f"SPECTRE daemon — {AGENT_ID}")
    parser.add_argument("--poll", type=float, default=5.0, help="Cortex poll interval (seconds)")
    parser.add_argument("--max-per-tick", type=int, default=3, help="Max cortex entries per tick")
    parser.add_argument("--stop", action="store_true", help="Stop running daemon")
    parser.add_argument("--status", action="store_true", help="Show daemon status")
    args = parser.parse_args()

    if args.status:
        _daemon_status()
        return

    if args.stop:
        _daemon_stop()
        return

    # Start path — acquire lock before anything else (F5 invariant)
    lock_fd = _acquire_lock()
    _start_time = time.monotonic()
    _write_pid()

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    print(
        f"[SPECTRE/daemon] lock acquired — PID={os.getpid()}, "
        f"lock={LOCK_PATH}, pid_file={PID_PATH}",
        flush=True,
    )

    try:
        asyncio.run(_run(poll_interval_s=args.poll, max_per_tick=args.max_per_tick))
    finally:
        _clear_pid()
        try:
            os.close(lock_fd)
        except Exception:
            pass
        LOCK_PATH.unlink(missing_ok=True)
        print(f"[SPECTRE/daemon] lock released, PID file cleared", flush=True)


if __name__ == "__main__":
    main()
