#!/usr/bin/env python3
"""SEAL Task Completed Hook (Fase 4 — Quality Gate)

Claude Code TaskCompleted hook. Runs when a task is being marked complete.
Checks recently modified .py files for syntax errors.

Exit codes:
  0 — All OK, task may complete
  2 — Syntax errors found, BLOCKS task completion
"""

import json
import os
import sys
import subprocess

PYTHON = "/home/dadito/IA/seal-spark/.venv/bin/python3"
PROJECT_DIR = "/home/dadito/IA/proyecto-seal"


def get_recent_py_files():
    """Get .py files modified in the last 5 commits."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~5"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        files = []
        for line in result.stdout.strip().splitlines():
            if line.endswith(".py"):
                full_path = os.path.join(PROJECT_DIR, line)
                if os.path.isfile(full_path):
                    files.append(full_path)
        return files
    except Exception:
        return []


def check_syntax(filepath):
    """Run py_compile on a file. Returns error string or None."""
    try:
        result = subprocess.run(
            [PYTHON, "-m", "py_compile", filepath],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return result.stderr.strip() or f"Syntax error in {filepath}"
        return None
    except Exception as e:
        return f"Could not check {filepath}: {e}"


def main():
    # Read stdin JSON (task_id, task_subject, task_description)
    try:
        event = json.loads(sys.stdin.read())
    except Exception:
        event = {}

    task_id = event.get("task_id", "unknown")
    task_subject = event.get("task_subject", "")

    # Find recently modified .py files
    py_files = get_recent_py_files()
    if not py_files:
        # No Python files to check — pass through
        output = {
            "hookSpecificOutput": {
                "additionalContext": (
                    f"[SEAL Quality Gate] Task {task_id} — no recent .py changes to validate. "
                    "Remember to run tests before considering this truly done."
                )
            }
        }
        json.dump(output, sys.stdout)
        sys.exit(0)

    # Check each file for syntax errors
    errors = []
    for filepath in py_files:
        err = check_syntax(filepath)
        if err:
            errors.append(err)

    if errors:
        # BLOCK task completion
        msg = (
            f"[SEAL Quality Gate] BLOCKED task '{task_subject}' ({task_id})\n"
            f"Syntax errors in {len(errors)} file(s):\n"
        )
        for e in errors:
            msg += f"  - {e}\n"
        msg += "Fix syntax errors before marking task complete."
        print(msg, file=sys.stderr)
        sys.exit(2)

    # All OK
    output = {
        "hookSpecificOutput": {
            "additionalContext": (
                f"[SEAL Quality Gate] Task {task_id} — {len(py_files)} .py file(s) "
                "passed syntax check. Remember to run full test suite before shipping."
            )
        }
    }
    json.dump(output, sys.stdout)
    sys.exit(0)


if __name__ == "__main__":
    main()
