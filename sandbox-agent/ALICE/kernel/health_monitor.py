"""ALICE health_monitor — introspection helpers for ALICE-Claude.

Library-only. Exposes `snapshot()` and `write_snapshot()` to capture
uptime, OCEAN, reasoning trace stats, and identity violations counter.
ALICE-Claude can call these on demand (e.g. when William asks status).

No persistent loop here — the runtime daemon was removed (William, 04-may-2026).
"""
from __future__ import annotations

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


def write_snapshot() -> Path:
    HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_PATH.write_text(json.dumps(snapshot(), ensure_ascii=False, indent=2))
    return HEALTH_PATH
