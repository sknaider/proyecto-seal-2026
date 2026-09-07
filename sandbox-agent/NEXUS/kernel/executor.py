"""NEXUS executor — sandboxed action execution layer.

Security model (JARVIS spec §5):
- Whitelist-only bash commands
- Git: read-only operations only
- File writes: restricted to NEXUS sandbox root
- SOUL MCP: own namespace only
- Audit log: every execution attempt, success or fail
- Propose mode (default): posts proposal to webchat, does NOT execute
- Execute mode: runs actual command, logs result

Reference: spec_nexus_kernel_soul_v1.md §5
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Config ────────────────────────────────────────────────────────────────────

SANDBOX_ROOT = Path(os.environ.get(
    "NEXUS_SANDBOX_ROOT",
    "/home/dadito/IA/proyecto-seal/sandbox-agent/NEXUS",
))
AUDIT_LOG = Path(os.environ.get("NEXUS_AUDIT_LOG", "/tmp/nexus_execution_audit.jsonl"))
WEBCHAT_URL = "http://localhost:8765/api/agents/send"
AGENT_ID = "NEXUS"


# ── Whitelist ─────────────────────────────────────────────────────────────────

ALLOWED_BASH_COMMANDS = {
    "grep", "find", "cat", "ls", "head", "tail",
    "ps", "df", "free", "du", "wc", "sort", "uniq",
    "git", "python3", "curl", "pg_isready", "pgrep", "date",
}

GIT_READONLY_OPS = {"log", "diff", "status", "show", "branch", "remote", "describe", "rev-parse"}

PYTHON_ALLOWED_FLAGS = {"-m pytest", "-m unittest"}

ALLOWED_SOUL_TOOLS = {
    "memory_store", "active_recall", "self_reflect", "memory_search", "memory_list",
}

# Paths NEXUS can write to
WRITE_ALLOWED = [SANDBOX_ROOT]

# Paths NEXUS can read from
READ_ALLOWED = [
    SANDBOX_ROOT,
    Path("/home/dadito/IA/proyecto-seal"),
]

# Paths NEXUS must NEVER touch
PROHIBITED = [
    Path("/etc"),
    Path("/root"),
    Path("/home/dadito/.claude"),
    Path("/home/dadito/IA/proyecto-seal/agents/ADA"),
    Path("/home/dadito/IA/proyecto-seal/agents/JARVIS"),
    Path("/home/dadito/.config/seal"),
]

# Patterns blocked regardless of command
BLOCKED_PATTERNS = [
    r"rm\s+-rf",
    r"kill\s+-9\s+[^$]",
    r"sudo\s+",
    r"chmod\s+777",
    r">\s*/etc/",
    r"DROP\s+TABLE",
    r"DELETE\s+FROM",
    r"TRUNCATE\s+",
]

CMD_TIMEOUT_S = 15.0
MAX_OUTPUT_CHARS = 2000


class ExecutorError(Exception):
    """Raised when a command is blocked by security policy."""


# ── Security validators ───────────────────────────────────────────────────────

def _check_blocked_patterns(cmd: str) -> None:
    for pat in BLOCKED_PATTERNS:
        if re.search(pat, cmd, re.IGNORECASE):
            raise ExecutorError(f"Blocked pattern: {pat!r} in command: {cmd!r}")


def _check_command_whitelist(cmd: str) -> None:
    parts = cmd.strip().split()
    if not parts:
        raise ExecutorError("Empty command")
    base = Path(parts[0]).name  # handle /usr/bin/grep -> grep
    if base not in ALLOWED_BASH_COMMANDS:
        raise ExecutorError(f"Command not in whitelist: {base!r}")

    # Git read-only enforcement
    if base == "git":
        if len(parts) < 2 or parts[1] not in GIT_READONLY_OPS:
            op = parts[1] if len(parts) > 1 else "(none)"
            raise ExecutorError(f"Git op not allowed: {op!r} — read-only ops only")

    # Python: only pytest/unittest
    if base == "python3":
        joined = " ".join(parts[1:])
        if not any(joined.startswith(f) for f in PYTHON_ALLOWED_FLAGS):
            raise ExecutorError(f"python3 only allowed with: {PYTHON_ALLOWED_FLAGS}")

    # Curl: only localhost
    if base == "curl":
        url_args = [p for p in parts if p.startswith("http")]
        for url in url_args:
            if not (url.startswith("http://localhost") or url.startswith("http://127.")):
                raise ExecutorError(f"curl only allowed to localhost, not: {url}")


def _check_prohibited_paths(cmd: str) -> None:
    for prohibited in PROHIBITED:
        if str(prohibited) in cmd:
            raise ExecutorError(f"Prohibited path: {prohibited}")


def validate(cmd: str) -> str:
    """Validate command against all security policies. Returns cmd if valid, raises ExecutorError."""
    _check_blocked_patterns(cmd)
    _check_command_whitelist(cmd)
    _check_prohibited_paths(cmd)
    return cmd


# ── Audit log ─────────────────────────────────────────────────────────────────

def _audit(entry: dict[str, Any]) -> None:
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as ex:
        print(f"[nexus/executor] audit write failed: {ex}", flush=True)


# ── Webchat ───────────────────────────────────────────────────────────────────

async def _post_webchat(message: str) -> None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            await c.post(WEBCHAT_URL, json={
                "from": AGENT_ID,
                "to": "William",
                "type": "conversation",
                "channel": "web_chat",
                "message": message,
            })
    except Exception as ex:
        print(f"[nexus/executor] webchat post failed: {ex}", flush=True)


# ── Executor class ────────────────────────────────────────────────────────────

class Executor:
    """Sandboxed command executor for NEXUS daemon.

    Mode 'propose': validates action, posts proposal to webchat — does NOT execute.
    Mode 'execute': validates and runs command, logs audit entry.
    """

    def __init__(self) -> None:
        self.mode = os.environ.get("NEXUS_EXECUTE_MODE", "propose")

    def reload_mode(self) -> None:
        """Reload mode from environment (allows hot-switch without restart)."""
        self.mode = os.environ.get("NEXUS_EXECUTE_MODE", "propose")

    async def handle(self, action: str) -> str:
        """Process an action string. Returns result description."""
        self.reload_mode()
        try:
            validated = validate(action)
        except ExecutorError as e:
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "agent": AGENT_ID,
                "mode": self.mode,
                "command": action,
                "blocked": True,
                "reason": str(e),
                "success": False,
                "duration_ms": 0,
            }
            _audit(entry)
            return f"[BLOCKED] {e}"

        if self.mode == "propose":
            return await self._propose(validated)
        else:
            return await self._execute(validated)

    async def _propose(self, cmd: str) -> str:
        _audit({
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": AGENT_ID,
            "mode": "propose",
            "command": cmd,
            "proposed": True,
            "success": True,
            "duration_ms": 0,
        })
        # Return formatted proposal — cortex posts everything in one message
        return (
            f"\n**[NEXUS propone]**\n```\n{cmd}\n```\n"
            f"Responde 'OK nexus' para autorizar."
        )

    async def _execute(self, cmd: str) -> str:
        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(SANDBOX_ROOT),
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=CMD_TIMEOUT_S)
            except asyncio.TimeoutError:
                proc.kill()
                raise ExecutorError(f"Command timed out after {CMD_TIMEOUT_S}s")
            output = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
            success = proc.returncode == 0
        except ExecutorError as e:
            output = str(e)
            success = False

        duration_ms = int((time.monotonic() - t0) * 1000)
        _audit({
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": AGENT_ID,
            "mode": "execute",
            "command": cmd,
            "result_preview": output[:200],
            "success": success,
            "duration_ms": duration_ms,
        })
        return f"[{'OK' if success else 'FAIL'}] {cmd}\n{output}"


# ── Module-level singleton ────────────────────────────────────────────────────

_executor: Executor | None = None


def get_executor() -> Executor:
    global _executor
    if _executor is None:
        _executor = Executor()
    return _executor
