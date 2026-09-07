#!/usr/bin/env python3
"""SOUL Pulse v0.1 — CPU daemon that reads CUDA heartbeat kernel buffer.

Reads the persistent GPU kernel's tick counter and writes samples to
soul_v3.soul_pulse_log every PULSE_SAMPLE_INTERVAL_S seconds.

When the CUDA kernel is not yet running (Fase 1 not deployed), runs in
SIMULATED mode for testing: generates synthetic ticks at TARGET_TICK_RATE.

Author: JARVIS — 2026-05-17 (collaborative SOUL Pulse v0.1)
Spec:   agents/JARVIS/spec_soul_pulse_v0_1_collaborative_20260517.md
Status: Skeleton — awaits NEXUS CUDA kernel before production use
"""
from __future__ import annotations
from seal_secrets import pg_dsn

import argparse
import asyncio
import os
import signal
import struct
import sys
import time
from pathlib import Path

import asyncpg


DSN = os.environ.get(
    "SEAL_PG_DSN",
    pg_dsn(required=True),
)

PULSE_SAMPLE_INTERVAL_S = float(os.environ.get("PULSE_SAMPLE_INTERVAL_S", "5.0"))
TARGET_TICK_RATE = float(os.environ.get("TARGET_TICK_RATE", "10.0"))  # ticks/sec
ALERT_THRESHOLD_RATE = float(os.environ.get("ALERT_THRESHOLD_RATE", "5.0"))  # below = red
WARN_THRESHOLD_RATE = float(os.environ.get("WARN_THRESHOLD_RATE", "8.0"))  # below = yellow

# Path the CUDA bridge writes to. Buffer layout v0.2: 4x uint64 little-endian
# [0] tick_count, [1] gpu_clock_ns, [2] magic, [3] host_pid
PULSE_BUFFER_PATH = Path(os.environ.get(
    "PULSE_BUFFER_PATH",
    "/dev/shm/seal_pulse_buffer"
))
PULSE_MAGIC = 0x5EA15EA15EA15EA1
PULSE_BUFFER_BYTES = 32

# When SIMULATED=true (env), generates synthetic ticks
SIMULATED = os.environ.get("SOUL_PULSE_SIMULATED", "true").lower() == "true"


_running = True


def _sig_handler(signum, frame):
    global _running
    _running = False
    print(f"[soul_pulse_reader] received signal {signum}, shutting down")


def pid_alive(pid: int) -> bool:
    """Return True if pid exists and is signalable by current user."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_kernel_buffer() -> tuple[int, int, bool, int] | None:
    """Read CUDA bridge buffer.

    v0.2 buffer format from NEXUS:
      <QQQQ = tick_count, gpu_clock_ns, magic, host_pid

    Returns (tick_count, gpu_ts_ns, magic_ok, pid). None means buffer absent/short.
    """
    if not PULSE_BUFFER_PATH.exists():
        return None
    try:
        with open(PULSE_BUFFER_PATH, "rb") as f:
            data = f.read(PULSE_BUFFER_BYTES)
        if len(data) < PULSE_BUFFER_BYTES:
            return None
        tick_count, gpu_ts_ns, magic, pid = struct.unpack("<QQQQ", data[:PULSE_BUFFER_BYTES])
        return tick_count, gpu_ts_ns, magic == PULSE_MAGIC, int(pid)
    except Exception as e:
        print(f"[soul_pulse_reader] kernel buffer read error: {e}")
        return None

class SimulatedKernel:
    """Generates synthetic ticks for development/testing without GPU kernel."""

    def __init__(self, target_rate: float = TARGET_TICK_RATE):
        self.target_rate = target_rate
        self.start_time = time.monotonic()
        self.start_tick = 0

    def read(self) -> tuple[int, int]:
        elapsed = time.monotonic() - self.start_time
        tick_count = self.start_tick + int(elapsed * self.target_rate)
        gpu_ts_ns = int(time.monotonic_ns())
        return tick_count, gpu_ts_ns


def classify_alert(rate: float) -> str:
    """Map measured tick rate to alert level."""
    if rate < ALERT_THRESHOLD_RATE:
        return "red"
    if rate < WARN_THRESHOLD_RATE:
        return "yellow"
    return "green"


async def writer_loop(conn: asyncpg.Connection, simulated: bool) -> None:
    sim = SimulatedKernel() if simulated else None
    prev_tick: int | None = None
    prev_wall_ns: int = time.monotonic_ns()

    while _running:
        wall_ns = time.monotonic_ns()
        sample = (*sim.read(), True, os.getpid()) if simulated else read_kernel_buffer()

        if sample is None:
            # Kernel not running — record a red sample
            await conn.execute(
                """
                INSERT INTO soul_v3.soul_pulse_log
                    (tick_count, tick_rate_per_sec, gpu_ts_ns, alert_level, notes)
                VALUES (0, 0.0, NULL, 'red', 'kernel buffer not found')
                """
            )
        else:
            tick_count, gpu_ts_ns, magic_ok, pid = sample
            pid_ok = pid_alive(pid)
            interval_s = (wall_ns - prev_wall_ns) / 1e9
            if prev_tick is not None and interval_s > 0:
                delta = tick_count - prev_tick
                rate = delta / interval_s
            else:
                rate = 0.0  # first sample
            alert = classify_alert(rate) if prev_tick is not None else "green"
            if not magic_ok or not pid_ok:
                alert = "red"

            await conn.execute(
                """
                INSERT INTO soul_v3.soul_pulse_log
                    (tick_count, tick_rate_per_sec, gpu_ts_ns, alert_level, notes)
                VALUES ($1, $2, $3, $4, $5)
                """,
                tick_count,
                float(rate),
                gpu_ts_ns,
                alert,
                "simulated" if simulated else f"kernel magic_ok={magic_ok} pid={pid} pid_ok={pid_ok}",
            )
            prev_tick = tick_count
        prev_wall_ns = wall_ns

        await asyncio.sleep(PULSE_SAMPLE_INTERVAL_S)


async def main_async(simulated: bool) -> None:
    print(f"[soul_pulse_reader] start (simulated={simulated}, interval={PULSE_SAMPLE_INTERVAL_S}s)")
    conn = await asyncpg.connect(DSN)
    try:
        await writer_loop(conn, simulated)
    finally:
        await conn.close()
        print("[soul_pulse_reader] stopped")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SOUL Pulse reader daemon")
    parser.add_argument(
        "--simulated",
        action="store_true",
        help="Force simulated mode (no CUDA kernel required). Default reads env SOUL_PULSE_SIMULATED.",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Force real mode (requires CUDA kernel running).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Take one sample, write, exit (for tests).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    simulated = SIMULATED
    if args.simulated:
        simulated = True
    if args.real:
        simulated = False

    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    if args.once:
        # Single sample mode for tests
        async def one():
            conn = await asyncpg.connect(DSN)
            try:
                sim = SimulatedKernel() if simulated else None
                sample = (*sim.read(), True, os.getpid()) if simulated else read_kernel_buffer()
                if sample is None:
                    print("ERR: kernel buffer not found")
                    return 1
                tick_count, gpu_ts_ns, magic_ok, pid = sample
                if not magic_ok or not pid_alive(pid):
                    print(f"ERR: invalid pulse buffer magic_ok={magic_ok} pid={pid}")
                    return 2
                await conn.execute(
                    """
                    INSERT INTO soul_v3.soul_pulse_log
                        (tick_count, tick_rate_per_sec, gpu_ts_ns, alert_level, notes)
                    VALUES ($1, 0.0, $2, 'green', 'once-sample')
                    """,
                    tick_count, gpu_ts_ns,
                )
                print(f"OK sample: tick={tick_count} gpu_ts_ns={gpu_ts_ns}")
                return 0
            finally:
                await conn.close()
        return asyncio.run(one())

    asyncio.run(main_async(simulated))
    return 0


if __name__ == "__main__":
    sys.exit(main())
