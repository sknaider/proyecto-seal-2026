#!/usr/bin/env python3
"""Claude Code Stop Hook — runs when Claude finishes responding.

Performs cleanup: delta capture, goodbye message, heartbeat update.
MUST check stop_hook_active to prevent infinite loops.
"""

import json
import os
import sys
import subprocess
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")


def main():
    # 1. Read stdin JSON, check stop_hook_active
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        data = {}

    if data.get("stop_hook_active"):
        print("{}")
        sys.exit(0)

    # 2. Detect agent
    agent = os.environ.get("SEAL_AGENT", "UNKNOWN")

    # 3. Fire-and-forget delta capture
    try:
        subprocess.Popen(
            [
                "/home/dadito/IA/seal-spark/.venv/bin/python3",
                "/home/dadito/IA/proyecto-seal/memory/session_delta_capture.py",
                "--compute-delta",
                "--agent", agent,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    now = datetime.now(LIMA_TZ).isoformat()

    # 4. Write goodbye message
    msg_file = None
    if agent == "JARVIS":
        msg_file = "/home/dadito/IA/proyecto-seal/messages/jarvis_messages.jsonl"
    elif agent == "ADA":
        msg_file = "/home/dadito/IA/proyecto-seal/messages/ada_messages.jsonl"

    if msg_file:
        goodbye = json.dumps({
            "from": agent,
            "type": "session_close",
            "message": f"{agent} cerrando sesión",
            "timestamp": now,
        })
        try:
            with open(msg_file, "a") as f:
                f.write(goodbye + "\n")
        except Exception:
            pass

    # 5. Update heartbeat — single writer via event_log (seal_heartbeat)
    if agent in ("JARVIS", "ADA", "ALICE", "DUM"):
        try:
            sys.path.insert(0, os.path.dirname(__file__))
            from seal_heartbeat import beat_sync
            beat_sync(agent, {"alive": False, "event": "session_stop"}, f"{agent} session closed")
        except Exception:
            pass

    # 6. Print empty JSON for observability
    print("{}")


if __name__ == "__main__":
    main()
