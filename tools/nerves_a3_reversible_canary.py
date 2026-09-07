#!/usr/bin/env python3
"""Bounded A3 write canary with mandatory, byte-exact rollback.

The canary has no caller-controlled path. Each agent receives one private
state file below a fixed runtime root. A run:

1. snapshots the exact pre-state;
2. writes and verifies a typed A3 marker;
3. restores the snapshot from a ``finally`` block;
4. verifies the final state byte-for-byte; and
5. persists a canonical receipt outside the rolled-back target.

All leaf operations are relative to pinned directory descriptors and reject
symbolic links. This proves the reversible-write mechanism; it does not
authorize a real repair or mutate a NERVES mission, service, database, or repo
file.
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
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_CLOEXEC
if hasattr(os, "O_NOFOLLOW"):
    _DIR_FLAGS |= os.O_NOFOLLOW
    _FILE_FLAGS |= os.O_NOFOLLOW


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


def _validate_component(name: str) -> str:
    if not name or name in {".", ".."} or "/" in name or "\x00" in name:
        raise A3CanaryError(f"unsafe_path_component:{name!r}")
    return name


def _validate_directory_fd(fd: int, label: str) -> None:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        raise A3CanaryError(f"unsafe_directory:{label}")
    if info.st_uid != os.geteuid():
        raise A3CanaryError(f"foreign_directory_owner:{label}")
    os.fchmod(fd, 0o700)


def _fixed_root() -> tuple[Path, int]:
    root = Path(os.path.abspath(os.fspath(DEFAULT_ROOT.expanduser())))
    if root.resolve(strict=False) != root:
        raise A3CanaryError(f"unsafe_root_symlink:{root}")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        fd = os.open(root, _DIR_FLAGS)
    except OSError as exc:
        raise A3CanaryError(f"unsafe_root:{root}") from exc
    try:
        _validate_directory_fd(fd, str(root))
    except Exception:
        os.close(fd)
        raise
    return root, fd


def _open_dir_at(parent_fd: int, name: str) -> int:
    name = _validate_component(name)
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise A3CanaryError(f"unsafe_directory:{name}") from exc
    try:
        _validate_directory_fd(fd, name)
    except Exception:
        os.close(fd)
        raise
    return fd


def _validate_regular_fd(fd: int, label: str) -> os.stat_result:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise A3CanaryError(f"unsafe_file:{label}")
    if info.st_uid != os.geteuid():
        raise A3CanaryError(f"foreign_file_owner:{label}")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise A3CanaryError(f"file_mode_not_0600:{label}")
    if info.st_size > MAX_STATE_BYTES:
        raise A3CanaryError(f"file_too_large:{label}")
    return info


def _read_at(dir_fd: int, name: str) -> bytes | None:
    name = _validate_component(name)
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise A3CanaryError(f"unsafe_file:{name}") from exc
    try:
        info = _validate_regular_fd(fd, name)
        chunks: list[bytes] = []
        remaining = info.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 131_072))
            if not chunk:
                raise A3CanaryError(f"short_read:{name}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise A3CanaryError(f"file_grew_during_read:{name}")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _write_all(fd: int, raw: bytes) -> None:
    view = memoryview(raw)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise A3CanaryError("short_write")
        view = view[written:]


def _atomic_write_at(dir_fd: int, name: str, raw: bytes) -> None:
    name = _validate_component(name)
    if len(raw) > MAX_STATE_BYTES:
        raise A3CanaryError("write_too_large")
    temp = f".{name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(temp, flags, 0o600, dir_fd=dir_fd)
    try:
        os.fchmod(fd, 0o600)
        _write_all(fd, raw)
        os.fsync(fd)
        _validate_regular_fd(fd, temp)
    finally:
        os.close(fd)
    try:
        os.replace(
            temp,
            name,
            src_dir_fd=dir_fd,
            dst_dir_fd=dir_fd,
        )
        os.fsync(dir_fd)
    finally:
        try:
            os.unlink(temp, dir_fd=dir_fd)
        except FileNotFoundError:
            pass


def _remove_at(dir_fd: int, name: str) -> None:
    if _read_at(dir_fd, name) is None:
        return
    os.unlink(_validate_component(name), dir_fd=dir_fd)
    os.fsync(dir_fd)


def _state(raw: bytes | None) -> dict[str, Any]:
    return {
        "exists": raw is not None,
        "sha256": _sha256(raw) if raw is not None else None,
        "bytes": len(raw) if raw is not None else 0,
    }


def _open_lock(agent_fd: int) -> int:
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(".lock", flags, 0o600, dir_fd=agent_fd)
    except OSError as exc:
        raise A3CanaryError("unsafe_lock") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise A3CanaryError("unsafe_lock")
        os.fchmod(fd, 0o600)
        path_info = os.stat(".lock", dir_fd=agent_fd, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino):
            raise A3CanaryError("lock_identity_changed")
    except Exception:
        os.close(fd)
        raise
    return fd


def _verify_mutation(agent_fd: int, expected: bytes) -> dict[str, Any]:
    observed = _read_at(agent_fd, "state.json")
    if observed != expected:
        raise A3CanaryError("mutation_verification_failed")
    return _state(observed)


def _restore_snapshot(agent_fd: int, before_raw: bytes | None) -> None:
    if before_raw is None:
        _remove_at(agent_fd, "state.json")
    else:
        _atomic_write_at(agent_fd, "state.json", before_raw)


def run_canary(agent: str) -> dict[str, Any]:
    agent = _validate_agent(agent)
    root, root_fd = _fixed_root()
    agent_fd = receipts_fd = receipt_agent_fd = lock_fd = None
    try:
        agent_fd = _open_dir_at(root_fd, agent)
        receipts_fd = _open_dir_at(root_fd, "receipts")
        receipt_agent_fd = _open_dir_at(receipts_fd, agent)
        lock_fd = _open_lock(agent_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        before_raw = _read_at(agent_fd, "state.json")
        before = _state(before_raw)
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
        mutation_error: BaseException | None = None
        mutated: dict[str, Any] | None = None
        rollback_error: BaseException | None = None
        try:
            _atomic_write_at(agent_fd, "state.json", mutation_raw)
            mutated = _verify_mutation(agent_fd, mutation_raw)
            if mutated["sha256"] == before["sha256"]:
                raise A3CanaryError("mutation_not_discriminating")
        except BaseException as exc:
            mutation_error = exc
        finally:
            try:
                _restore_snapshot(agent_fd, before_raw)
                after_raw = _read_at(agent_fd, "state.json")
                after = _state(after_raw)
                if after_raw != before_raw or after != before:
                    raise A3CanaryError("rollback_verification_failed")
            except BaseException as exc:
                rollback_error = exc

        if rollback_error is not None:
            raise A3CanaryError("rollback_verification_failed") from rollback_error
        if mutation_error is not None:
            raise mutation_error
        if mutated is None:
            raise A3CanaryError("mutation_verification_missing")

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
        receipt_name = f"{run_id}.json"
        _atomic_write_at(receipt_agent_fd, receipt_name, receipt_raw)
        if _read_at(receipt_agent_fd, receipt_name) != receipt_raw:
            raise A3CanaryError("receipt_persistence_failed")
        receipt["receipt_path"] = str(root / "receipts" / agent / receipt_name)
        receipt["receipt_sha256"] = _sha256(receipt_raw)
        return receipt
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        for fd in (receipt_agent_fd, receipts_fd, agent_fd, root_fd):
            if fd is not None:
                os.close(fd)


def _latest_receipt(receipt_fd: int, agent: str) -> tuple[str, bytes]:
    candidates: list[tuple[int, str, bytes]] = []
    for name in os.listdir(receipt_fd):
        if not name.endswith(".json"):
            continue
        try:
            run_id = str(uuid.UUID(name.removesuffix(".json")))
        except ValueError as exc:
            raise A3CanaryError(f"invalid_receipt_filename:{agent}:{name}") from exc
        if name != f"{run_id}.json":
            raise A3CanaryError(f"noncanonical_receipt_filename:{agent}:{name}")
        raw = _read_at(receipt_fd, name)
        if raw is None:
            raise A3CanaryError(f"receipt_disappeared:{agent}")
        info = os.stat(name, dir_fd=receipt_fd, follow_symlinks=False)
        candidates.append((info.st_mtime_ns, name, raw))
    if not candidates:
        raise A3CanaryError(f"missing_receipt:{agent}")
    _, name, raw = max(candidates, key=lambda item: (item[0], item[1]))
    return name, raw


def verify_latest(agents: Iterable[str] = AGENTS) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    _, root_fd = _fixed_root()
    receipts_fd = None
    try:
        try:
            receipts_fd = os.open("receipts", _DIR_FLAGS, dir_fd=root_fd)
            _validate_directory_fd(receipts_fd, "receipts")
        except (FileNotFoundError, OSError) as exc:
            raise A3CanaryError("missing_receipt_directory") from exc
        for raw_agent in agents:
            agent = _validate_agent(raw_agent)
            receipt_agent_fd = agent_fd = None
            try:
                try:
                    receipt_agent_fd = os.open(
                        agent, _DIR_FLAGS, dir_fd=receipts_fd
                    )
                    _validate_directory_fd(receipt_agent_fd, f"receipts/{agent}")
                except (FileNotFoundError, OSError) as exc:
                    raise A3CanaryError(
                        f"missing_receipt_directory:{agent}"
                    ) from exc
                filename, raw = _latest_receipt(receipt_agent_fd, agent)
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise A3CanaryError(f"invalid_receipt_json:{agent}") from exc
                if not isinstance(value, dict) or _canonical(value) != raw:
                    raise A3CanaryError(f"noncanonical_receipt:{agent}")
                required = {
                    "schema": RECEIPT_SCHEMA,
                    "agent": agent,
                    "risk_class": "A3_REVERSIBLE_WRITE",
                    "target": f"{agent}/state.json",
                    "mutation_verified": True,
                    "rollback_verified": True,
                }
                for key, expected in required.items():
                    if value.get(key) != expected:
                        raise A3CanaryError(
                            f"receipt_contract_mismatch:{agent}:{key}"
                        )
                try:
                    run_id = str(uuid.UUID(str(value.get("run_id", ""))))
                except ValueError as exc:
                    raise A3CanaryError(f"invalid_run_id:{agent}") from exc
                if filename != f"{run_id}.json":
                    raise A3CanaryError(f"receipt_run_id_mismatch:{agent}")

                try:
                    agent_fd = os.open(agent, _DIR_FLAGS, dir_fd=root_fd)
                    _validate_directory_fd(agent_fd, agent)
                except (FileNotFoundError, OSError) as exc:
                    raise A3CanaryError(f"missing_agent_directory:{agent}") from exc
                observed = _state(_read_at(agent_fd, "state.json"))
                if (
                    observed != value.get("after")
                    or value.get("before") != value.get("after")
                ):
                    raise A3CanaryError(f"rollback_state_mismatch:{agent}")
                rows.append(
                    {
                        "agent": agent,
                        "run_id": run_id,
                        "receipt_sha256": _sha256(raw),
                        "rollback_verified": True,
                    }
                )
            finally:
                if agent_fd is not None:
                    os.close(agent_fd)
                if receipt_agent_fd is not None:
                    os.close(receipt_agent_fd)
    finally:
        if receipts_fd is not None:
            os.close(receipts_fd)
        os.close(root_fd)
    return rows


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("run", "verify"), help="execute or verify receipts"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--agent", action="append", dest="agents")
    group.add_argument("--all", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    agents = AGENTS if args.all else tuple(args.agents or ())
    try:
        if args.command == "run":
            rows = [run_canary(agent) for agent in agents]
        else:
            rows = verify_latest(agents)
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
