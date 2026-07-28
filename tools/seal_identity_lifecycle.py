#!/usr/bin/env python3
"""Audit and stage lifecycle metadata for SEAL Store-A identity tokens.

This tool never prints token bytes and does not rotate or revoke credentials.
`bootstrap-shadow` only adds private metadata beside an existing token so a
later coordinated rollout can measure age before enforcement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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


def _meta_path(token_path: Path) -> Path:
    return token_path.with_suffix(".token.meta.json")


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
    else:
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
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
