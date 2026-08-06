#!/usr/bin/env python3
"""F2: portable SOUL event orchestrator for local runtimes.

The orchestrator owns the trigger that Claude Code used to provide.  It maps a
native runtime event to a SOUL event, loads the visible hook registry through the
real per-agent PostgreSQL role, and executes the selected hook without inheriting
the host's privileged database variables.

This module is intentionally not a public chat writer.  A caller (local llama.cpp
runtime, CLI, or future service) owns the conversation transport.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from typing import Any, Iterable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid

from soul_event_interface import HookRegistration, hooks_for
from soul_runtime_adapter import get_adapter


ROOT = Path(__file__).resolve().parents[1]
MEMORY = ROOT / "memory"
KNOWN_AGENTS = {"ADA", "ALICE", "DUM", "JARVIS", "NEXUS"}
DB_ENV_NAMES = {
    "SEAL_DB_DSN", "SEAL_DB_URL", "SEAL_PG_DSN", "PGHOST", "PGPORT",
    "PGUSER", "PGPASSWORD", "PGDATABASE", "PGSERVICE", "PGSERVICEFILE",
}
SAFE_ENV_NAMES = {
    "HOME", "LANG", "LC_ALL", "TZ", "XDG_RUNTIME_DIR",
    "SOUL_CIRCUMSTANCE_ROUTING",
}
TRUSTED_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
MAX_HTTP_RESPONSE_BYTES = 4 * 1024 * 1024


class RuntimeIdentityError(RuntimeError):
    """The database connection is not the expected restricted runtime identity."""


class HookPathError(RuntimeError):
    """A registry path escaped the explicit local hook roots."""


def _reject_symlink_components(path: Path) -> None:
    """Reject symlinks in every existing component, not only the final name."""
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise HookPathError(f"symlink component rejected: {current}")


def _ensure_private_directory(path: Path) -> Path:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode):
                raise RuntimeIdentityError(f"runtime state symlink rejected: {current}")
            if not stat.S_ISDIR(info.st_mode):
                raise RuntimeIdentityError(f"runtime state component is not a directory: {current}")
        except FileNotFoundError:
            os.mkdir(current, 0o700)
    info = os.lstat(absolute)
    if info.st_uid != os.geteuid():
        raise RuntimeIdentityError("runtime state directory is not owned by this uid")
    os.chmod(absolute, 0o700)
    return absolute


def _normalized_agent(agent: str) -> str:
    value = (agent or "").strip().upper()
    if value not in KNOWN_AGENTS:
        raise ValueError(f"unsupported SOUL runtime agent: {agent!r}")
    return value


def default_dsn_path(agent: str) -> Path:
    return Path.home() / ".config" / "seal" / "mcp_agents" / f"{_normalized_agent(agent).lower()}.dsn"


def read_private_dsn(path: Path) -> str:
    """Read one DSN without following symlinks or accepting group/world access."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeIdentityError("runtime DSN is not a regular file")
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeIdentityError("runtime DSN must be owner-controlled mode 0600")
        raw = os.read(fd, 8193)
    finally:
        os.close(fd)
    if len(raw) > 8192:
        raise RuntimeIdentityError("runtime DSN is unexpectedly large")
    value = raw.decode("utf-8").strip()
    if not value.startswith(("postgresql://", "postgres://")):
        raise RuntimeIdentityError("runtime DSN is not PostgreSQL")
    return value


@dataclass(frozen=True)
class RuntimeDatabaseIdentity:
    session_user: str
    current_user: str
    resolved_agent: str
    superuser: bool


class RuntimeHookStore:
    """RLS-backed registry reader.  It refuses superuser or mismatched identities."""

    def __init__(self, agent: str, dsn_path: Path | None = None) -> None:
        self.agent = _normalized_agent(agent)
        self.dsn_path = dsn_path or default_dsn_path(self.agent)
        self.dsn = read_private_dsn(self.dsn_path)
        self.conn: Any | None = None
        self.identity: RuntimeDatabaseIdentity | None = None

    async def __aenter__(self) -> "RuntimeHookStore":
        self.conn = await connect_postgres(self.dsn)
        try:
            row = await self.conn.fetchrow(
                """SELECT session_user::text AS session_user,
                          current_user::text AS current_user,
                          COALESCE(soul_v3.mcp_session_agent(), '') AS resolved_agent,
                          (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) AS superuser"""
            )
            assert row is not None
            identity = RuntimeDatabaseIdentity(**dict(row))
            expected_role = f"mcp_runtime_{self.agent.lower()}"
            if (
                identity.session_user != expected_role
                or identity.current_user != expected_role
                or identity.resolved_agent != self.agent
                or identity.superuser
            ):
                raise RuntimeIdentityError(
                    "runtime registry requires exact non-superuser identity "
                    f"{expected_role}; observed session={identity.session_user} "
                    f"current={identity.current_user} agent={identity.resolved_agent!r} "
                    f"superuser={identity.superuser}"
                )
            self.identity = identity
            return self
        except Exception:
            await self.conn.close()
            self.conn = None
            raise

    async def __aexit__(self, *_exc: object) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    async def load(self, soul_event: str) -> list[HookRegistration]:
        if self.conn is None or self.identity is None:
            raise RuntimeIdentityError("runtime registry connection is not verified")
        rows = await self.conn.fetch(
            """SELECT soul_event, script_path, kind, agent, enabled, ordering, matcher
               FROM soul_v3.runtime_hooks
               WHERE enabled AND soul_event=$1
               ORDER BY ordering, script_path, matcher NULLS FIRST""",
            soul_event,
        )
        registrations = [HookRegistration(**dict(row)) for row in rows]
        if any(row.agent not in (None, self.agent) for row in registrations):
            raise RuntimeIdentityError("RLS returned a hook owned by another agent")
        return registrations


@dataclass(frozen=True)
class HookEffect:
    script_path: str
    ok: bool
    returncode: int
    duration_ms: int
    stdout: str = ""
    stderr: str = ""

    def context(self) -> str:
        """Extract Claude-compatible additionalContext without trusting free text."""
        try:
            parsed = json.loads(self.stdout or "{}")
            value = parsed.get("hookSpecificOutput", {}).get("additionalContext", "")
            return value if isinstance(value, str) else ""
        except (TypeError, json.JSONDecodeError):
            return ""


class HookExecutor:
    """Execute registry hooks with a restricted DB identity and bounded subprocess."""

    def __init__(
        self,
        agent: str,
        restricted_dsn: str,
        *,
        timeout_seconds: float = 15.0,
        allowed_roots: Iterable[Path] | None = None,
    ) -> None:
        self.agent = _normalized_agent(agent)
        self.restricted_dsn = restricted_dsn
        self.timeout_seconds = timeout_seconds
        roots = allowed_roots or (ROOT, Path.home() / ".claude" / "skills" / "dream")
        self.allowed_roots = tuple(root.resolve() for root in roots)

    def _resolve_script(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        if candidate.is_symlink():
            raise HookPathError(f"hook symlink rejected: {candidate}")
        resolved = candidate.resolve(strict=True)
        _reject_symlink_components(resolved.parent)
        if not resolved.is_file():
            raise HookPathError(f"hook is not a regular file: {resolved}")
        if not any(resolved.is_relative_to(root) for root in self.allowed_roots):
            raise HookPathError(f"hook escaped allowed roots: {resolved}")
        return resolved

    def _open_script(self, raw_path: str) -> tuple[Path, int]:
        resolved = self._resolve_script(raw_path)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(resolved, flags)
        try:
            info = os.fstat(fd)
            path_info = os.stat(resolved, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise HookPathError(f"hook is not a regular file: {resolved}")
            if (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino):
                raise HookPathError(f"hook changed during validation: {resolved}")
            return resolved, fd
        except Exception:
            os.close(fd)
            raise

    def _environment(self, session_id: str) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key in SAFE_ENV_NAMES and value}
        for key in DB_ENV_NAMES:
            env.pop(key, None)
        env.update(
            {
                "PATH": TRUSTED_PATH,
                "SEAL_AGENT": self.agent,
                "SEAL_SESSION_ID": session_id,
                # The shared secret helper checks the historical aliases before
                # SEAL_PG_DSN after loading credentials.env.  Pin every accepted
                # alias to the same restricted identity so a child hook cannot
                # silently fall back to the host's `seal` superuser credential.
                "SEAL_DB_DSN": self.restricted_dsn,
                "SEAL_DB_URL": self.restricted_dsn,
                "SEAL_PG_DSN": self.restricted_dsn,
                "SOUL_RUNTIME": "local_llama",
                "CLAUDE_PROJECT_DIR": str(ROOT),
                "PYTHONPATH": os.pathsep.join((str(MEMORY), str(ROOT))),
            }
        )
        return env

    def run(self, registration: HookRegistration, envelope: dict[str, Any]) -> HookEffect:
        started = time.monotonic()
        fd: int | None = None
        try:
            script, fd = self._open_script(registration.script_path)
            if script.suffix == ".py":
                launcher = (
                    "import os,sys; fd=int(sys.argv[1]); path=sys.argv[2]; "
                    "parts=[]; "
                    "\nwhile True:\n b=os.read(fd,65536)\n if not b: break\n parts.append(b)\n"
                    "code=compile(b''.join(parts),path,'exec'); "
                    "scope={'__name__':'__main__','__file__':path,'__package__':None}; exec(code,scope,scope)"
                )
                argv = [sys.executable, "-c", launcher, str(fd), str(script)]
            elif script.suffix == ".sh":
                argv = ["/bin/bash", f"/proc/self/fd/{fd}"]
            else:
                raise HookPathError(f"unsupported hook suffix: {script.suffix}")
            payload = dict(envelope.get("payload") or {})
            payload.update(
                {
                    "session_id": envelope["session_id"],
                    "cwd": str(ROOT),
                    "soul_event": envelope["soul_event"],
                    "runtime": envelope["runtime"],
                }
            )
            completed = subprocess.run(
                argv,
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                cwd=ROOT,
                env=self._environment(envelope["session_id"]),
                timeout=self.timeout_seconds,
                pass_fds=(fd,),
                check=False,
            )
            return HookEffect(
                str(script), completed.returncode == 0, completed.returncode,
                int((time.monotonic() - started) * 1000),
                completed.stdout[-262144:], completed.stderr[-16384:],
            )
        except subprocess.TimeoutExpired as exc:
            return HookEffect(
                registration.script_path, False, 124,
                int((time.monotonic() - started) * 1000),
                (exc.stdout or "")[-262144:] if isinstance(exc.stdout, str) else "",
                "hook timeout",
            )
        except (OSError, HookPathError) as exc:
            return HookEffect(
                registration.script_path, False, 126,
                int((time.monotonic() - started) * 1000), "", str(exc),
            )
        finally:
            if fd is not None:
                os.close(fd)


class SoulRuntimeOrchestrator:
    def __init__(
        self,
        agent: str,
        *,
        runtime: str = "local_llama",
        session_id: str | None = None,
        dsn_path: Path | None = None,
        llm_url: str = "http://127.0.0.1:8899/v1/chat/completions",
        model: str = "gemma4-dum",
        state_root: Path | None = None,
    ) -> None:
        self.agent = _normalized_agent(agent)
        self.adapter = get_adapter(runtime)
        self.session_id = session_id or f"soul-{self.agent.lower()}-{uuid.uuid4().hex}"
        self.dsn_path = dsn_path or default_dsn_path(self.agent)
        self.llm_url = validated_local_llm_url(llm_url)
        self.model = model
        self._booted = False
        self._boot_contexts: list[str] = []
        state_base = state_root or (Path.home() / ".local" / "state" / "seal" / "soul-runtime")
        state_dir = _ensure_private_directory(state_base / self.agent.lower())
        session_key = hashlib.sha256(self.session_id.encode("utf-8")).hexdigest()[:24]
        self.transcript_path = state_dir / f"{session_key}.jsonl"

    def _append_transcript(self, role: str, content: str) -> None:
        record = json.dumps({"role": role, "content": content}, ensure_ascii=False) + "\n"
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        _reject_symlink_components(self.transcript_path.parent)
        fd = os.open(self.transcript_path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise RuntimeIdentityError("runtime transcript must be an owner-only regular file")
            os.write(fd, record.encode("utf-8"))
        finally:
            os.close(fd)

    def _read_transcript_messages(self, limit: int = 32) -> list[dict[str, str]]:
        _reject_symlink_components(self.transcript_path.parent)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.transcript_path, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise RuntimeIdentityError("runtime transcript must be an owner-only regular file")
            chunks: list[bytes] = []
            total = 0
            while total <= 2 * 1024 * 1024:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > 2 * 1024 * 1024:
                raise RuntimeIdentityError("runtime transcript exceeds 2 MiB")
        finally:
            os.close(fd)
        rows: list[dict[str, str]] = []
        for line in b"".join(chunks).decode("utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("role") in {"user", "assistant"} and isinstance(row.get("content"), str):
                rows.append({"role": row["role"], "content": row["content"]})
        return rows[-limit:]

    async def emit(self, native_event: str, payload: dict[str, Any]) -> dict[str, Any]:
        envelope = self.adapter.to_envelope(
            native_event, self.agent, self.session_id,
            datetime.now(UTC).isoformat(), payload,
        )
        async with RuntimeHookStore(self.agent, self.dsn_path) as store:
            registry = await store.load(envelope.soul_event)
            selected = hooks_for(registry, envelope.soul_event, self.agent, envelope.payload)
            executor = HookExecutor(self.agent, store.dsn)
            effects = [await asyncio.to_thread(executor.run, hook, envelope.as_dict()) for hook in selected]
            return {
                "soul_event": envelope.soul_event,
                "native_event": native_event,
                "agent": self.agent,
                "runtime": self.adapter.runtime_name,
                "db_identity": asdict(store.identity) if store.identity else None,
                "selected": [hook.script_path for hook in selected],
                "effects": [asdict(effect) for effect in effects],
                "contexts": [context for effect in effects if (context := effect.context())],
                "all_ran": bool(effects) and all(effect.ok for effect in effects),
            }

    async def complete(self, prompt: str, *, max_tokens: int = 256) -> dict[str, Any]:
        boot = None
        if not self._booted:
            boot = await self.emit("session_start", {"matcher_value": "startup", "source": "startup"})
            self._boot_contexts = list(boot.get("contexts", []))
            self._booted = True
        self._append_transcript("user", prompt)
        before = await self.emit(
            "user_message",
            {"prompt": prompt, "message": prompt, "transcript_path": str(self.transcript_path)},
        )
        messages: list[dict[str, str]] = []
        contexts = [*self._boot_contexts, *before["contexts"]]
        if contexts:
            messages.append({"role": "system", "content": "\n\n".join(contexts)})
        messages.extend(self._read_transcript_messages())
        status, data = await asyncio.to_thread(
            http_json,
            self.llm_url,
            {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "max_tokens": max_tokens,
                "temperature": 0.2,
            },
            120.0,
        )
        if status != 200:
            raise RuntimeError(f"local LLM returned HTTP {status}")
        answer = data["choices"][0]["message"]["content"]
        self._append_transcript("assistant", answer)
        after = await self.emit(
            "turn_complete",
            {
                "prompt": prompt,
                "response": answer,
                "stop_reason": "end_turn",
                "transcript_path": str(self.transcript_path),
            },
        )
        return {"answer": answer, "boot": boot, "before": before, "after": after}

    async def probe(self) -> dict[str, Any]:
        async with RuntimeHookStore(self.agent, self.dsn_path) as store:
            rows: list[HookRegistration] = []
            for soul_event in sorted(self.adapter.emits()):
                rows.extend(await store.load(soul_event))
            health_status, _health_body = await asyncio.to_thread(
                http_json, self.llm_url.rsplit("/v1/", 1)[0] + "/health", None, 10.0,
            )
            return {
                "ok": health_status == 200,
                "agent": self.agent,
                "runtime": self.adapter.runtime_name,
                "db_identity": asdict(store.identity) if store.identity else None,
                "visible_hooks": len(rows),
                "emitted_events": sorted(self.adapter.emits()),
                "llm_health": health_status,
                "llm_url": self.llm_url,
                "model": self.model,
            }


async def connect_postgres(dsn: str) -> Any:
    """Lazy import keeps static/quality evaluation independent of production wheels."""
    import asyncpg

    return await asyncpg.connect(dsn)


def http_json(url: str, payload: dict[str, Any] | None, timeout: float) -> tuple[int, dict[str, Any]]:
    validated_local_llm_url(url, allow_health=True)
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    with urlopen(request, timeout=timeout) as response:
        raw_bytes = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
        if len(raw_bytes) > MAX_HTTP_RESPONSE_BYTES:
            raise RuntimeError("local LLM response exceeds 4 MiB")
        raw = raw_bytes.decode("utf-8")
        return response.status, json.loads(raw or "{}")


def validated_local_llm_url(url: str, *, allow_health: bool = False) -> str:
    """Keep recalled SOUL context on the loopback runtime boundary."""
    parsed = urlsplit(url)
    allowed_paths = {"/v1/chat/completions"}
    if allow_health:
        allowed_paths.add("/health")
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in allowed_paths
    ):
        raise ValueError("SOUL local runtime URL must be loopback HTTP with an approved path")
    return url


async def _main_async(args: argparse.Namespace) -> int:
    orchestrator = SoulRuntimeOrchestrator(
        args.agent, llm_url=args.llm_url, model=args.model,
    )
    if args.command == "probe":
        result = await orchestrator.probe()
    else:
        result = await orchestrator.complete(args.prompt, max_tokens=args.max_tokens)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SOUL F2 local runtime orchestrator")
    parser.add_argument("--agent", required=True, choices=sorted(KNOWN_AGENTS))
    parser.add_argument("--llm-url", default="http://127.0.0.1:8899/v1/chat/completions")
    parser.add_argument("--model", default="gemma4-dum")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("probe")
    turn = sub.add_parser("turn")
    turn.add_argument("prompt")
    turn.add_argument("--max-tokens", type=int, default=256)
    return asyncio.run(_main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
