"""ALICE runtime_heartbeat — public liveness signal for DUM and team watchdogs.

Two writes per beat:
1. `messages/alice_runtime_heartbeat.json` — debug/introspection JSON
2. `soul_v3.event_log` row via `seal_heartbeat.beat()` — TRUTH (DUM reads here)

Single-writer principle (seal_heartbeat.py): event_log is canonical, JSON is
secondary. Without the event_log row, DUM does not see ALICE as alive.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

AGENT_ID = "ALICE"
MESSAGES_DIR = Path("/home/dadito/IA/proyecto-seal/messages")
HEARTBEAT_PATH = MESSAGES_DIR / "alice_runtime_heartbeat.json"
_SEAL_MEMORY = Path("/home/dadito/IA/proyecto-seal/memory")
if str(_SEAL_MEMORY) not in sys.path:
    sys.path.append(str(_SEAL_MEMORY))

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


def _beat_event_log(extra: dict | None = None) -> bool:
    """Write a heartbeat row into soul_v3.event_log via seal_heartbeat (truth).
    Async-safe: schedule on running loop if available, else use beat_sync.
    """
    try:
        from seal_heartbeat import beat, beat_sync  # type: ignore
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        meta = {"source": "runtime_daemon", **(extra or {})}
        content = f"{AGENT_ID} runtime_daemon heartbeat"
        if loop is not None:
            loop.create_task(beat(AGENT_ID, metadata=meta, content=content))
        else:
            beat_sync(AGENT_ID, metadata=meta, content=content)
        return True
    except Exception as ex:
        print(f"[alice/runtime_heartbeat] event_log beat failed: {ex}", flush=True)
        return False


def write_beat(extra: dict | None = None) -> bool:
    """Write JSON debug file AND event_log row (canonical for DUM)."""
    json_ok = False
    if MESSAGES_DIR.exists():
        try:
            HEARTBEAT_PATH.write_text(json.dumps(_payload(extra), ensure_ascii=False, indent=2))
            json_ok = True
        except Exception as ex:
            print(f"[alice/runtime_heartbeat] json write failed: {ex}", flush=True)
    db_ok = _beat_event_log(extra)
    return json_ok or db_ok


def mark_dead() -> bool:
    """Single beat marking the runtime daemon as gone (clean shutdown)."""
    payload = _payload({"alive": False, "source": "runtime_daemon_shutdown"})
    try:
        HEARTBEAT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    except Exception:
        pass
    try:
        from seal_heartbeat import beat_sync  # type: ignore
        beat_sync(AGENT_ID, metadata={"source": "runtime_daemon_shutdown", "alive": False}, content=f"{AGENT_ID} runtime_daemon shutdown")
        return True
    except Exception:
        return False
