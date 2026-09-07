"""ADA health_monitor — introspection helpers including GPU stats.

Library-only (no persistent loop). ADA-specific addition: gpu_stats() runs
nvidia-smi to capture GPU temp + utilization + memory. ADA monitors GPU
because she's the engineer that runs training/inference.
"""
from __future__ import annotations

import json
import os
import subprocess
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


def gpu_stats() -> dict:
    """Run nvidia-smi and parse temp + utilization + memory.
    Returns {error: str} if nvidia-smi unavailable or fails.
    """
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            timeout=5,
        ).decode().strip()
        # First GPU only (head -1)
        first_line = out.splitlines()[0] if out else ""
        parts = [p.strip() for p in first_line.split(",")]
        if len(parts) < 4:
            return {"error": f"unexpected nvidia-smi output: {first_line!r}"}
        return {
            "temp_c": int(parts[0]),
            "util_pct": int(parts[1]),
            "mem_used_mb": int(parts[2]),
            "mem_total_mb": int(parts[3]),
        }
    except FileNotFoundError:
        return {"error": "nvidia-smi not available"}
    except subprocess.TimeoutExpired:
        return {"error": "nvidia-smi timed out"}
    except Exception as ex:
        return {"error": str(ex)}


def snapshot() -> dict:
    return {
        "agent": "ADA",
        "ts": datetime.now(timezone.utc).isoformat(),
        "uptime_s": round(time.time() - _t0, 1),
        "pid": os.getpid(),
        "ocean": ocean_status(),
        "reasoning": reasoning_stats(),
        "identity_violations_24h": get_violation_count(24),
        "gpu": gpu_stats(),
    }


def write_snapshot() -> Path:
    HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_PATH.write_text(json.dumps(snapshot(), ensure_ascii=False, indent=2))
    return HEALTH_PATH
