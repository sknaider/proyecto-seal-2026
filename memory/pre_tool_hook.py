#!/usr/bin/env python3
"""
SEAL PreToolUse Hook — runs BEFORE every Claude Code tool call.

Must complete in <200ms. Only stdlib imports allowed.

Tiers:
  1. MCP Identity Gate — validates SEAL_AGENT for seal-memory MCP calls
  2. Bash Safety — blocks clearly dangerous shell commands
  3. Passthrough — everything else exits immediately
"""

import json
import os
import sys
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")


def deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def allow_with_updated_input(updated_input: dict, context: str = "") -> dict:
    result = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        }
    }
    if context:
        result["hookSpecificOutput"]["additionalContext"] = context
    return result


# ---------------------------------------------------------------------------
# Tier 2: Dangerous bash patterns
# ---------------------------------------------------------------------------
BASH_BLOCKLIST = [
    # rm -rf / or rm -rf /* (but not rm -rf ./somedir)
    (r'\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?-[a-zA-Z]*r[a-zA-Z]*\s+/\s*$',
     "rm -rf / is blocked — catastrophic filesystem deletion"),
    (r'\brm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+)?-[a-zA-Z]*f[a-zA-Z]*\s+/\s*$',
     "rm -rf / is blocked — catastrophic filesystem deletion"),
    (r'\brm\s+-[a-zA-Z]*rf[a-zA-Z]*\s+/\*',
     "rm -rf /* is blocked — catastrophic filesystem deletion"),
    # git push --force to main/master
    (r'\bgit\s+push\s+.*--force.*\b(main|master)\b',
     "Force push to main/master is blocked"),
    (r'\bgit\s+push\s+.*-f\b.*\b(main|master)\b',
     "Force push to main/master is blocked"),
    # SQL destruction
    (r'\bDROP\s+(TABLE|DATABASE)\b',
     "DROP TABLE/DATABASE is blocked — use migrations instead"),
    # chmod 777
    (r'\bchmod\s+777\b',
     "chmod 777 is blocked — overly permissive permissions"),
    # mkfs on any device
    (r'\bmkfs\b',
     "mkfs is blocked — filesystem formatting requires manual confirmation"),
    # dd writing to block devices
    (r'\bdd\s+.*\bif=.*\bof=/dev/',
     "dd to /dev/ device is blocked — destructive disk write"),
    # fork bomb
    (r':\(\)\s*\{\s*:\|:\s*&\s*\}\s*;?\s*:',
     "Fork bomb detected and blocked"),
]

# Pre-compile for speed
_COMPILED_BLOCKLIST = [(re.compile(pat, re.IGNORECASE), msg) for pat, msg in BASH_BLOCKLIST]


def check_bash_safety(command: str) -> dict | None:
    """Return a deny dict if command matches a dangerous pattern, else None."""
    for pattern, message in _COMPILED_BLOCKLIST:
        if pattern.search(command):
            return deny(message)
    return None


def main():
    raw = sys.stdin.read()
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        # Can't parse — passthrough
        print(json.dumps({}))
        return

    tool_name = event.get("tool_name", "")
    tool_input = event.get("tool_input", {})

    # -----------------------------------------------------------------------
    # Tier 1: MCP Identity Gate
    # -----------------------------------------------------------------------
    if tool_name.startswith("mcp__seal-memory__"):
        agent = os.environ.get("SEAL_AGENT", "")
        if agent not in ("ADA", "JARVIS"):
            print(json.dumps(deny(
                f"Unknown agent '{agent}' — SEAL_AGENT must be ADA or JARVIS"
            )))
            return

        # Auto-enrich memory_store
        if tool_name == "mcp__seal-memory__memory_store":
            updated = dict(tool_input)
            updated["agent"] = agent
            updated["timestamp"] = datetime.now(LIMA_TZ).isoformat()
            print(json.dumps(allow_with_updated_input(
                updated, f"Auto-enriched with agent={agent}"
            )))
            return

        # Auto-enrich memory_invalidate if missing agent
        if tool_name == "mcp__seal-memory__memory_invalidate":
            if "agent" not in tool_input or not tool_input["agent"]:
                updated = dict(tool_input)
                updated["agent"] = agent
                print(json.dumps(allow_with_updated_input(
                    updated, f"Injected agent={agent} into invalidate call"
                )))
                return

        # All other MCP calls — passthrough (agent is valid)
        print(json.dumps({}))
        return

    # -----------------------------------------------------------------------
    # Tier 2: Bash Safety
    # -----------------------------------------------------------------------
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        result = check_bash_safety(command)
        if result:
            print(json.dumps(result))
            return
        print(json.dumps({}))
        return

    # -----------------------------------------------------------------------
    # Tier 3: Everything else — passthrough
    # -----------------------------------------------------------------------
    print(json.dumps({}))


if __name__ == "__main__":
    main()
