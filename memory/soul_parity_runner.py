#!/usr/bin/env python3
"""Run F3 learning parity: real Claude Code hooks versus the local SOUL runtime."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
import uuid

from soul_event_interface import LEARNING_EVENTS, HookRegistration
from soul_parity_test import EvidenceKey, FireReceipt, arun_parity
from soul_runtime_adapter import get_adapter
from soul_runtime_orchestrator import (
    ROOT,
    RuntimeHookStore,
    default_dsn_path,
    read_private_dsn,
)


HOOK = (ROOT / "memory" / "soul_parity_effect_hook.py").resolve()
HOOK_LAUNCHER = (ROOT / "memory" / "soul_parity_hook_launcher.py").resolve()
CLAUDE_EXEC = (ROOT / "memory" / "soul_parity_claude_exec.py").resolve()
LOCAL_PROCESS = (ROOT / "memory" / "soul_parity_local_process.py").resolve()
PYTHON = Path("/home/dadito/IA/seal-spark/.venv/bin/python3")
F3_EVENT_ORDER = ("on_compact", "on_boot", "on_prompt", "on_turn_end")
CLAUDE_ENV_ALLOWLIST = {
    "HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "COLORTERM", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR",
    "CLAUDE_CONFIG_DIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
}


def sandbox_dsn(agent: str) -> str:
    raw = read_private_dsn(default_dsn_path(agent))
    parsed = urlsplit(raw)
    return urlunsplit((parsed.scheme, parsed.netloc, "/soul_v3_sandbox", "", ""))


class SandboxEvidence:
    def __init__(self, agent: str, dsn: str) -> None:
        self.agent = agent.upper()
        self.dsn = dsn
        self.conn = None

    async def __aenter__(self):
        import asyncpg
        self.conn = await asyncpg.connect(self.dsn)
        row = await self.conn.fetchrow(
            """SELECT current_database() db, session_user::text session_user,
                      current_user::text current_user,
                      soul_v3.mcp_session_agent() resolved_agent,
                      (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) superuser"""
        )
        expected = f"mcp_runtime_{self.agent.lower()}"
        if dict(row) != {
            "db": "soul_v3_sandbox", "session_user": expected,
            "current_user": expected, "resolved_agent": self.agent, "superuser": False,
        }:
            raise RuntimeError(f"sandbox verifier identity mismatch: {dict(row)}")
        return self

    async def __aexit__(self, *_exc):
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    async def verify(self, key: EvidenceKey) -> bool:
        assert self.conn is not None
        value = await self.conn.fetchval(
            """SELECT EXISTS(
                 SELECT 1 FROM soul_f3.parity_evidence_v2
                 WHERE suite_run_id=$1 AND runtime_id=$2 AND soul_event=$3
                   AND script_path=$4 AND agent=$5 AND nonce=$6 AND token_sha256=$7
                   AND native_event=$8 AND script_sha256=$9 AND runtime_pid=$10
               )""",
            key.suite_run_id, key.runtime_id, key.soul_event,
            key.script_path, key.agent, key.nonce,
            hashlib.sha256(key.token.encode("utf-8")).hexdigest(),
            key.native_event, key.script_sha256, key.runtime_pid,
        )
        return bool(value)

    async def read(self, run_id: str, runtime: str, event: str, agent: str):
        assert self.conn is not None
        rows = await self.conn.fetch(
            """SELECT DISTINCT script_path FROM soul_f3.parity_evidence_v2
               WHERE suite_run_id=$1 AND runtime_id=$2 AND soul_event=$3 AND agent=$4""",
            run_id, runtime, event, agent,
        )
        return {row["script_path"] for row in rows}


def _native_event(runtime: str, soul_event: str) -> str:
    matches = [native for native, semantic in get_adapter(runtime).event_map.items() if semantic == soul_event]
    if len(matches) != 1:
        raise RuntimeError(f"{runtime} has no unique native event for {soul_event}")
    return matches[0]


class LocalFire:
    def __init__(self, agent: str, dsn: str, timeout_seconds: float = 120.0) -> None:
        self.agent = agent.upper()
        self.dsn = dsn
        self.timeout_seconds = timeout_seconds
        self.session_id = f"f3-local-{uuid.uuid4().hex}"
        self.process_ids: list[int] = []

    async def __call__(self, key: EvidenceKey, payload: dict) -> FireReceipt:
        native_event = _native_event("local_llama", key.soul_event)
        env = {name: os.environ[name] for name in CLAUDE_ENV_ALLOWLIST if os.environ.get(name)}
        env.update({
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PYTHONPATH": os.pathsep.join((str(ROOT / "memory"), str(ROOT))),
            "SEAL_DB_DSN": self.dsn,
            "SEAL_DB_URL": self.dsn,
            "SEAL_PG_DSN": self.dsn,
        })
        request = json.dumps({
            "agent": self.agent,
            "native_event": native_event,
            "session_id": self.session_id,
            "payload": payload,
        })
        proc = await asyncio.create_subprocess_exec(
            str(PYTHON), str(LOCAL_PROCESS),
            cwd=str(ROOT), env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self.process_ids.append(proc.pid)
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(request.encode("utf-8")), timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.communicate()
            raise
        if proc.returncode != 0:
            raise RuntimeError(f"local runtime failed rc={proc.returncode}: {stderr[-1000:]!r}")
        result = json.loads(stdout)
        executed = {str(Path(row["script_path"]).resolve()) for row in result["effects"]}
        if (
            result.get("pid") != proc.pid
            or not result.get("llm_effect")
            or str(HOOK) not in executed
            or not result["all_ran"]
        ):
            raise RuntimeError(f"local runtime did not prove llama.cpp + hook effect: {result}")
        return FireReceipt(
            key.suite_run_id, "local_llama", key.soul_event,
            "soul_parity_local_process", native_event, proc.pid,
        )


class ClaudeFire:
    def __init__(self, agent: str, dsn: str, timeout_seconds: float = 120.0) -> None:
        self.agent = agent.upper()
        self.dsn = dsn
        self.timeout_seconds = timeout_seconds
        self.process_ids: list[int] = []

    def _settings(self, native_event: str) -> str:
        command = f"{PYTHON} {HOOK_LAUNCHER} {HOOK}"
        return json.dumps({
            "hooks": {
                native_event: [{"hooks": [{"type": "command", "command": command, "timeout": 20}]}]
            }
        }, separators=(",", ":"))

    def _environment(self, key: EvidenceKey) -> dict[str, str]:
        allowed = {
            name: os.environ[name] for name in CLAUDE_ENV_ALLOWLIST
            if os.environ.get(name)
        }
        allowed.update({
            # Deliberately do not set SEAL_AGENT: this is a finite test probe,
            # not the long-lived agent process governed by the lifecycle daemon.
            "SOUL_PARITY_AGENT": self.agent,
            "SEAL_DB_DSN": self.dsn,
            "SEAL_DB_URL": self.dsn,
            "SEAL_PG_DSN": self.dsn,
            "SOUL_RUNTIME": "claude_code",
            "SOUL_PARITY_SUITE_RUN_ID": key.suite_run_id,
            "SOUL_PARITY_RUNTIME_ID": key.runtime_id,
            "SOUL_PARITY_SOUL_EVENT": key.soul_event,
            "SOUL_PARITY_NONCE": key.nonce,
            "SOUL_PARITY_TOKEN": key.token,
        })
        return allowed

    def _run(self, key: EvidenceKey) -> tuple[int, str, str]:
        native_event = _native_event("claude_code", key.soul_event)
        compact_probe = key.soul_event == "on_compact"
        prompt = "Reply exactly PARITY_OK."
        argv = [
            str(PYTHON), str(CLAUDE_EXEC), "claude", "-p",
            "--setting-sources", "",
            "--settings", self._settings(native_event),
            "--tools", "",
            "--permission-mode", "dontAsk",
            "--model", "haiku",
            "--max-budget-usd", "0.10",
            "--no-session-persistence",
            "--output-format", "json",
        ]
        if compact_probe:
            # A deliberately over-limit stdin causes Claude Code to emit its real
            # PreCompact lifecycle event before failing the inference request.  The
            # parity artifact, not the CLI return code, is the acceptance signal.
            argv.extend(("--autocompact", "100k"))
            # Keep an explicit print prompt as well as the oversized piped
            # context. Claude Code rejects a stdin-only `-p` invocation before
            # reading the pipe on current CLI builds.
            argv.append(prompt)
            stdin = ("cobalt " * 115000) + "Reply exactly PARITY_OK."
        else:
            argv.append(prompt)
            stdin = None
        proc = subprocess.Popen(
            argv, cwd=ROOT, env=self._environment(key), text=True,
            stdin=subprocess.PIPE if stdin is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.process_ids.append(proc.pid)
        try:
            stdout, stderr = proc.communicate(input=stdin, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            raise TimeoutError(f"Claude Code event {native_event} timed out")
        return proc.returncode, stdout, stderr

    async def __call__(self, key: EvidenceKey, _payload: dict) -> FireReceipt:
        returncode, stdout, stderr = await asyncio.to_thread(self._run, key)
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Claude Code returned non-JSON output for {key.soul_event} "
                f"rc={returncode}; stdout={stdout[-1000:]!r}; stderr={stderr[-1000:]!r}"
            ) from exc
        compact_lifecycle_probe = (
            key.soul_event == "on_compact"
            and returncode != 0
            and parsed.get("terminal_reason") == "blocking_limit"
            and parsed.get("result") == "Prompt is too long"
        )
        if returncode != 0 and not compact_lifecycle_probe:
            raise RuntimeError(f"Claude Code event failed rc={returncode}: {stderr[-1000:]}")
        if parsed.get("is_error") and not compact_lifecycle_probe:
            raise RuntimeError(f"Claude Code reported error: {parsed}")
        return FireReceipt(
            key.suite_run_id, "claude_code", key.soul_event,
            f"claude-code:{_native_event('claude_code', key.soul_event)}",
            _native_event("claude_code", key.soul_event), self.process_ids[-1],
        )


async def run_live(agent: str) -> dict:
    normalized = agent.upper()
    dsn = sandbox_dsn(normalized)
    async with RuntimeHookStore(normalized, restricted_dsn=dsn) as registry_store:
        registry: list[HookRegistration] = []
        for event in F3_EVENT_ORDER:
            for hook in await registry_store.load(event):
                path = Path(hook.script_path)
                canonical = path if path.is_absolute() else ROOT / path
                registry.append(replace(hook, script_path=str(canonical.resolve())))
    claude = ClaudeFire(normalized, dsn)
    local = LocalFire(normalized, dsn)
    async with SandboxEvidence(normalized, dsn) as evidence:
        report = await arun_parity(
            normalized, registry, "claude_code", "local_llama",
            claude, local, evidence.verify, evidence.verify,
            evidence.read, evidence.read,
            events=F3_EVENT_ORDER, timeout_seconds=150.0,
        )
    report["sandbox_db"] = "soul_v3_sandbox"
    report["verifier_role"] = f"mcp_runtime_{normalized.lower()}"
    report["claude_processes"] = claude.process_ids
    report["local_processes"] = local.process_ids
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="SOUL F3 live parity runner")
    parser.add_argument("--agent", default="ADA", choices=["ADA", "ALICE", "DUM", "JARVIS", "NEXUS"])
    args = parser.parse_args()
    report = asyncio.run(run_live(args.agent))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("parity") and report.get("learning_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
