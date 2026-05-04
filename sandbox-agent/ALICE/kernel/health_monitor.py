"""ALICE health_monitor — periodic self-check loop.

Lightweight: report uptime, LLM tier reachability, OCEAN drift,
reasoning trace stats, identity violations count. Writes to
state/health.json for external observers (DUM).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).parent.parent
HEALTH_PATH = _BASE / "state" / "health.json"

sys.path.insert(0, str(_BASE / "kernel"))
from identity_integrity import get_violation_count  # noqa: E402
from reasoning_logger import stats as reasoning_stats  # noqa: E402
from ocean_runtime import status as ocean_status  # noqa: E402


_t0 = time.time()


def snapshot() -> dict:
    return {
        "agent": "ALICE",
        "ts": datetime.now(timezone.utc).isoformat(),
        "uptime_s": round(time.time() - _t0, 1),
        "pid": os.getpid(),
        "ocean": ocean_status(),
        "reasoning": reasoning_stats(),
        "identity_violations_24h": get_violation_count(24),
    }


def write_snapshot() -> None:
    HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_PATH.write_text(json.dumps(snapshot(), ensure_ascii=False, indent=2))


async def health_loop(interval_s: int = 900, stop_event: asyncio.Event | None = None) -> None:
    print(f"[alice/health] loop started — interval={interval_s}s", flush=True)
    while True:
        if stop_event and stop_event.is_set():
            break
        try:
            write_snapshot()
        except Exception as ex:
            print(f"[alice/health] snapshot failed: {ex}", flush=True)
        try:
            await asyncio.wait_for(stop_event.wait() if stop_event else asyncio.sleep(interval_s), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
        if stop_event and stop_event.is_set():
            break
    print(f"[alice/health] loop stopped", flush=True)
