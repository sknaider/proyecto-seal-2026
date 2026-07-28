#!/usr/bin/env python3
"""Audit and stage lifecycle metadata for SEAL Store-A identity tokens.

This tool never prints token bytes and does not rotate or revoke credentials.
`bootstrap-shadow` only adds private metadata beside an existing token so a
later coordinated rollout can measure age before enforcement.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import secrets
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

SCHEMA = "seal.identity-token-lifecycle.v1"
DEFAULT_AGENTS = ("ADA", "ALICE", "JARVIS", "NEXUS")
DEFAULT_TTL_HOURS = 168


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_private_regular(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f"{path}: not_regular")
        if info.st_uid != os.geteuid():
            raise RuntimeError(f"{path}: wrong_owner")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError(f"{path}: not_private")
        raw = os.read(fd, 4097)
    finally:
        os.close(fd)
    if len(raw) > 4096:
        raise RuntimeError(f"{path}: oversized")
    token = raw.decode("utf-8").strip()
    if not token:
        raise RuntimeError(f"{path}: empty")
    return token


def _atomic_private_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            json.dump(payload, fh, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _atomic_private_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            fh.write(value)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _meta_path(token_path: Path) -> Path:
    return token_path.with_suffix(".token.meta.json")


@contextlib.contextmanager
def _lifecycle_lock(token_dir: Path):
    token_dir.mkdir(parents=True, exist_ok=True)
    lock_path = token_dir / ".identity-lifecycle.lock"
    fd = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _validated_meta(
    token: str,
    meta_path: Path,
    agent: str,
    mode: str,
    *,
    now: datetime,
) -> dict:
    meta = json.loads(_read_private_regular(meta_path))
    if meta.get("schema") != SCHEMA:
        raise RuntimeError(f"{meta_path}: unsupported_schema")
    if meta.get("agent") != agent:
        raise RuntimeError(f"{meta_path}: agent_mismatch")
    if meta.get("mode") != mode or meta.get("enforced") is not True:
        raise RuntimeError(f"{meta_path}: invalid_mode")
    if meta.get("token_sha256") != _digest(token):
        raise RuntimeError(f"{meta_path}: token_metadata_mismatch")
    if meta.get("revoked_at"):
        raise RuntimeError(f"{meta_path}: already_revoked")
    expires_at = datetime.fromisoformat(str(meta["expires_at"]).replace("Z", "+00:00"))
    if expires_at <= now:
        raise RuntimeError(f"{meta_path}: expired")
    return meta


def promote_next(
    token_dir: Path,
    agents: Iterable[str],
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Swap NEXT into CURRENT while retaining the old generation as grace."""
    observed_at = now or _utcnow()
    rows: list[dict] = []
    with _lifecycle_lock(token_dir):
        for raw_agent in agents:
            agent = raw_agent.strip().upper()
            current_path = token_dir / f"{agent}.token"
            current_meta_path = _meta_path(current_path)
            next_path = token_dir / f"{agent}.token.next"
            next_meta_path = token_dir / f"{agent}.token.next.meta.json"
            current = _read_private_regular(current_path)
            next_token = _read_private_regular(next_path)
            current_meta = _validated_meta(
                current, current_meta_path, agent, "CURRENT", now=observed_at
            )
            next_meta = _validated_meta(
                next_token, next_meta_path, agent, "NEXT", now=observed_at
            )
            current_generation = int(current_meta.get("generation", 0))
            next_generation = int(next_meta.get("generation", 0))
            if next_generation != current_generation + 1:
                raise RuntimeError(f"{next_meta_path}: generation_not_successor")

            promoted_meta = {
                **next_meta,
                "mode": "CURRENT",
                "promoted_at": _iso(observed_at),
                "previous_generation": current_generation,
            }
            grace_meta = {
                **current_meta,
                "mode": "NEXT",
                "grace_after_promotion": True,
                "superseded_by_generation": next_generation,
                "superseded_at": _iso(observed_at),
            }

            # At every boundary at least one slot remains valid.
            _atomic_private_text(current_path, next_token)
            _atomic_private_json(current_meta_path, promoted_meta)
            _atomic_private_text(next_path, current)
            _atomic_private_json(next_meta_path, grace_meta)
            rows.append(
                {
                    "agent": agent,
                    "action": "promoted_with_grace",
                    "current_generation": next_generation,
                    "grace_generation": current_generation,
                    "current_expires_at": promoted_meta["expires_at"],
                    "grace_expires_at": grace_meta["expires_at"],
                }
            )
    return rows


def retire_previous(
    token_dir: Path,
    agents: Iterable[str],
    *,
    archive_dir: Path,
    now: datetime | None = None,
) -> list[dict]:
    """Remove a superseded grace credential from the live acceptance surface."""
    observed_at = now or _utcnow()
    archive_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(archive_dir, 0o700)
    rows: list[dict] = []
    with _lifecycle_lock(token_dir):
        for raw_agent in agents:
            agent = raw_agent.strip().upper()
            current_path = token_dir / f"{agent}.token"
            current_meta_path = _meta_path(current_path)
            next_path = token_dir / f"{agent}.token.next"
            next_meta_path = token_dir / f"{agent}.token.next.meta.json"
            current = _read_private_regular(current_path)
            grace = _read_private_regular(next_path)
            current_meta = _validated_meta(
                current, current_meta_path, agent, "CURRENT", now=observed_at
            )
            grace_meta = _validated_meta(
                grace, next_meta_path, agent, "NEXT", now=observed_at
            )
            if grace_meta.get("grace_after_promotion") is not True:
                raise RuntimeError(f"{next_meta_path}: not_promotion_grace")
            if int(grace_meta.get("superseded_by_generation", 0)) != int(
                current_meta.get("generation", 0)
            ):
                raise RuntimeError(f"{next_meta_path}: supersession_mismatch")

            retired_meta = {
                **grace_meta,
                "revoked_at": _iso(observed_at),
                "revocation_reason": "coordinated_store_a_generation_promotion",
            }
            archive_token = archive_dir / f"{agent}.token.retired"
            archive_meta = archive_dir / f"{agent}.token.retired.meta.json"
            if archive_token.exists() or archive_meta.exists():
                raise RuntimeError(f"{archive_dir}: archive_target_exists")
            _atomic_private_text(archive_token, grace)
            _atomic_private_json(archive_meta, retired_meta)
            next_path.unlink()
            next_meta_path.unlink()
            rows.append(
                {
                    "agent": agent,
                    "action": "previous_retired",
                    "current_generation": int(current_meta["generation"]),
                    "retired_generation": int(grace_meta["generation"]),
                    "archive_token": str(archive_token),
                    "archive_metadata": str(archive_meta),
                }
            )
    return rows


def bootstrap_shadow(
    token_dir: Path,
    agents: Iterable[str],
    *,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    now: datetime | None = None,
) -> list[dict]:
    if ttl_hours <= 0:
        raise ValueError("ttl_hours_must_be_positive")
    observed_at = now or _utcnow()
    rows: list[dict] = []
    for raw_agent in agents:
        agent = raw_agent.strip().upper()
        token_path = token_dir / f"{agent}.token"
        meta_path = _meta_path(token_path)
        token = _read_private_regular(token_path)
        if meta_path.exists():
            meta = json.loads(_read_private_regular(meta_path))
            if meta.get("schema") != SCHEMA:
                raise RuntimeError(f"{meta_path}: unsupported_schema")
            if meta.get("token_sha256") != _digest(token):
                raise RuntimeError(f"{meta_path}: token_metadata_mismatch")
            action = "kept"
        else:
            meta = {
                "schema": SCHEMA,
                "agent": agent,
                "mode": "SHADOW",
                "generation": 1,
                "token_sha256": _digest(token),
                "issued_at": _iso(observed_at),
                "expires_at": _iso(observed_at + timedelta(hours=ttl_hours)),
                "issued_at_basis": "bootstrap_observed_existing_token",
                "enforced": False,
            }
            _atomic_private_json(meta_path, meta)
            action = "created"
        rows.append(
            {
                "agent": agent,
                "action": action,
                "mode": meta["mode"],
                "generation": meta["generation"],
                "expires_at": meta["expires_at"],
                "token_fingerprint": meta["token_sha256"][:12],
            }
        )
    return rows


def promote_current(
    token_dir: Path,
    agents: Iterable[str],
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Enable lifecycle enforcement for existing current tokens without rotation."""
    observed_at = now or _utcnow()
    rows: list[dict] = []
    for raw_agent in agents:
        agent = raw_agent.strip().upper()
        token_path = token_dir / f"{agent}.token"
        meta_path = _meta_path(token_path)
        token = _read_private_regular(token_path)
        meta = json.loads(_read_private_regular(meta_path))
        if meta.get("schema") != SCHEMA:
            raise RuntimeError(f"{meta_path}: unsupported_schema")
        if meta.get("agent") != agent:
            raise RuntimeError(f"{meta_path}: agent_mismatch")
        if meta.get("token_sha256") != _digest(token):
            raise RuntimeError(f"{meta_path}: token_metadata_mismatch")
        if datetime.fromisoformat(str(meta["expires_at"]).replace("Z", "+00:00")) <= observed_at:
            raise RuntimeError(f"{meta_path}: expired")
        meta.update(
            {
                "mode": "CURRENT",
                "enforced": True,
                "promoted_at": _iso(observed_at),
            }
        )
        _atomic_private_json(meta_path, meta)
        rows.append(
            {
                "agent": agent,
                "action": "promoted",
                "mode": "CURRENT",
                "generation": meta["generation"],
                "expires_at": meta["expires_at"],
                "token_fingerprint": meta["token_sha256"][:12],
            }
        )
    return rows


def stage_next(
    token_dir: Path,
    agents: Iterable[str],
    *,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    now: datetime | None = None,
) -> list[dict]:
    """Create an accepted next generation without changing the live current token."""
    if ttl_hours <= 0:
        raise ValueError("ttl_hours_must_be_positive")
    observed_at = now or _utcnow()
    rows: list[dict] = []
    for raw_agent in agents:
        agent = raw_agent.strip().upper()
        current_path = token_dir / f"{agent}.token"
        current_meta_path = _meta_path(current_path)
        current = _read_private_regular(current_path)
        current_meta = json.loads(_read_private_regular(current_meta_path))
        if current_meta.get("schema") != SCHEMA:
            raise RuntimeError(f"{current_meta_path}: unsupported_schema")
        if current_meta.get("token_sha256") != _digest(current):
            raise RuntimeError(f"{current_meta_path}: token_metadata_mismatch")
        if current_meta.get("mode") != "CURRENT" or current_meta.get("enforced") is not True:
            raise RuntimeError(f"{current_meta_path}: current_not_enforced")

        next_path = token_dir / f"{agent}.token.next"
        next_meta_path = token_dir / f"{agent}.token.next.meta.json"
        if next_path.exists() or next_meta_path.exists():
            if not (next_path.exists() and next_meta_path.exists()):
                raise RuntimeError(f"{next_path}: incomplete_next_generation")
            next_token = _read_private_regular(next_path)
            next_meta = json.loads(_read_private_regular(next_meta_path))
            if (
                next_meta.get("schema") != SCHEMA
                or next_meta.get("agent") != agent
                or next_meta.get("mode") != "NEXT"
                or next_meta.get("enforced") is not True
                or next_meta.get("token_sha256") != _digest(next_token)
            ):
                raise RuntimeError(f"{next_meta_path}: invalid_next_generation")
            action = "kept"
        else:
            next_token = secrets.token_urlsafe(48)
            generation = int(current_meta.get("generation", 0)) + 1
            next_meta = {
                "schema": SCHEMA,
                "agent": agent,
                "mode": "NEXT",
                "generation": generation,
                "token_sha256": _digest(next_token),
                "issued_at": _iso(observed_at),
                "expires_at": _iso(observed_at + timedelta(hours=ttl_hours)),
                "issued_at_basis": "coordinated_stage_next",
                "enforced": True,
                "previous_generation": int(current_meta.get("generation", 0)),
            }
            _atomic_private_text(next_path, next_token)
            try:
                _atomic_private_json(next_meta_path, next_meta)
            except Exception:
                next_path.unlink(missing_ok=True)
                raise
            action = "created"
        rows.append(
            {
                "agent": agent,
                "action": action,
                "mode": "NEXT",
                "generation": next_meta["generation"],
                "expires_at": next_meta["expires_at"],
                "token_fingerprint": next_meta["token_sha256"][:12],
            }
        )
    return rows


def audit(token_dirs: Iterable[Path], agents: Iterable[str]) -> dict:
    dirs = [Path(d) for d in token_dirs]
    rows: list[dict] = []
    for raw_agent in agents:
        agent = raw_agent.strip().upper()
        seen: list[tuple[str, str]] = []
        for token_dir in dirs:
            token_path = token_dir / f"{agent}.token"
            row = {
                "agent": agent,
                "directory": str(token_dir),
                "token_present": token_path.exists(),
                "metadata_present": _meta_path(token_path).exists(),
            }
            if token_path.exists():
                try:
                    token = _read_private_regular(token_path)
                    seen.append((str(token_dir), _digest(token)))
                    row["private_regular"] = True
                    row["token_fingerprint"] = _digest(token)[:12]
                except Exception as exc:
                    row["private_regular"] = False
                    row["error"] = str(exc).split(":")[-1].strip()
            rows.append(row)
        copies_equal = len(seen) > 1 and len({digest for _, digest in seen}) == 1
        for row in rows:
            if row["agent"] == agent:
                row["copies_equal"] = copies_equal
    return {
        "schema": SCHEMA,
        "observed_at": _iso(_utcnow()),
        "euid": os.geteuid(),
        "same_uid_isolation": False,
        "same_uid_reason": "file modes do not isolate processes sharing one uid",
        "rows": rows,
    }


def _default_dirs() -> list[Path]:
    runtime = os.environ.get("SEAL_TOKENS_DIR")
    if not runtime:
        runtime = str(Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.geteuid()}")) / "seal")
    dirs = [Path(runtime)]
    if os.environ.get("SEAL_DISABLE_LEGACY_TMP_TOKENS") != "1":
        dirs.append(Path("/tmp/seal_tokens"))
    return list(dict.fromkeys(dirs))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", action="append", dest="agents")
    parser.add_argument("--token-dir", action="append", dest="token_dirs")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit")
    bootstrap = sub.add_parser("bootstrap-shadow")
    bootstrap.add_argument("--ttl-hours", type=int, default=DEFAULT_TTL_HOURS)
    sub.add_parser("promote-current")
    stage = sub.add_parser("stage-next")
    stage.add_argument("--ttl-hours", type=int, default=DEFAULT_TTL_HOURS)
    sub.add_parser("promote-next")
    retire = sub.add_parser("retire-previous")
    retire.add_argument("--archive-dir", type=Path, required=True)
    args = parser.parse_args()
    agents = tuple(args.agents or DEFAULT_AGENTS)
    dirs = [Path(p) for p in (args.token_dirs or _default_dirs())]
    if args.command == "audit":
        result = audit(dirs, agents)
    elif args.command == "bootstrap-shadow":
        result = {
            "schema": SCHEMA,
            "mode": "SHADOW",
            "enforcement_changed": False,
            "rotated": False,
            "directories": [
                {
                    "directory": str(token_dir),
                    "rows": bootstrap_shadow(
                        token_dir,
                        agents,
                        ttl_hours=args.ttl_hours,
                    ),
                }
                for token_dir in dirs
            ],
        }
    elif args.command == "promote-current":
        result = {
            "schema": SCHEMA,
            "mode": "CURRENT",
            "enforcement_changed": True,
            "rotated": False,
            "directories": [
                {
                    "directory": str(token_dir),
                    "rows": promote_current(token_dir, agents),
                }
                for token_dir in dirs
            ],
        }
    elif args.command == "stage-next":
        result = {
            "schema": SCHEMA,
            "mode": "NEXT",
            "enforcement_changed": False,
            "rotated": False,
            "directories": [
                {
                    "directory": str(token_dir),
                    "rows": stage_next(
                        token_dir,
                        agents,
                        ttl_hours=args.ttl_hours,
                    ),
                }
                for token_dir in dirs
            ],
        }
    elif args.command == "promote-next":
        result = {
            "schema": SCHEMA,
            "mode": "CURRENT_PLUS_GRACE",
            "enforcement_changed": True,
            "rotated": True,
            "directories": [
                {
                    "directory": str(token_dir),
                    "rows": promote_next(token_dir, agents),
                }
                for token_dir in dirs
            ],
        }
    else:
        if len(dirs) != 1:
            raise RuntimeError("retire-previous requires exactly one live token directory")
        result = {
            "schema": SCHEMA,
            "mode": "CURRENT",
            "enforcement_changed": True,
            "rotated": True,
            "directories": [
                {
                    "directory": str(dirs[0]),
                    "rows": retire_previous(
                        dirs[0],
                        agents,
                        archive_dir=args.archive_dir,
                    ),
                }
            ],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
