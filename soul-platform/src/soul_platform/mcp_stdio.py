"""Local stdio MCP bridge for official Codex/Claude client configuration.

The bridge never changes the selected model.  It exposes the installed machine
soul as explicit tools over a child process started by the client itself.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from soul_framework import Soul
from soul_framework.config import SoulConfig

from soul_platform.proxy import ProxySettings


PROTOCOL_VERSION = "2025-06-18"
ALLOWED_CLIENTS = {"codex", "claude"}
ATTACH_TTL_SECONDS = 8 * 60 * 60
_GRANT_THREAD_LOCK = threading.Lock()


@dataclass(frozen=True)
class ProcessIdentity:
    executable: str
    executable_sha256: str
    owner: str
    session: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _owner_identity() -> str:
    if os.name != "nt":
        return f"uid:{os.getuid()}"
    script = (
        "$i=[Security.Principal.WindowsIdentity]::GetCurrent();"
        "[ordered]@{sid=[string]$i.User.Value}|ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=8, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise ValueError("cannot resolve Windows owner identity")
    payload = json.loads(completed.stdout)
    sid = str(payload.get("sid") or "")
    if not sid.startswith("S-"):
        raise ValueError("invalid Windows owner identity")
    return f"sid:{sid}"


def _current_session_identity() -> str:
    if os.name != "nt":
        return f"sid:{os.getsid(0)}"
    script = (
        "$p=[Diagnostics.Process]::GetCurrentProcess();"
        "[ordered]@{session=[int]$p.SessionId}|ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=8, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise ValueError("cannot resolve Windows logon session")
    return f"session:{int(json.loads(completed.stdout)['session'])}"


def _parent_identity() -> ProcessIdentity:
    parent_pid = os.getppid()
    if os.name == "nt":
        script = (
            "$ErrorActionPreference='Stop';"
            f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {parent_pid}';"
            "if(-not $p -or -not $p.ExecutablePath){throw 'parent missing'};"
            "$s=Invoke-CimMethod -InputObject $p -MethodName GetOwnerSid;"
            "[ordered]@{path=[string]$p.ExecutablePath;sid=[string]$s.Sid;session=[int]$p.SessionId}"
            "|ConvertTo-Json -Compress"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=8, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            raise ValueError("cannot inspect MCP parent process")
        payload = json.loads(completed.stdout)
        path = Path(str(payload.get("path") or "")).resolve()
        owner = f"sid:{payload.get('sid')}"
        session = f"session:{int(payload.get('session'))}"
    else:
        proc = Path(f"/proc/{parent_pid}")
        path = proc.joinpath("exe").resolve(strict=True)
        owner = f"uid:{proc.stat().st_uid}"
        session = f"sid:{os.getsid(parent_pid)}"
    if not path.is_file():
        raise ValueError("MCP parent executable is unavailable")
    return ProcessIdentity(str(path), _sha256(path), owner, session)


def _grant_file(settings: ProxySettings) -> Path:
    return settings.soul_db.parent / "client-grants.json"


def _current_server_executable() -> Path:
    """Resolve the real console launcher, including Windows' hidden .exe suffix."""

    raw = Path(sys.argv[0]).expanduser()
    candidates = [raw]
    if raw.suffix.casefold() != ".exe":
        candidates.append(raw.with_suffix(f"{raw.suffix}.exe"))
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    raise ValueError("SOUL MCP server executable is unavailable")


@contextmanager
def _grant_store_lock(path: Path):
    """Serialize grant read/validate/write across threads and processes."""

    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _GRANT_THREAD_LOCK:
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _migrate_v1_grants(
    path: Path, raw_bytes: bytes, settings: ProxySettings
) -> dict[str, Any]:
    """Replace legacy enabled-only grants during explicit enrollment only."""

    try:
        legacy = json.loads(raw_bytes)
    except (TypeError, ValueError) as exc:
        raise ValueError("client grant store differs from the installed machine soul") from exc
    clients = legacy.get("clients") if isinstance(legacy, dict) else None
    if (
        not isinstance(legacy, dict)
        or legacy.get("schema") != "soul.client-grants.v1"
        or legacy.get("machine_soul_id") != settings.machine_soul_id
        or not isinstance(clients, dict)
        or any(
            client_id not in ALLOWED_CLIENTS
            or not isinstance(entry, dict)
            or set(entry) != {"enabled"}
            or entry.get("enabled") is not True
            for client_id, entry in clients.items()
        )
    ):
        raise ValueError("client grant store differs from the installed machine soul")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    backup = path.with_name(f"{path.name}.v1.{digest[:16]}.bak")
    if backup.exists():
        if backup.is_symlink() or not backup.is_file() or backup.read_bytes() != raw_bytes:
            raise ValueError("client grant migration backup collision")
    else:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, raw_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        if os.name != "nt":
            os.chmod(backup, 0o600)
    return {
        "schema": "soul.client-grants.v2",
        "machine_soul_id": settings.machine_soul_id,
        "clients": {},
    }


def ensure_client_grants(
    settings: ProxySettings, *, allow_v1_migration: bool = False
) -> Path:
    path = _grant_file(settings)
    expected = {
        "schema": "soul.client-grants.v2",
        "machine_soul_id": settings.machine_soul_id,
        "clients": {},
    }
    with _grant_store_lock(path):
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError("client grant store must be a regular file")
            raw_bytes = path.read_bytes()
            raw = json.loads(raw_bytes)
            if raw.get("schema") == "soul.client-grants.v1" and allow_v1_migration:
                raw = _migrate_v1_grants(path, raw_bytes, settings)
                _atomic_json(path, raw)
            if (
                not isinstance(raw, dict)
                or raw.get("schema") != expected["schema"]
                or raw.get("machine_soul_id") != settings.machine_soul_id
                or not isinstance(raw.get("clients"), dict)
            ):
                raise ValueError("client grant store differs from the installed machine soul")
            return path
        _atomic_json(path, expected)
    return path


def _launch_digest(
    *, server_executable: str, config_path: Path, client_id: str
) -> str:
    material = json.dumps(
        {
            "server_executable": os.path.normcase(str(Path(server_executable).resolve())),
            "config": os.path.normcase(str(config_path.resolve())),
            "client_id": client_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(material).hexdigest()


def enroll_client(
    settings: ProxySettings,
    client_id: str,
    *,
    parent_executable: Path,
    server_executable: Path,
    config_path: Path,
) -> dict[str, Any]:
    if client_id not in ALLOWED_CLIENTS:
        raise ValueError("unsupported SOUL client")
    parent = parent_executable.expanduser().resolve(strict=True)
    server = server_executable.expanduser().resolve(strict=True)
    if not parent.is_file() or not server.is_file():
        raise ValueError("client or MCP executable is unavailable")
    entry = {
        "enabled": True,
        "owner": _owner_identity(),
        "session": _current_session_identity(),
        "parent_executable": str(parent),
        "parent_sha256": _sha256(parent),
        "server_executable": str(server),
        "server_sha256": _sha256(server),
        "launch_digest": _launch_digest(
            server_executable=str(server), config_path=config_path, client_id=client_id
        ),
        "scopes": ["boot", "memory.search", "memory.store"],
        "enrolled_unix_ms": int(time.time() * 1000),
    }
    path = _grant_file(settings)
    expected = {
        "schema": "soul.client-grants.v2",
        "machine_soul_id": settings.machine_soul_id,
        "clients": {},
    }
    with _grant_store_lock(path):
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError("client grant store must be a regular file")
            raw_bytes = path.read_bytes()
            raw = json.loads(raw_bytes)
            if isinstance(raw, dict) and raw.get("schema") == "soul.client-grants.v1":
                raw = _migrate_v1_grants(path, raw_bytes, settings)
        else:
            raw = dict(expected)
            raw["clients"] = {}
        if (
            not isinstance(raw, dict)
            or raw.get("schema") != expected["schema"]
            or raw.get("machine_soul_id") != settings.machine_soul_id
            or not isinstance(raw.get("clients"), dict)
        ):
            raise ValueError("client grant store differs from the installed machine soul")
        existing = raw["clients"].get(client_id)
        if isinstance(existing, dict):
            immutable_fields = (
                "enabled",
                "owner",
                "session",
                "parent_executable",
                "parent_sha256",
                "server_executable",
                "server_sha256",
                "launch_digest",
                "scopes",
            )
            if all(existing.get(field) == entry.get(field) for field in immutable_fields):
                return existing
            legacy_stable_fields = (
                "enabled",
                "owner",
                "parent_executable",
                "server_executable",
                "launch_digest",
                "scopes",
            )
            if "session" not in existing and all(
                existing.get(field) == entry.get(field)
                for field in legacy_stable_fields
            ):
                entry["migrated_from"] = "sessionless-v2"
                entry["dormant_hash_finalized"] = True
                entry["previous_parent_sha256"] = existing.get("parent_sha256")
                entry["previous_server_sha256"] = existing.get("server_sha256")
                raw["clients"][client_id] = entry
                _atomic_json(path, raw)
                return entry
            if (
                existing.get("migrated_from") == "sessionless-v2"
                and existing.get("dormant_hash_finalized") is not True
                and all(
                    existing.get(field) == entry.get(field)
                    for field in (*legacy_stable_fields, "session")
                )
            ):
                entry["migrated_from"] = "sessionless-v2"
                entry["dormant_hash_finalized"] = True
                entry["previous_parent_sha256"] = existing.get("parent_sha256")
                entry["previous_server_sha256"] = existing.get("server_sha256")
                raw["clients"][client_id] = entry
                _atomic_json(path, raw)
                return entry
            raise ValueError(
                "SOUL client already has an immutable binding; explicit owner-controlled "
                "reinstallation is required to rotate it"
            )
        raw["clients"][client_id] = entry
        _atomic_json(path, raw)
    return entry


def verify_client_grant(
    settings: ProxySettings,
    client_id: str,
    *,
    config_path: Path | None = None,
    server_executable: Path | None = None,
    process_identity: ProcessIdentity | None = None,
) -> dict[str, Any]:
    if client_id not in ALLOWED_CLIENTS:
        raise ValueError("unsupported SOUL client")
    path = ensure_client_grants(settings)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw["machine_soul_id"] != settings.machine_soul_id:
        raise ValueError("client grant audience mismatch")
    entry = raw["clients"].get(client_id)
    if not isinstance(entry, dict) or entry.get("enabled") is not True:
        raise ValueError("SOUL client is not granted")
    observed = process_identity or _parent_identity()
    expected_owner = str(entry.get("owner") or "")
    if observed.owner != expected_owner or _owner_identity() != expected_owner:
        raise ValueError("SOUL client OS session mismatch")
    expected_session = str(entry.get("session") or "")
    if observed.session != expected_session or _current_session_identity() != expected_session:
        raise ValueError("SOUL client logon session mismatch")
    if os.path.normcase(str(Path(observed.executable).resolve())) != os.path.normcase(
        str(Path(str(entry.get("parent_executable") or "")).resolve())
    ):
        raise ValueError("SOUL client parent executable mismatch")
    if observed.executable_sha256 != entry.get("parent_sha256"):
        raise ValueError("SOUL client parent hash mismatch")
    server = (
        server_executable.expanduser().resolve(strict=True)
        if server_executable is not None
        else _current_server_executable()
    )
    if os.path.normcase(str(server)) != os.path.normcase(
        str(Path(str(entry.get("server_executable") or "")).resolve())
    ) or _sha256(server) != entry.get("server_sha256"):
        raise ValueError("SOUL MCP server binary mismatch")
    bound_config = (config_path or settings.soul_db.parent / "proxy.toml").resolve()
    if _launch_digest(
        server_executable=str(server), config_path=bound_config, client_id=client_id
    ) != entry.get("launch_digest"):
        raise ValueError("SOUL client launch binding mismatch")
    return entry


def _soul_config(settings: ProxySettings) -> SoulConfig:
    return SoulConfig(
        backend="sqlite",
        backend_url=str(settings.soul_db),
        embedding_provider=settings.embedding_provider,
        embedding_dimensions=settings.embedding_dimensions,
        memory_vector_index=settings.memory_vector_index,
    )


async def _run_tool(
    settings: ProxySettings, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    config = _soul_config(settings)
    async with Soul.create(settings.soul_name, config=config) as soul:
        if name == "soul_boot_context":
            content = await soul.boot()
            return {"content": [{"type": "text", "text": content}]}
        if name == "soul_memory_search":
            query = arguments.get("query")
            limit = arguments.get("limit", 4)
            if not isinstance(query, str) or not query.strip() or len(query) > 4096:
                raise ValueError("query must be non-empty text up to 4096 characters")
            if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 8:
                raise ValueError("limit must be an integer from 1 to 8")
            hits = await soul.memory.search(query.strip(), limit=limit)
            payload = [
                {
                    "id": str(hit.memory.id),
                    "content": hit.memory.content,
                    "importance": hit.memory.importance,
                    "scope": hit.memory.scope,
                    "score": round(float(hit.score), 6),
                }
                for hit in hits
            ]
            return {
                "content": [
                    {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
                ],
                "structuredContent": {"memories": payload},
            }
        if name == "soul_memory_store":
            content = arguments.get("content")
            importance = arguments.get("importance", 5)
            if (
                not isinstance(content, str)
                or not 1 <= len(content.strip()) <= 4096
                or "?" in content
            ):
                raise ValueError("content must be a declarative fact up to 4096 characters")
            if (
                not isinstance(importance, int)
                or isinstance(importance, bool)
                or not 1 <= importance <= 10
            ):
                raise ValueError("importance must be an integer from 1 to 10")
            memory_id = await soul.memory.store(
                content.strip(), importance=importance, scope="private"
            )
            return {
                "content": [{"type": "text", "text": f"stored:{memory_id}"}],
                "structuredContent": {"memory_id": str(memory_id)},
            }
    raise ValueError("unknown SOUL tool")


TOOLS = [
    {
        "name": "soul_boot_context",
        "description": "Load the persistent machine identity without dumping all memories.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "idempotentHint": True},
    },
    {
        "name": "soul_memory_search",
        "description": "Search only the local machine soul's authorized persistent memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 4096},
                "limit": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "idempotentHint": True},
    },
    {
        "name": "soul_memory_store",
        "description": "Persist one explicit declarative fact in the local machine soul.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "minLength": 1, "maxLength": 4096},
                "importance": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            },
            "required": ["content"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    },
]


ToolRunner = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class MCPStdioServer:
    def __init__(self, runner: ToolRunner) -> None:
        self.runner = runner
        self.session_id: str | None = None
        self.expires_at = 0.0

    async def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            raise ValueError("invalid JSON-RPC request")
        method = request.get("method")
        request_id = request.get("id")
        if method and str(method).startswith("notifications/"):
            return None
        if request_id is None:
            raise ValueError("request id is required")
        if method == "initialize":
            self.session_id = secrets.token_urlsafe(24)
            self.expires_at = time.monotonic() + ATTACH_TTL_SECONDS
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "soul-local", "version": "0.5.0"},
                "instructions": (
                    "SOUL is the local persistent identity and memory layer. Call "
                    "soul_boot_context once, search memory when prior context matters, "
                    "and store only explicit declarative facts requested by the owner."
                ),
                "_meta": {"soulAttachSession": self.session_id, "ttlSeconds": ATTACH_TTL_SECONDS},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            if self.session_id is None or time.monotonic() >= self.expires_at:
                raise ValueError("SOUL attach session is not active")
            result = {"tools": TOOLS}
        elif method == "tools/call":
            if self.session_id is None or time.monotonic() >= self.expires_at:
                raise ValueError("SOUL attach session is not active")
            params = request.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("arguments", {}), dict):
                raise ValueError("invalid tool call")
            result = await self.runner(str(params.get("name") or ""), params.get("arguments", {}))
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "method not found"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


async def serve(config_path: Path, client_id: str) -> None:
    settings = ProxySettings.from_toml(config_path)
    verify_client_grant(settings, client_id, config_path=config_path)
    server = MCPStdioServer(lambda name, arguments: _run_tool(settings, name, arguments))
    while True:
        line = await asyncio.to_thread(sys.stdin.buffer.readline)
        if not line:
            return
        try:
            request = json.loads(line)
            response = await server.handle(request)
        except Exception as exc:
            request_id = request.get("id") if isinstance(locals().get("request"), dict) else None
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": type(exc).__name__},
            }
        if response is not None:
            raw = (json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="soul-mcp-stdio")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--client-id", choices=sorted(ALLOWED_CLIENTS), required=True)
    args = parser.parse_args(argv)
    config = args.config.expanduser().resolve()
    asyncio.run(serve(config, args.client_id))
    return 0


def enroll_main(argv: list[str] | None = None) -> int:
    """Installer-only entry point that hashes the dormant MCP launcher."""

    parser = argparse.ArgumentParser(prog="soul-mcp-enroll")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--client-id", choices=sorted(ALLOWED_CLIENTS), required=True)
    parser.add_argument("--parent-executable", type=Path, required=True)
    parser.add_argument("--server-executable", type=Path, required=True)
    args = parser.parse_args(argv)
    config = args.config.expanduser().resolve()
    settings = ProxySettings.from_toml(config)
    entry = enroll_client(
        settings,
        args.client_id,
        parent_executable=args.parent_executable,
        server_executable=args.server_executable,
        config_path=config,
    )
    print(json.dumps({"enrolled": args.client_id, "parent_sha256": entry["parent_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
