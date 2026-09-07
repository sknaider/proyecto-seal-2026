#!/usr/bin/env python3
"""Rotate the restricted MCP web operator credential without an admin DSN.

The operation is deliberately restartable.  A root-only candidate credential is
persisted *before* ``ALTER ROLE``; if the process dies after PostgreSQL accepts
the new password but before the credential is promoted, rerunning the same
``--rotation-id`` proves the candidate and completes the promotion.

This script never starts the operator.  A deployment may enable it only after
this rotation and the remaining release gates have succeeded.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import asyncpg


ROLE = "svc_mcp_web_soul_operator"
UNIT = "seal-mcp-web-soul-operator.service"
ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1"})
EXPECTED_PORT = 5433
EXPECTED_DATABASE = "seal_memory"
DEFAULT_DIRECTORY = Path("/etc/seal/mcp-web-soul-operator")
DEFAULT_TARGET = DEFAULT_DIRECTORY / "operator.dsn"
DEFAULT_LEGACY = Path("/home/dadito/.config/seal/mcp_web_soul_operator.env")
ROTATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
GENERATED_PASSWORD_RE = re.compile(r"^[A-Za-z0-9_-]{40,128}$")


class RotationError(RuntimeError):
    """Fail-closed operational error whose message never contains a secret."""


class Database(Protocol):
    def probe(self, dsn: str) -> bool: ...

    def alter_own_password_and_revoke_sessions(self, dsn: str, new_password: str) -> None: ...

    def revoke_other_sessions(self, dsn: str) -> None: ...


class OperatorStopper(Protocol):
    def stop_and_verify(self) -> None: ...


@dataclass(frozen=True)
class Paths:
    directory: Path
    target: Path
    legacy: Path

    @property
    def candidate(self) -> Path:
        return self.directory / "operator.dsn.next"

    @property
    def state(self) -> Path:
        return self.directory / "rotation.state"

    @property
    def receipt(self) -> Path:
        return self.directory / "rotation.receipt"


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_dsn(raw: str) -> tuple[str, str, str, str, str]:
    """Return safe DSN, pgpass host/port/database/user and password."""

    try:
        parsed = urlsplit(raw.strip())
        username = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        host = parsed.hostname or ""
        port = str(parsed.port or 5432)
        database = unquote(parsed.path.lstrip("/"))
    except (TypeError, ValueError) as exc:
        raise RotationError("operator credential is not a valid PostgreSQL DSN") from exc
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise RotationError("operator credential is not a PostgreSQL DSN")
    if username != ROLE or not password or not host or not database:
        raise RotationError("operator credential has the wrong restricted identity")
    # The legacy file is still owned by the shared host UID before rotation.
    # Identity alone is therefore insufficient: an attacker could point it at
    # a database they control and create a role with the expected name.  Bind
    # the promotion to the one local production endpoint and reject query
    # parameters that could alter connection routing or TLS behavior.
    if (
        host not in ALLOWED_HOSTS
        or int(port) != EXPECTED_PORT
        or database != EXPECTED_DATABASE
        or parsed.query
        or parsed.fragment
    ):
        raise RotationError("operator credential endpoint is not the approved local database")
    host_for_uri = f"[{host}]" if ":" in host and not host.startswith("[") else host
    netloc = f"{quote(username, safe='')}@{host_for_uri}:{port}"
    safe_dsn = urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))
    return safe_dsn, host, port, database, password


def _with_password(dsn: str, password: str) -> str:
    parsed = urlsplit(dsn)
    username = unquote(parsed.username or "")
    host = parsed.hostname or ""
    port = parsed.port or 5432
    host_for_uri = f"[{host}]" if ":" in host and not host.startswith("[") else host
    netloc = f"{quote(username, safe='')}:{quote(password, safe='')}@{host_for_uri}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))


def _probe_approved(database: Database, dsn: str) -> bool:
    """Never let a database adapter touch an endpoint outside production."""

    _parse_dsn(dsn)
    return database.probe(dsn)


class AsyncpgDatabase:
    """Use only the restricted role itself; never import or derive an admin DSN."""

    @staticmethod
    async def _identity(connection: asyncpg.Connection) -> tuple[str, bool, bool] | None:
        row = await connection.fetchrow(
            "SELECT current_user AS role_name, rolsuper, rolcanlogin "
            "FROM pg_roles WHERE rolname = current_user"
        )
        if row is None:
            return None
        return str(row["role_name"]), bool(row["rolsuper"]), bool(row["rolcanlogin"])

    @staticmethod
    async def _connect(dsn: str) -> asyncpg.Connection:
        _parse_dsn(dsn)
        return await asyncpg.connect(dsn, timeout=5, command_timeout=5)

    async def _probe(self, dsn: str) -> bool:
        try:
            connection = await self._connect(dsn)
        except Exception:
            return False
        try:
            identity = await self._identity(connection)
            return identity == (ROLE, False, True)
        except Exception:
            return False
        finally:
            try:
                await connection.close()
            except Exception:
                pass

    def probe(self, dsn: str) -> bool:
        return asyncio.run(self._probe(dsn))

    @staticmethod
    async def _revoke_other_sessions(connection: asyncpg.Connection) -> None:
        rows = await connection.fetch(
            "SELECT pid, pg_terminate_backend(pid, 5000) AS terminated "
            "FROM pg_stat_activity "
            "WHERE usename=current_user AND pid<>pg_backend_pid()"
        )
        if any(not bool(row["terminated"]) for row in rows):
            raise RotationError("a legacy database session resisted termination")
        remaining = int(await connection.fetchval(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE usename=current_user AND pid<>pg_backend_pid()"
        ))
        if remaining != 0:
            raise RotationError("legacy database sessions remain active")

    async def _alter_own_password_and_revoke_sessions(self, dsn: str, new_password: str) -> None:
        if not GENERATED_PASSWORD_RE.fullmatch(new_password):
            raise RotationError("generated credential has an unsafe format")
        try:
            connection = await self._connect(dsn)
        except Exception as exc:
            raise RotationError("restricted operator credential did not authenticate") from exc
        try:
            if await self._identity(connection) != (ROLE, False, True):
                raise RotationError("database session is not the restricted operator role")
            # Production keeps this role default_transaction_read_only=on.
            # Override that default only inside this atomic rotation: if peer
            # revocation fails, PostgreSQL rolls the password change back too.
            async with connection.transaction():
                await connection.execute("SET LOCAL transaction_read_only=off")
                # PostgreSQL does not parameterize ALTER ROLE. The candidate is
                # constrained to URL-safe non-SQL characters before interpolation;
                # the role name is the session's immutable CURRENT_USER.
                await connection.execute(f"ALTER ROLE CURRENT_USER PASSWORD '{new_password}'")
                # Password rotation does not invalidate sessions authenticated
                # before ALTER ROLE. Revoke them from this same surviving session
                # before it closes, then prove zero peers remain.
                await self._revoke_other_sessions(connection)
        except RotationError:
            raise
        except Exception as exc:
            raise RotationError("restricted role could not rotate its own credential") from exc
        finally:
            try:
                await connection.close()
            except Exception:
                pass

    def alter_own_password_and_revoke_sessions(self, dsn: str, new_password: str) -> None:
        asyncio.run(self._alter_own_password_and_revoke_sessions(dsn, new_password))

    async def _revoke_with_candidate(self, dsn: str) -> None:
        connection = await self._connect(dsn)
        try:
            if await self._identity(connection) != (ROLE, False, True):
                raise RotationError("database session is not the restricted operator role")
            await self._revoke_other_sessions(connection)
        finally:
            try:
                await connection.close()
            except Exception:
                pass

    def revoke_other_sessions(self, dsn: str) -> None:
        asyncio.run(self._revoke_with_candidate(dsn))


class SystemdOperatorStopper:
    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    @staticmethod
    def _operator_pids() -> list[int]:
        found: list[int] = []
        for entry in Path("/proc").glob("[0-9]*"):
            try:
                raw = (entry / "cmdline").read_bytes()
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            args = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
            if any(Path(arg).name == "mcp_web_soul_operator.py" for arg in args):
                if int(entry.name) != os.getpid():
                    found.append(int(entry.name))
        return sorted(found)

    def stop_and_verify(self) -> None:
        self._run(["/usr/bin/systemctl", "stop", UNIT])
        if Path("/run/user/1000").is_dir():
            self._run(
                [
                    "/usr/sbin/runuser", "-u", "dadito", "--", "/usr/bin/env",
                    "XDG_RUNTIME_DIR=/run/user/1000", "/usr/bin/systemctl", "--user",
                    "disable", "--now", UNIT,
                ]
            )
        active = self._run(["/usr/bin/systemctl", "is-active", "--quiet", UNIT])
        pids = self._operator_pids()
        if active.returncode == 0 or pids:
            raise RotationError("operator remains active after stop request")


def _ensure_root_directory(path: Path, *, owner_uid: int, owner_gid: int) -> None:
    if not path.exists():
        path.mkdir(mode=0o700, parents=True)
        os.chown(path, owner_uid, owner_gid)
        os.chmod(path, 0o700)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != owner_uid
        or info.st_gid != owner_gid
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise RotationError("credential directory is not a trusted private directory")


def _read_secure(path: Path, *, owner_uid: int) -> str:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid or stat.S_IMODE(info.st_mode) != 0o600:
        raise RotationError("credential artifact ownership or mode is unsafe")
    return path.read_text(encoding="utf-8").strip()


def _atomic_write(path: Path, content: str, *, owner_uid: int, owner_gid: int) -> None:
    temp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(temp, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, owner_uid, owner_gid)
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _load_json(path: Path, *, owner_uid: int) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(_read_secure(path, owner_uid=owner_uid))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RotationError("rotation metadata is invalid") from exc
    if not isinstance(value, dict):
        raise RotationError("rotation metadata is invalid")
    return value


def _write_json(path: Path, value: dict[str, object], *, owner_uid: int, owner_gid: int) -> None:
    _atomic_write(
        path,
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )


def _read_legacy_dsn(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise RotationError("legacy credential path is not a regular file")
    raw = path.read_text(encoding="utf-8").strip()
    if raw.startswith(("postgresql://", "postgres://")):
        return raw
    for line in raw.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key.strip() == "MCP_WEB_SOUL_OPERATOR_DSN":
            return value.strip().strip('"').strip("'")
    return None


def _invalidate_legacy(path: Path, rotation_id: str) -> None:
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise RotationError("legacy credential path is not a regular file")
    info = path.stat()
    raw = path.read_text(encoding="utf-8")
    tombstone = f"# MCP_WEB_SOUL_OPERATOR_DSN invalidated by root rotation {rotation_id}"
    if raw.strip().startswith(("postgresql://", "postgres://")):
        rewritten = tombstone + "\n"
    else:
        lines: list[str] = []
        replaced = False
        for line in raw.splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _value = line.split("=", 1)
                if key.strip() == "MCP_WEB_SOUL_OPERATOR_DSN":
                    lines.append(tombstone)
                    replaced = True
                    continue
            lines.append(line)
        if not replaced:
            return
        rewritten = "\n".join(lines) + "\n"
    _atomic_write(path, rewritten, owner_uid=info.st_uid, owner_gid=info.st_gid)


FaultHook = Callable[[str], None]


def rotate(
    *,
    rotation_id: str,
    paths: Paths,
    database: Database,
    stopper: OperatorStopper,
    owner_uid: int = 0,
    owner_gid: int = 0,
    password_factory: Callable[[], str] = lambda: secrets.token_urlsafe(48),
    fault_hook: FaultHook | None = None,
) -> dict[str, str]:
    if not ROTATION_ID_RE.fullmatch(rotation_id):
        raise RotationError("invalid rotation id")
    if paths.target.parent != paths.directory or paths.legacy == paths.target:
        raise RotationError("unsafe credential path layout")
    _ensure_root_directory(paths.directory, owner_uid=owner_uid, owner_gid=owner_gid)
    stopper.stop_and_verify()

    receipt = _load_json(paths.receipt, owner_uid=owner_uid)
    if receipt and receipt.get("rotation_id") == rotation_id:
        current = _read_secure(paths.target, owner_uid=owner_uid)
        if receipt.get("credential_sha256") != _fingerprint(current) or not _probe_approved(database, current):
            raise RotationError("completed rotation receipt does not match a working credential")
        database.revoke_other_sessions(current)
        _invalidate_legacy(paths.legacy, rotation_id)
        # A crash after writing the receipt but before removing recovery state
        # must not leave a future rotation permanently blocked.
        if paths.candidate.exists():
            raise RotationError("completed rotation unexpectedly retains a candidate credential")
        try:
            paths.state.unlink()
        except FileNotFoundError:
            pass
        return {"status": "already_complete", "rotation_id": rotation_id}

    state = _load_json(paths.state, owner_uid=owner_uid)
    if state and state.get("rotation_id") != rotation_id:
        raise RotationError("another credential rotation is incomplete")

    current: str | None = None
    if paths.target.exists():
        current = _read_secure(paths.target, owner_uid=owner_uid)
    if current is None:
        current = _read_legacy_dsn(paths.legacy)

    candidate: str | None = None
    if paths.candidate.exists():
        candidate = _read_secure(paths.candidate, owner_uid=owner_uid)
    elif state:
        candidate_hash = str(state.get("candidate_sha256") or "")
        if paths.target.exists():
            promoted = _read_secure(paths.target, owner_uid=owner_uid)
            if candidate_hash and _fingerprint(promoted) == candidate_hash:
                candidate = promoted
    else:
        if current is None or not _probe_approved(database, current):
            raise RotationError("no working restricted operator credential is available")
        safe_dsn, _host, _port, _database, _old_password = _parse_dsn(current)
        new_password = password_factory()
        if not new_password or new_password == _old_password:
            raise RotationError("password generator did not produce a fresh credential")
        candidate = _with_password(safe_dsn, new_password)
        _atomic_write(paths.candidate, candidate + "\n", owner_uid=owner_uid, owner_gid=owner_gid)
        state = {
            "version": 1,
            "rotation_id": rotation_id,
            "phase": "prepared",
            "candidate_sha256": _fingerprint(candidate),
            "previous_sha256": _fingerprint(current),
        }
        _write_json(paths.state, state, owner_uid=owner_uid, owner_gid=owner_gid)

    if candidate is None or state is None:
        raise RotationError("rotation recovery state is incomplete")
    if _fingerprint(candidate) != state.get("candidate_sha256"):
        raise RotationError("candidate credential does not match rotation state")
    _safe, _host, _port, _database, candidate_password = _parse_dsn(candidate)
    if not GENERATED_PASSWORD_RE.fullmatch(candidate_password):
        raise RotationError("candidate credential has an unsafe format")

    candidate_works = _probe_approved(database, candidate)
    current_works = bool(
        current
        and _fingerprint(current) != _fingerprint(candidate)
        and _probe_approved(database, current)
    )
    if not candidate_works:
        if not current_works or current is None:
            raise RotationError("neither previous nor candidate restricted credential authenticates")
        database.alter_own_password_and_revoke_sessions(current, candidate_password)
        state["phase"] = "altered"
        _write_json(paths.state, state, owner_uid=owner_uid, owner_gid=owner_gid)
        if fault_hook:
            fault_hook("after_alter")
        candidate_works = _probe_approved(database, candidate)
        current_works = _probe_approved(database, current)

    if not candidate_works:
        raise RotationError("candidate credential did not authenticate after rotation")
    if current and _fingerprint(current) != _fingerprint(candidate) and current_works:
        raise RotationError("previous credential still authenticates; invalidation is unproven")
    # Crash recovery can arrive here with sessions authenticated before the
    # previous ALTER. The candidate kills every peer again before promotion.
    database.revoke_other_sessions(candidate)

    state["phase"] = "verified"
    _write_json(paths.state, state, owner_uid=owner_uid, owner_gid=owner_gid)
    if fault_hook:
        fault_hook("before_promote")
    if paths.candidate.exists():
        os.replace(paths.candidate, paths.target)
        directory_fd = os.open(paths.directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    promoted = _read_secure(paths.target, owner_uid=owner_uid)
    if _fingerprint(promoted) != state["candidate_sha256"] or not _probe_approved(database, promoted):
        raise RotationError("promoted credential failed final verification")
    state["phase"] = "promoted"
    _write_json(paths.state, state, owner_uid=owner_uid, owner_gid=owner_gid)
    if fault_hook:
        fault_hook("after_promote")

    _invalidate_legacy(paths.legacy, rotation_id)
    receipt_value = {
        "version": 1,
        "rotation_id": rotation_id,
        "status": "complete",
        "role": ROLE,
        "credential_sha256": _fingerprint(promoted),
    }
    _write_json(paths.receipt, receipt_value, owner_uid=owner_uid, owner_gid=owner_gid)
    try:
        paths.state.unlink()
    except FileNotFoundError:
        pass
    return {"status": "rotated", "rotation_id": rotation_id}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely rotate the restricted SOUL operator credential.")
    parser.add_argument("--rotation-id", required=True)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--legacy", type=Path, default=DEFAULT_LEGACY)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if os.geteuid() != 0:
        print("rotation failed: root execution required", file=sys.stderr)
        return 2
    paths = Paths(args.directory, args.directory / "operator.dsn", args.legacy)
    lock_path = Path("/run/lock/seal-mcp-web-soul-operator-rotation.lock")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(lock_fd, 0o600)
        os.fchown(lock_fd, 0, 0)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = rotate(
            rotation_id=args.rotation_id,
            paths=paths,
            database=AsyncpgDatabase(),
            stopper=SystemdOperatorStopper(),
        )
    except Exception as exc:  # no traceback: operational errors may wrap secret-bearing libraries
        print(f"rotation failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        os.close(lock_fd)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
