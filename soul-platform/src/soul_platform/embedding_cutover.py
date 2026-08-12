"""Fail-closed MachineSoul cutover from 128-d embeddings to BGE-M3/portable ANN.

The expensive re-embedding is delegated to ``soul-framework==0.4.2`` and
always produces a separate candidate.  Activation never deletes the original
database: it moves it to a byte-verified backup, promotes the candidate, and
records enough evidence to restore the exact prior database and config.

The caller must stop SOUL Platform before ``activate`` or ``rollback``.  An
exclusive SQLite probe makes this requirement executable instead of advisory.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from soul_framework.embedding.bge_m3 import BgeM3Embedding
from soul_framework.embedding_migration import migrate_sqlite_embeddings

from soul_platform.proxy import ProxySettings

EMBEDDING_BLOCK = """[embedding]
provider = "bge-m3"
dimensions = 1024
model = "bge-m3"
url = "http://127.0.0.1:11434/api/embed"
timeout_seconds = 60
vector_index = "auto"
"""


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    if os.name == "nt" and path.exists():
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)
    return False


def _regular_file(path: Path, label: str) -> Path:
    path = path.expanduser().absolute()
    _safe_parent(path, label)
    if _is_link_or_reparse(path) or not path.is_file():
        raise ValueError(f"{label} must be a regular file, never a symlink/reparse point")
    return path.resolve()


def _safe_parent(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.absolute().parts[1:-1]:
        current /= part
        if current.exists() and _is_link_or_reparse(current):
            raise ValueError(f"{label} contains a symlink/reparse path component")


def _atomic_write(path: Path, payload: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load_checkpoint(path: Path) -> dict[str, Any]:
    path = _regular_file(path, "checkpoint")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid migration checkpoint: {exc}") from exc
    if state.get("version") != 1 or not isinstance(state.get("plan"), dict):
        raise ValueError("unsupported migration checkpoint")
    return state


def _save_checkpoint(path: Path, state: dict[str, Any]) -> None:
    _atomic_write(
        path,
        (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )


def _exclusive_sqlite_probe(path: Path, *, checkpoint_wal: bool = False) -> None:
    """Prove exclusivity and checkpoint a clean WAL before byte promotion.

    A stopped SQLite database may legitimately leave WAL/SHM files behind.
    Their mere existence is not proof that a writer is alive.  The exclusive
    transaction is the actual stop gate; once obtained, a TRUNCATE checkpoint
    folds committed WAL bytes into the database before its SHA is trusted.
    """
    wal = Path(f"{path}-wal")
    if wal.exists() and wal.stat().st_size:
        header = wal.read_bytes()[:4]
        if len(header) != 4 or int.from_bytes(header, "big") not in {
            0x377F0682,
            0x377F0683,
        }:
            raise RuntimeError("MachineSoul WAL is corrupt; stop SOUL Platform first")
    connection = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        connection.execute("ROLLBACK")
        if checkpoint_wal:
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint and int(checkpoint[0]) != 0:
                raise RuntimeError("MachineSoul WAL checkpoint is busy")
    except sqlite3.OperationalError as exc:
        raise RuntimeError("MachineSoul database is busy; stop SOUL Platform first") from exc
    finally:
        connection.close()


def _verify_sqlite_candidate(path: Path, expected_rows: dict[str, Any]) -> None:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA quick_check").fetchall()
        if result != [("ok",)]:
            raise ValueError(f"candidate SQLite quick_check failed: {result}")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "memories" not in tables:
            raise ValueError("candidate memories schema is missing")
        for table in ("memories", "procedural_memories"):
            expected = int(expected_rows.get(table, 0))
            if table not in tables:
                if expected:
                    raise ValueError(f"candidate {table} schema is missing")
                continue
            columns = {
                str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
            }
            if not {"id", "embedding"}.issubset(columns):
                raise ValueError(f"candidate {table} schema lacks id/embedding")
            migrated, mixed = connection.execute(
                f'SELECT COUNT(*), SUM(CASE WHEN length(embedding) != 4096 THEN 1 ELSE 0 END) '
                f'FROM "{table}" WHERE embedding IS NOT NULL'
            ).fetchone()
            if int(migrated) != expected or int(mixed or 0) != 0:
                raise ValueError(
                    f"candidate {table} embedding gate failed: "
                    f"rows={migrated}/{expected}, non_1024={int(mixed or 0)}"
                )
    finally:
        connection.close()


def _probe_bge() -> None:
    """Require a live local BGE-M3 endpoint returning finite 1024-d output."""
    provider = BgeM3Embedding(dimensions=1024)
    try:
        vector = asyncio.run(provider.embed("SOUL BGE-M3 cutover readiness probe"))
    except Exception as exc:
        raise RuntimeError("local BGE-M3 readiness probe failed") from exc
    if len(vector) != 1024 or not all(math.isfinite(float(value)) for value in vector):
        raise RuntimeError("local BGE-M3 readiness probe returned invalid dimensions")


def _target_config(previous: bytes) -> bytes:
    text = previous.decode("utf-8")
    replacement = f"{EMBEDDING_BLOCK.rstrip()}\n\n"
    pattern = re.compile(
        r"(?ms)^[ \t]*\[embedding\][ \t]*\r?\n.*?"
        r"(?=^[ \t]*\[[^\]]+\][ \t]*\r?$|\Z)"
    )
    if pattern.search(text):
        text = pattern.sub(replacement, text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text = f"{text}\n{replacement}"
    return text.encode("utf-8")


async def prepare(
    source: Path,
    *,
    candidate: Path,
    checkpoint: Path,
    batch_size: int = 256,
    resume: bool = False,
    max_batches: int | None = None,
) -> dict[str, Any]:
    """Build/resume a separate 1024-d candidate from a stable source snapshot."""
    source = _regular_file(source, "source")
    # Consolidate a valid crash-left WAL before Core fingerprints the source.
    # This changes SQLite's byte representation, never its logical rows, and
    # makes the later byte-exact backup/checkpoint contract meaningful.
    _exclusive_sqlite_probe(source, checkpoint_wal=True)
    provider = BgeM3Embedding(dimensions=1024)
    return await migrate_sqlite_embeddings(
        source,
        provider,
        candidate=candidate,
        checkpoint=checkpoint,
        source_dimensions=128,
        target_dimensions=1024,
        batch_size=batch_size,
        resume=resume,
        max_batches=max_batches,
        provider_name="bge-m3:bge-m3",
    )


def verify_candidate(checkpoint: Path) -> dict[str, str]:
    """Bind the completed checkpoint to the still-live source and candidate bytes."""
    state = _load_checkpoint(checkpoint)
    if state.get("status") != "completed":
        raise ValueError("migration is not completed")
    plan = state["plan"]
    source = _regular_file(Path(plan["source"]), "source")
    candidate = _regular_file(Path(plan["candidate"]), "candidate")
    source_sha = _sha256(source)
    candidate_sha = _sha256(candidate)
    if source_sha != plan.get("source_sha256"):
        raise ValueError("source changed after migration")
    if candidate_sha != state.get("candidate_sha256"):
        raise ValueError("candidate bytes do not match checkpoint")
    if plan.get("source_dimensions") != 128 or plan.get("target_dimensions") != 1024:
        raise ValueError("checkpoint is not a 128-to-1024 migration")
    _verify_sqlite_candidate(candidate, plan.get("rows") or {})
    return {"source_sha256": source_sha, "candidate_sha256": candidate_sha}


def activate(config: Path, checkpoint: Path) -> dict[str, Any]:
    """Promote the verified candidate while retaining an exact rollback path."""
    config = _regular_file(config, "config")
    checkpoint = _regular_file(checkpoint, "checkpoint")
    hashes = verify_candidate(checkpoint)
    state = _load_checkpoint(checkpoint)
    plan = state["plan"]
    source = _regular_file(Path(plan["source"]), "source")
    candidate = _regular_file(Path(plan["candidate"]), "candidate")
    if source.parent != candidate.parent or source.parent != config.parent:
        raise ValueError("config, source and candidate must share one directory")
    prior_activation = state.get("activation") or {}
    if prior_activation.get("status") == "activation-failed-rolled-back":
        state.pop("activation", None)
    elif prior_activation:
        raise ValueError("checkpoint already contains an activation record")
    # Re-checkpoint under an exclusive lock immediately before activation.
    # If a stopped process left newly committed WAL bytes after ``prepare``,
    # the checkpoint changes the main DB hash and the byte-bound gate below
    # rejects activation instead of silently dropping those rows.
    _exclusive_sqlite_probe(source, checkpoint_wal=True)
    if _sha256(source) != hashes["source_sha256"]:
        raise RuntimeError("source changed after final WAL checkpoint")
    _probe_bge()

    previous_config = config.read_bytes()
    target_config = _target_config(previous_config)
    stamp = _timestamp()
    source_backup = source.with_name(f"{source.name}.pre-bge-{stamp}.backup")
    config_backup = config.with_name(f"{config.name}.pre-bge-{stamp}.backup")
    _safe_parent(source_backup, "source backup")
    _safe_parent(config_backup, "config backup")
    if source_backup.exists() or config_backup.exists():
        raise FileExistsError("cutover backup path already exists")

    shutil.copy2(config, config_backup)
    if os.name != "nt":
        os.chmod(config_backup, 0o600)
    state["activation"] = {
        "status": "activating",
        "started_at": datetime.now(UTC).isoformat(),
        "source_backup": str(source_backup),
        "config_backup": str(config_backup),
        "source_sha256": hashes["source_sha256"],
        "candidate_sha256": hashes["candidate_sha256"],
        "previous_config_sha256": hashlib.sha256(previous_config).hexdigest(),
        "target_config_sha256": hashlib.sha256(target_config).hexdigest(),
    }
    _save_checkpoint(checkpoint, state)

    source_moved = False
    candidate_moved = False
    try:
        if _sha256(source) != hashes["source_sha256"]:
            raise RuntimeError("source changed immediately before activation")
        os.replace(source, source_backup)
        source_moved = True
        if _is_link_or_reparse(source_backup) or _sha256(source_backup) != hashes[
            "source_sha256"
        ]:
            raise RuntimeError("source backup changed across activation move")
        os.replace(candidate, source)
        candidate_moved = True
        if _is_link_or_reparse(source) or _sha256(source) != hashes["candidate_sha256"]:
            raise RuntimeError("candidate changed across activation move")
        _atomic_write(config, target_config)
        if _sha256(source) != hashes["candidate_sha256"]:
            raise RuntimeError("active database hash differs from candidate")
        loaded = ProxySettings.from_toml(config)
        if loaded.soul_db.resolve() != source:
            raise RuntimeError("activated config points to another database")
        activated_profile = (
            loaded.embedding_provider,
            loaded.embedding_dimensions,
            loaded.embedding_model,
            loaded.memory_vector_index,
        )
        if activated_profile != ("bge-m3", 1024, "bge-m3", "auto"):
            raise RuntimeError("activated config is not BGE-M3/1024/auto")
        state["activation"].update(
            {"status": "active", "activated_at": datetime.now(UTC).isoformat()}
        )
        _save_checkpoint(checkpoint, state)
        return state
    except BaseException:
        _atomic_write(config, previous_config)
        if candidate_moved and source.exists() and not candidate.exists():
            os.replace(source, candidate)
        if source_moved and source_backup.exists() and not source.exists():
            os.replace(source_backup, source)
        state["activation"]["status"] = "activation-failed-rolled-back"
        _save_checkpoint(checkpoint, state)
        raise


def rollback(config: Path, checkpoint: Path) -> dict[str, Any]:
    """Restore the exact pre-BGE database/config and retain the BGE candidate."""
    config = _regular_file(config, "config")
    checkpoint = _regular_file(checkpoint, "checkpoint")
    state = _load_checkpoint(checkpoint)
    activation = state.get("activation") or {}
    if activation.get("status") == "rolled-back":
        return state
    if activation.get("status") != "active":
        raise ValueError("checkpoint does not describe an active cutover")
    source = _regular_file(Path(state["plan"]["source"]), "active database")
    source_backup = _regular_file(Path(activation["source_backup"]), "source backup")
    config_backup = _regular_file(Path(activation["config_backup"]), "config backup")
    if _sha256(source) != activation["candidate_sha256"]:
        raise ValueError("active database changed; refusing byte-unsafe rollback")
    if _sha256(source_backup) != activation["source_sha256"]:
        raise ValueError("source backup changed; refusing rollback")
    if hashlib.sha256(config_backup.read_bytes()).hexdigest() != activation[
        "previous_config_sha256"
    ]:
        raise ValueError("config backup changed; refusing rollback")
    _exclusive_sqlite_probe(source)

    retained = source.with_name(f"{source.name}.bge-retained-{_timestamp()}.db")
    _safe_parent(retained, "retained candidate")
    if retained.exists():
        raise FileExistsError("rollback retention path already exists")
    os.replace(source, retained)
    try:
        os.replace(source_backup, source)
        _atomic_write(config, config_backup.read_bytes())
        if _sha256(source) != activation["source_sha256"]:
            raise RuntimeError("restored database hash differs from original")
    except BaseException:
        if source.exists() and not source_backup.exists():
            os.replace(source, source_backup)
        if retained.exists() and not source.exists():
            os.replace(retained, source)
        raise
    activation.update(
        {
            "status": "rolled-back",
            "rolled_back_at": datetime.now(UTC).isoformat(),
            "retained_candidate": str(retained),
        }
    )
    _save_checkpoint(checkpoint, state)
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="soul-machine-embedding-cutover")
    actions = parser.add_subparsers(dest="action", required=True)
    migrate = actions.add_parser("migrate")
    migrate.add_argument("source", type=Path)
    migrate.add_argument("--candidate", type=Path, required=True)
    migrate.add_argument("--checkpoint", type=Path, required=True)
    migrate.add_argument("--batch-size", type=int, default=256)
    migrate.add_argument("--resume", action="store_true")
    verify = actions.add_parser("verify")
    verify.add_argument("checkpoint", type=Path)
    activate_parser = actions.add_parser("activate")
    activate_parser.add_argument("config", type=Path)
    activate_parser.add_argument("checkpoint", type=Path)
    rollback_parser = actions.add_parser("rollback")
    rollback_parser.add_argument("config", type=Path)
    rollback_parser.add_argument("checkpoint", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "migrate":
            result = asyncio.run(
                prepare(
                    args.source,
                    candidate=args.candidate,
                    checkpoint=args.checkpoint,
                    batch_size=args.batch_size,
                    resume=args.resume,
                )
            )
        elif args.action == "verify":
            result = verify_candidate(args.checkpoint)
        elif args.action == "activate":
            result = activate(args.config, args.checkpoint)
        else:
            result = rollback(args.config, args.checkpoint)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
