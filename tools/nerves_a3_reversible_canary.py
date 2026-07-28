#!/usr/bin/env python3
"""Bounded A3 write canary with mandatory, byte-exact rollback.

The canary never accepts an arbitrary path.  Each agent receives one private
state file below a fixed runtime root.  A run:

1. snapshots the exact pre-state;
2. writes and verifies a typed A3 marker;
3. restores the snapshot (or removes the newly-created marker);
4. verifies the final state byte-for-byte; and
5. persists a receipt outside the rolled-back target.

This proves the reversible-write mechanism.  It does not authorize a real
repair and it never mutates a NERVES mission, service, database, or repo file.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid
from typing import Any, Iterable


SCHEMA = "seal.nerves.a3-reversible-canary.v1"
RECEIPT_SCHEMA = "seal.nerves.a3-reversible-receipt.v1"
AGENTS = ("ADA", "ALICE", "FABLE", "JARVIS", "NEXUS")
DEFAULT_ROOT = Path.home() / ".local/state/seal/nerves_a3_reversible"
MAX_STATE_BYTES = 1_048_576


class A3CanaryError(RuntimeError):
    """Fail-closed A3 canary error."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _validate_agent(agent: str) -> str:
    normalized = agent.strip().upper()
    if normalized not in AGENTS:
        raise A3CanaryError(f"unknown_agent:{normalized or '<empty>'}")
    return normalized


def _ensure_private_dir(path: Path) -> None:
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise A3CanaryError(f"unsafe_directory:{path}")
        if info.st_uid != os.geteuid():
            raise A3CanaryError(f"foreign_directory_owner:{path}")
        os.chmod(path, 0o700)
        return
    path.mkdir(parents=True, mode=0o700)
    os.chmod(path, 0o700)


def _read_state(path: Path) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise A3CanaryError(f"unsafe_state_file:{path}")
    if info.st_uid != os.geteuid():
        raise A3CanaryError(f"foreign_state_owner:{path}")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise A3CanaryError(f"state_mode_not_0600:{path}")
    if info.st_size > MAX_STATE_BYTES:
        raise A3CanaryError(f"state_too_large:{path}")
    return path.read_bytes()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, raw: bytes) -> None:
    if len(raw) > MAX_STATE_BYTES:
        raise A3CanaryError("write_too_large")
    _ensure_private_dir(path.parent)
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(temp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
        _fsync_dir(path.parent)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _remove_state(path: Path) -> None:
    current = _read_state(path)
    if current is None:
        return
    path.unlink()
    _fsync_dir(path.parent)


def _post_state(path: Path) -> dict[str, Any]:
    raw = _read_state(path)
    return {
        "exists": raw is not None,
        "sha256": _sha256(raw) if raw is not None else None,
        "bytes": len(raw) if raw is not None else 0,
    }


def run_canary(agent: str, *, root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    agent = _validate_agent(agent)
    _ensure_private_dir(root)
    agent_dir = root / agent
    receipt_dir = root / "receipts" / agent
    _ensure_private_dir(agent_dir)
    _ensure_private_dir(root / "receipts")
    _ensure_private_dir(receipt_dir)

    lock_path = agent_dir / ".lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        target = agent_dir / "state.json"
        before_raw = _read_state(target)
        before = _post_state(target)
        run_id = str(uuid.uuid4())
        mutation = {
            "schema": SCHEMA,
            "agent": agent,
            "risk_class": "A3_REVERSIBLE_WRITE",
            "run_id": run_id,
            "mutation": "bounded_private_canary",
            "created_at": _now(),
        }
        mutation_raw = _canonical(mutation)
        _atomic_write(target, mutation_raw)
        mutated_raw = _read_state(target)
        if mutated_raw != mutation_raw:
            raise A3CanaryError("mutation_verification_failed")
        mutated = _post_state(target)
        if mutated["sha256"] == before["sha256"]:
            raise A3CanaryError("mutation_not_discriminating")

        if before_raw is None:
            _remove_state(target)
        else:
            _atomic_write(target, before_raw)

        after_raw = _read_state(target)
        after = _post_state(target)
        if after_raw != before_raw or after != before:
            raise A3CanaryError("rollback_verification_failed")

        receipt = {
            "schema": RECEIPT_SCHEMA,
            "agent": agent,
            "risk_class": "A3_REVERSIBLE_WRITE",
            "run_id": run_id,
            "target": f"{agent}/state.json",
            "before": before,
            "mutation": mutated,
            "after": after,
            "mutation_verified": True,
            "rollback_verified": True,
            "completed_at": _now(),
        }
        receipt_raw = _canonical(receipt)
        receipt_path = receipt_dir / f"{run_id}.json"
        _atomic_write(receipt_path, receipt_raw)
        persisted = _read_state(receipt_path)
        if persisted != receipt_raw:
            raise A3CanaryError("receipt_persistence_failed")
        receipt["receipt_path"] = str(receipt_path)
        receipt["receipt_sha256"] = _sha256(receipt_raw)
        return receipt
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def verify_latest(
    agents: Iterable[str] = AGENTS, *, root: Path = DEFAULT_ROOT
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_agent in agents:
        agent = _validate_agent(raw_agent)
        receipt_dir = root / "receipts" / agent
        if not receipt_dir.is_dir():
            raise A3CanaryError(f"missing_receipt_directory:{agent}")
        candidates = sorted(receipt_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise A3CanaryError(f"missing_receipt:{agent}")
        path = candidates[-1]
        raw = _read_state(path)
        if raw is None:
            raise A3CanaryError(f"missing_receipt:{agent}")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise A3CanaryError(f"invalid_receipt_json:{agent}") from exc
        required = {
            "schema": RECEIPT_SCHEMA,
            "agent": agent,
            "risk_class": "A3_REVERSIBLE_WRITE",
            "mutation_verified": True,
            "rollback_verified": True,
        }
        for key, expected in required.items():
            if value.get(key) != expected:
                raise A3CanaryError(f"receipt_contract_mismatch:{agent}:{key}")
        target = root / str(value.get("target", ""))
        observed = _post_state(target)
        if observed != value.get("after") or value.get("before") != value.get("after"):
            raise A3CanaryError(f"rollback_state_mismatch:{agent}")
        rows.append(
            {
                "agent": agent,
                "run_id": value["run_id"],
                "receipt_sha256": _sha256(raw),
                "rollback_verified": True,
            }
        )
    return rows


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("run", "verify"), help="execute or verify receipts"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--agent", action="append", dest="agents")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    return parser


def main() -> int:
    args = _parser().parse_args()
    agents = AGENTS if args.all else tuple(args.agents or ())
    try:
        if args.command == "run":
            rows = [run_canary(agent, root=args.root) for agent in agents]
        else:
            rows = verify_latest(agents, root=args.root)
    except (A3CanaryError, OSError) as exc:
        print(
            json.dumps(
                {"schema": RECEIPT_SCHEMA, "status": "FAILED", "error": str(exc)},
                sort_keys=True,
            )
        )
        return 4
    print(
        json.dumps(
            {"schema": RECEIPT_SCHEMA, "status": "PASS", "rows": rows},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
