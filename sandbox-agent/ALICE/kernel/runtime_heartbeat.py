"""ALICE runtime_heartbeat — public liveness signal for DUM and team watchdogs.

Distinct from the internal `health_monitor.snapshot()` (which writes to
`state/health.json`, an introspection blob for ALICE herself). This module
emits a small JSON to the shared `messages/` directory in the format that
`seal_agent_resurrect.sh` and DUM heartbeat checkers already expect for
team agents. Path is namespaced `alice_runtime_*` so it does not collide
with the Claude-session heartbeat file `alice_claude_heartbeat.json`.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

AGENT_ID = "ALICE"
MESSAGES_DIR = Path("/home/dadito/IA/proyecto-seal/messages")
HEARTBEAT_PATH = MESSAGES_DIR / "alice_runtime_heartbeat.json"

_started_monotonic = time.monotonic()


def _payload(extra: dict | None = None) -> dict:
    payload: dict = {
        "agent": AGENT_ID,
        "source": "runtime_daemon",
        "alive": True,
        "pid": os.getpid(),
        "uptime_s": round(time.monotonic() - _started_monotonic, 1),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if extra:
        payload.update(extra)
    return payload


def write_beat(extra: dict | None = None) -> bool:
    if not MESSAGES_DIR.exists():
        return False
    try:
        HEARTBEAT_PATH.write_text(json.dumps(_payload(extra), ensure_ascii=False, indent=2))
        return True
    except Exception as ex:
        print(f"[alice/runtime_heartbeat] write failed: {ex}", flush=True)
        return False


def mark_dead() -> bool:
    """Write a single beat marking the runtime daemon as gone (clean shutdown)."""
    payload = _payload({"alive": False, "source": "runtime_daemon_shutdown"})
    payload["alive"] = False
    try:
        HEARTBEAT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        return True
    except Exception:
        return False
