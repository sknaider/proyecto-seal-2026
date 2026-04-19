#!/usr/bin/env python3
"""SEAL Denial Tracker — Tracks tool permission denials per session.

Hook for PermissionDenied events:
  - Increments consecutive + total denial counters
  - Alerts JARVIS+team via web_chat when thresholds exceeded
  - Threshold: 3 consecutive OR 20 total denials

Hook for PostToolUse (successful):
  - Resets consecutive counter (tool succeeded = streak broken)

Counter file: /tmp/.seal_denial_{session_id}.json
"""

import json
import os
import sys
import time

CHAT_URL = "http://localhost:8765/api/agents/send"
COUNTER_DIR = "/tmp"
CONSECUTIVE_THRESHOLD = 3
TOTAL_THRESHOLD = 20

# Suppress flood: don't alert more than once per 5 minutes
ALERT_COOLDOWN_SECS = 300


def get_counter_file(session_id: str) -> str:
    safe = session_id.replace("/", "_")[:40]
    return f"{COUNTER_DIR}/.seal_denial_{safe}.json"


def load_counter(session_id: str) -> dict:
    path = get_counter_file(session_id)
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {"consecutive": 0, "total": 0, "last_alert_ts": 0}


def save_counter(session_id: str, data: dict) -> None:
    path = get_counter_file(session_id)
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def post_alert(agent: str, consecutive: int, total: int, tool_name: str, reason: str) -> None:
    import urllib.request
    msg = (
        f"[DENIAL TRACKER] {agent} ha acumulado {consecutive} denials consecutivos "
        f"({total} total) — último tool: {tool_name}. Razón: {reason[:100]}. "
        f"William o JARVIS deberían revisar los permisos."
    )
    payload = json.dumps({
        "from": agent or "SEAL",
        "to": "JARVIS",
        "type": "system_alert",
        "channel": "web_chat",
        "message": msg,
    }).encode()
    try:
        req = urllib.request.Request(
            CHAT_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except Exception:
        print(json.dumps({}))
        return

    event = data.get("hook_event_name", "")
    session_id = data.get("session_id", "unknown")
    agent = data.get("agent_type", os.environ.get("SEAL_AGENT", "SEAL"))

    counter = load_counter(session_id)

    if event == "PermissionDenied":
        tool_name = data.get("tool_name", "?")
        reason = data.get("reason", "")

        counter["consecutive"] = counter.get("consecutive", 0) + 1
        counter["total"] = counter.get("total", 0) + 1

        consecutive = counter["consecutive"]
        total = counter["total"]

        hit_threshold = (
            consecutive >= CONSECUTIVE_THRESHOLD or total >= TOTAL_THRESHOLD
        )

        if hit_threshold:
            last_alert = counter.get("last_alert_ts", 0)
            now = time.monotonic()
            if now - last_alert > ALERT_COOLDOWN_SECS:
                counter["last_alert_ts"] = now
                post_alert(agent, consecutive, total, tool_name, reason)

        save_counter(session_id, counter)

        # Return additionalContext warning (visible to Claude after the denial)
        warning = (
            f"[Denial Tracker: {consecutive} consecutive, {total} total]"
            if hit_threshold
            else None
        )
        if warning:
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionDenied",
                    "retry": False,
                },
                "output": warning,
            }
            print(json.dumps(output))
        else:
            print(json.dumps({}))

    elif event == "PostToolUse":
        # Successful tool use — reset consecutive counter
        if counter.get("consecutive", 0) > 0:
            counter["consecutive"] = 0
            save_counter(session_id, counter)
        print(json.dumps({}))

    else:
        print(json.dumps({}))


if __name__ == "__main__":
    main()
