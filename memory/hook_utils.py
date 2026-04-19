#!/usr/bin/env python3
"""SEAL Hook Utilities — Shared module for all Claude Code hooks.

Provides unified detect_agent() and common constants.
Every hook should import from here instead of duplicating detection logic.

Primary source: SEAL_AGENT env var (set by SessionStart via CLAUDE_ENV_FILE).
Fallback: /proc/ppid/cmdline inspection → cwd heuristic.
"""

import os


def detect_agent() -> str:
    """Detect which SEAL agent is running this hook.

    Priority:
    1. SEAL_AGENT env var (set by soul_boot_hook.sh via CLAUDE_ENV_FILE)
    2. Parent process cmdline inspection
    3. CWD heuristic (memory/ = JARVIS, else ADA)
    """
    # Primary: env var set by SessionStart hook
    agent = os.environ.get("SEAL_AGENT", "").strip().upper()
    if agent in ("ADA", "JARVIS"):
        return agent

    # Secondary: parent process cmdline
    try:
        ppid = os.getppid()
        with open(f"/proc/{ppid}/cmdline", "rb") as f:
            cmdline = f.read().decode("utf-8", errors="replace")
        if "JARVIS" in cmdline and "ADA" not in cmdline:
            return "JARVIS"
        if "ADA" in cmdline and "JARVIS" not in cmdline:
            return "ADA"
    except Exception:
        pass

    # Tertiary: cwd heuristic
    cwd = os.getcwd()
    if cwd.rstrip("/").endswith("/memory"):
        return "JARVIS"
    return "ADA"


# Common constants
DB_URL = os.environ.get(
    "SEAL_PG_DSN",
    "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory",
)
PYTHON = "/home/dadito/IA/seal-spark/.venv/bin/python3"
SEAL_DIR = "/home/dadito/IA/proyecto-seal"
MESSAGES_DIR = os.path.join(SEAL_DIR, "messages")
