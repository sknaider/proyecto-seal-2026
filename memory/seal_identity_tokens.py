"""Fail-closed readers for Store-A agent identity tokens.

The module never logs or returns token metadata.  It supports a coordinated
``current + next`` overlap while rotation is in flight and enforces lifecycle
metadata when ``SEAL_TOKEN_LIFECYCLE_MODE=ENFORCE``.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SCHEMA = "seal.identity-token-lifecycle.v1"


def lifecycle_mode() -> str:
    mode = os.environ.get("SEAL_TOKEN_LIFECYCLE_MODE", "OFF").strip().upper()
    return mode if mode in {"OFF", "SHADOW", "ENFORCE"} else "OFF"


def token_candidates(token_dir: str | Path, agent: str) -> tuple[tuple[Path, Path], ...]:
    root = Path(token_dir)
    name = agent.upper()
    return (
        (root / f"{name}.token", root / f"{name}.token.meta.json"),
        (root / f"{name}.token.next", root / f"{name}.token.next.meta.json"),
    )


def _read_private_regular(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError("not_regular")
        if info.st_uid != os.geteuid():
            raise RuntimeError("wrong_owner")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError("not_private")
        raw = os.read(fd, 4097)
    finally:
        os.close(fd)
    if len(raw) > 4096:
        raise RuntimeError("oversized")
    value = raw.decode("utf-8").strip()
    if not value:
        raise RuntimeError("empty")
    return value


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError("missing_expiry")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise RuntimeError("naive_expiry")
    return parsed.astimezone(timezone.utc)


def _metadata_allows(
    token: str,
    meta_path: Path,
    agent: str,
    slot: str,
    *,
    now: datetime,
) -> bool:
    try:
        meta = json.loads(_read_private_regular(meta_path))
        expected_mode = "CURRENT" if slot == "current" else "NEXT"
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return bool(
            meta.get("schema") == SCHEMA
            and meta.get("agent") == agent.upper()
            and meta.get("mode") == expected_mode
            and meta.get("enforced") is True
            and meta.get("token_sha256") == digest
            and not meta.get("revoked_at")
            and _parse_time(meta.get("expires_at")) > now
        )
    except (OSError, ValueError, TypeError, RuntimeError, json.JSONDecodeError):
        return False


def valid_tokens(
    token_dirs: Iterable[str | Path],
    agent: str,
    *,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Return valid current/next values without exposing provenance or metadata."""
    mode = lifecycle_mode()
    observed_at = now or datetime.now(timezone.utc)
    values: list[str] = []
    for token_dir in token_dirs:
        for index, (token_path, meta_path) in enumerate(token_candidates(token_dir, agent)):
            try:
                token = _read_private_regular(token_path)
            except (OSError, UnicodeError, RuntimeError):
                continue
            if mode == "ENFORCE" and not _metadata_allows(
                token,
                meta_path,
                agent,
                "current" if index == 0 else "next",
                now=observed_at,
            ):
                continue
            if token not in values:
                values.append(token)
    return tuple(values)


def token_owner(
    token_dirs: Iterable[str | Path],
    known_agents: Iterable[str],
    token: str | None,
    *,
    now: datetime | None = None,
) -> str | None:
    if not token:
        return None
    for agent in known_agents:
        if token in valid_tokens(token_dirs, agent, now=now):
            return agent.upper()
    return None
