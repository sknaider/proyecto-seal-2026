"""ADA executor — supervised production action layer.

Differs from NEXUS executor (sandbox-only) in that ADA is the engineer
who actually builds in the project. Whitelist is ampliada to include:
  - pip / pip3 / pytest / nvidia-smi / watch
  - cp / mv / mkdir / chmod (no 777)
  - tar / rsync / ssh / scp
  - git WRITE ops (add / commit / push / checkout / stash)
  - Python: training/eval scripts in addition to pytest

Every execution attempt is audited at /tmp/ada_execution_audit.jsonl
(per the dogfooding rule applied to NEXUS today 14:20).

Mode 'propose' (default): post action plan to webchat, no execution.
Mode 'execute' (NEXUS_EXECUTE_MODE-style env): runs the command.
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

import httpx

AGENT_ID = "ADA"
PROJECT_ROOT = Path("/home/dadito/IA/proyecto-seal")
AUDIT_LOG = Path("/tmp/ada_execution_audit.jsonl")
WEBCHAT_URL = os.environ.get("WEBCHAT_URL", "http://localhost:8765/api/agents/send")

CMD_TIMEOUT_S = int(os.environ.get("ADA_CMD_TIMEOUT_S", "30"))
MAX_OUTPUT_CHARS = 4000

ALLOWED_BASH_COMMANDS = {
    # heredados de NEXUS — read/inspect
    "grep", "find", "cat", "ls", "head", "tail",
    "ps", "df", "free", "du", "wc", "sort", "uniq",
    "git", "python3", "curl", "pg_isready", "pgrep", "date",
    # propios de ADA — engineering tools
    "pip", "pip3",
    "pytest",
    "nvidia-smi",
    "watch",
    "cp", "mv",
    "mkdir",
    "chmod",
    "tar",
    "rsync",
    "ssh",
    "scp",
}

GIT_ALLOWED_OPS = {
    # read
    "log", "diff", "status", "show", "branch", "remote", "describe", "rev-parse",
    # write (ADA es ingeniera)
    "add", "commit", "push", "checkout", "stash",
}

PYTHON_ALLOWED_PREFIXES = (
    "-m pytest",
    "-m unittest",
    "train_",
    "eval_",
    "memory/",
    "scripts/",
)

WRITE_ALLOWED = [PROJECT_ROOT]
PROHIBITED_PATHS = [
    Path("/etc"),
    Path("/root"),
    Path("/home/dadito/.claude"),
    Path("/home/dadito/.config/seal"),
]

# Patterns ADA must NEVER execute (regardless of whitelist match)
BLOCKED_PATTERNS = [
    re.compile(r"\brm\s+-rf\b"),
    re.compile(r"\bchmod\s+777\b"),
    re.compile(r"/etc/shadow"),
    re.compile(r"DROP\s+TABLE", re.IGNORECASE),
    re.compile(r"DELETE\s+FROM\s+\w+\s*;?\s*$", re.IGNORECASE),
    re.compile(r":\(\)\s*\{.*:.*}\s*;\s*:"),  # fork bomb
]


class ExecutorError(Exception):
    pass


def _check_blocked_patterns(cmd: str) -> None:
    for pat in BLOCKED_PATTERNS:
        if pat.search(cmd):
            raise ExecutorError(f"Blocked pattern: {pat.pattern!r} in command: {cmd!r}")


def _check_command_whitelist(cmd: str) -> None:
    parts = cmd.strip().split()
    if not parts:
        raise ExecutorError("empty command")
    base = parts[0]
    if base not in ALLOWED_BASH_COMMANDS:
        raise ExecutorError(f"command {base!r} not in whitelist")

    if base == "git":
        if len(parts) < 2:
            raise ExecutorError("git requires a subcommand")
        op = parts[1]
        if op not in GIT_ALLOWED_OPS:
            raise ExecutorError(f"git op {op!r} not allowed")

    if base == "python3":
        rest = " ".join(parts[1:])
        if not any(rest.startswith(p) for p in PYTHON_ALLOWED_PREFIXES):
            raise ExecutorError(f"python3 only allowed with: {PYTHON_ALLOWED_PREFIXES}")

    if base == "chmod":
        if "777" in cmd:
            raise ExecutorError("chmod 777 prohibited")


def _check_prohibited_paths(cmd: str) -> None:
    for path in PROHIBITED_PATHS:
        if str(path) in cmd:
            raise ExecutorError(f"path {str(path)!r} prohibited")


def validate(cmd: str) -> str:
    """Validate command. Returns the (unchanged) command if OK, raises ExecutorError otherwise."""
    if not cmd or not cmd.strip():
        raise ExecutorError("empty command")
    _check_blocked_patterns(cmd)
    _check_command_whitelist(cmd)
    _check_prohibited_paths(cmd)
    return cmd


def _audit(entry: dict[str, Any]) -> None:
    try:
        with AUDIT_LOG.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as ex:
        print(f"[ada/executor] audit write failed: {ex}", flush=True)


async def _post_webchat(message: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            await c.post(
                WEBCHAT_URL,
                json={
                    "from": AGENT_ID,
                    "to": "William",
                    "type": "proposal",
                    "channel": "web_chat",
                    "message": message,
                },
            )
    except Exception as ex:
        print(f"[ada/executor] webchat post failed: {ex}", flush=True)


class Executor:
    """Sandboxed (production-supervised) command executor for ADA.

    Mode 'propose' (default): posts proposal to webchat, does NOT execute.
    Mode 'execute' (env ADA_EXECUTE_MODE=execute): runs the command.
    """

    def __init__(self) -> None:
        self.mode = os.environ.get("ADA_EXECUTE_MODE", "propose")

    def reload_mode(self) -> None:
        self.mode = os.environ.get("ADA_EXECUTE_MODE", "propose")

    async def handle(self, action: str) -> str:
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
        return (
            f"\n**[ADA propone]**\n```\n{cmd}\n```\n"
            f"Responde 'OK ada' para autorizar."
        )

    async def _execute(self, cmd: str) -> str:
        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(PROJECT_ROOT),
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
            "success": success,
            "duration_ms": duration_ms,
            "output_first_300": output[:300],
        })
        if success:
            return f"[OK] {cmd}\n{output}" if output.strip() else f"[OK] {cmd}"
        return f"[FAIL] {cmd}\n{output}"


_executor_singleton: Executor | None = None


def get_executor() -> Executor:
    global _executor_singleton
    if _executor_singleton is None:
        _executor_singleton = Executor()
    return _executor_singleton
