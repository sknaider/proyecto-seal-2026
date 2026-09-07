#!/usr/bin/env python3
"""PostToolUse hook — patches scheduled_tasks.json after any CronCreate.
Ensures all cron entries have permanent: true so they never expire in 7 days."""

import json
import sys
import os
from pathlib import Path

TASKS_FILE = Path.home() / ".claude" / "scheduled_tasks.json"

def patch_permanent():
    if not TASKS_FILE.exists():
        return

    try:
        with open(TASKS_FILE, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return

    modified = False
    if isinstance(data, list):
        for task in data:
            if isinstance(task, dict) and not task.get("permanent"):
                task["permanent"] = True
                modified = True
    elif isinstance(data, dict):
        for key, task in data.items():
            if isinstance(task, dict) and not task.get("permanent"):
                task["permanent"] = True
                modified = True

    if modified:
        with open(TASKS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[cron_permanent_hook] Patched {TASKS_FILE} — permanent:true added", file=sys.stderr)

if __name__ == "__main__":
    patch_permanent()
