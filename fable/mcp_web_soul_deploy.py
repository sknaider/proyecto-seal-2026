#!/usr/bin/python3
"""Primitivas fail-closed para staging y cutover de mcp-web-soul.

El código no despliega servicios: crea candidatos verificables. Los instaladores
root consumen únicamente el bundle root-owned que devuelve ``stage``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import pwd
import secrets
from pathlib import Path


class DeployError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _entries(manifest: Path) -> list[tuple[str, Path]]:
    entries: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for number, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not raw or raw.lstrip().startswith("#"):
            continue
        try:
            expected, name = raw.split(None, 1)
        except ValueError as exc:
            raise DeployError(f"manifest line {number} malformed") from exc
        relative = Path(name.strip())
        if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
            raise DeployError(f"manifest line {number} has invalid sha256")
        if relative.is_absolute() or ".." in relative.parts or relative in seen:
            raise DeployError(f"manifest line {number} has unsafe/duplicate path")
        seen.add(relative)
        entries.append((expected, relative))
    if not entries:
        raise DeployError("empty deployment manifest")
    return entries


def stage_bundle(
    source_root: str | Path,
    manifest_path: str | Path,
    expected_manifest_sha256: str,
    stage_root: str | Path,
) -> Path:
    """Copy a closed bundle with source re-hash before and after every copy."""

    source = Path(source_root).resolve()
    manifest = Path(manifest_path).resolve()
    stage_base = Path(stage_root).resolve()
    observed_manifest = sha256_file(manifest)
    if not expected_manifest_sha256 or observed_manifest != expected_manifest_sha256:
        raise DeployError("deployment manifest digest mismatch")
    entries = _entries(manifest)
    stage_base.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(stage_base, 0o700)
    final = stage_base / observed_manifest
    if final.exists():
        staged_manifest = final / ".seal-bundle-manifest.sha256"
        if not staged_manifest.is_file() or staged_manifest.is_symlink() or sha256_file(staged_manifest) != observed_manifest:
            raise DeployError("existing staged bundle lacks its exact manifest")
        for expected, relative in entries:
            target = final / relative
            if not target.is_file() or target.is_symlink() or sha256_file(target) != expected:
                raise DeployError("existing staged bundle failed revalidation")
            if os.geteuid() == 0 and (target.stat().st_uid != 0 or target.stat().st_mode & 0o022):
                raise DeployError("existing staged bundle is not root-owned/read-only")
        return final

    candidate = Path(tempfile.mkdtemp(prefix=".candidate-", dir=stage_base))
    try:
        for expected, relative in entries:
            src = source / relative
            try:
                before = src.lstat()
            except FileNotFoundError as exc:
                raise DeployError(f"bundle source missing: {relative}") from exc
            if not stat.S_ISREG(before.st_mode) or src.is_symlink():
                raise DeployError(f"bundle source is not a regular non-symlink: {relative}")
            if sha256_file(src) != expected:
                raise DeployError(f"bundle source digest mismatch: {relative}")
            dst = candidate / relative
            dst.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            source_fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                target_fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    while chunk := os.read(source_fd, 1024 * 1024):
                        remaining = memoryview(chunk)
                        while remaining:
                            written = os.write(target_fd, remaining)
                            if written <= 0:
                                raise DeployError(f"short bundle write: {relative}")
                            remaining = remaining[written:]
                    os.fsync(target_fd)
                finally:
                    os.close(target_fd)
            finally:
                os.close(source_fd)
            after = src.lstat()
            if (
                before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or sha256_file(src) != expected
                or sha256_file(dst) != expected
            ):
                raise DeployError(f"bundle source changed during copy: {relative}")
            os.chmod(dst, before.st_mode & 0o755)
            if os.geteuid() == 0:
                os.chown(dst, 0, 0)
        staged_manifest = candidate / ".seal-bundle-manifest.sha256"
        shutil.copyfile(manifest, staged_manifest, follow_symlinks=False)
        os.chmod(staged_manifest, 0o400)
        if os.geteuid() == 0:
            os.chown(staged_manifest, 0, 0)
        if sha256_file(staged_manifest) != observed_manifest:
            raise DeployError("staged manifest copy mismatch")
        for directory in sorted(
            (p for p in candidate.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
        ):
            _fsync_dir(directory)
            if os.geteuid() == 0:
                os.chown(directory, 0, 0)
        _fsync_dir(candidate)
        os.replace(candidate, final)
        _fsync_dir(stage_base)
        if os.geteuid() == 0:
            for path in (final, *final.rglob("*")):
                info = path.lstat()
                if info.st_uid != 0 or info.st_mode & 0o022:
                    raise DeployError("staged bundle is not root-owned/read-only")
        return final
    except Exception:
        shutil.rmtree(candidate, ignore_errors=True)
        raise


def verify_staged_bundle(
    stage_root: str | Path,
    expected_manifest_sha256: str,
    stage_base: str | Path = "/var/lib/seal-release-staging/mcp-web-soul",
) -> None:
    """Revalidate a canonical root-owned stage before every privileged phase."""

    root = Path(stage_root).resolve()
    base = Path(stage_base).resolve()
    if root.parent != base or root.name != expected_manifest_sha256:
        raise DeployError("staged root is not the canonical manifest-addressed path")
    for directory in (base, root):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or directory.is_symlink():
            raise DeployError("staged directory is unsafe")
        if os.geteuid() == 0 and (info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022):
            raise DeployError("staged directory is not root-owned and immutable")
    manifest = root / ".seal-bundle-manifest.sha256"
    if not manifest.is_file() or manifest.is_symlink() or sha256_file(manifest) != expected_manifest_sha256:
        raise DeployError("staged manifest identity mismatch")
    for expected, relative in _entries(manifest):
        target = root / relative
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or target.is_symlink() or sha256_file(target) != expected:
            raise DeployError(f"staged artifact mismatch: {relative}")
        if os.geteuid() == 0 and (info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022):
            raise DeployError(f"staged artifact is not root-owned/read-only: {relative}")


def _table_fingerprint(db: sqlite3.Connection, table: str) -> dict[str, object]:
    columns = [str(row[1]) for row in db.execute(f'PRAGMA table_info("{table}")')]
    digest = hashlib.sha256()
    count = 0
    order = ",".join('"' + column.replace('"', '""') + '"' for column in columns)
    query = f'SELECT * FROM "{table}"' + (f" ORDER BY {order}" if order else "")
    for row in db.execute(query):
        digest.update(json.dumps(row, default=str, separators=(",", ":")).encode())
        digest.update(b"\n")
        count += 1
    return {"columns": columns, "count": count, "sha256": digest.hexdigest()}


def sqlite_fingerprint(db: sqlite3.Connection) -> dict[str, dict[str, object]]:
    tables = [
        str(row[0])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name!='cutover_snapshot' ORDER BY name"
        )
    ]
    return {table: _table_fingerprint(db, table) for table in tables}


def open_process_references(
    paths: list[str | Path], proc_root: str | Path = "/proc",
) -> list[str]:
    """Return process FDs that still reference any protected inode."""

    protected: set[tuple[int, int]] = set()
    for raw in paths:
        path = Path(raw)
        if path.exists():
            info = path.stat()
            protected.add((info.st_dev, info.st_ino))
    found: list[str] = []
    proc = Path(proc_root)
    for pattern in ("[0-9]*/fd/*", "[0-9]*/map_files/*"):
        for reference in proc.glob(pattern):
            try:
                info = reference.stat()
            except (FileNotFoundError, PermissionError, OSError):
                continue
            if (info.st_dev, info.st_ino) in protected:
                found.append(str(reference))
    return sorted(found)


def assert_no_open_process_references(
    paths: list[str | Path], proc_root: str | Path = "/proc",
) -> None:
    found = open_process_references(paths, proc_root)
    if found:
        raise DeployError("frozen legacy input still has open process descriptors")


def write_private_cursor(path: str | Path, value: int, owner_user: str) -> None:
    """Atomically create a private cursor without following attacker symlinks."""

    target = Path(path)
    account = pwd.getpwnam(owner_user)
    parent = target.parent
    parent_info = parent.lstat()
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent.is_symlink()
        or parent_info.st_uid != account.pw_uid
        or parent_info.st_gid != account.pw_gid
        or stat.S_IMODE(parent_info.st_mode) != 0o700
    ):
        raise DeployError("cursor directory ownership or mode is unsafe")
    temporary = parent / f".{target.name}.{secrets.token_hex(16)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(temporary, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, account.pw_uid, account.pw_gid)
        os.write(fd, f"{int(value)}\n".encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, target)
    _fsync_dir(parent)


def copy_stable_file(source: str | Path, destination: str | Path) -> str:
    """Copy one regular file while detecting path swaps and in-place races."""

    src = Path(source)
    dst = Path(destination)
    before = src.lstat()
    if not stat.S_ISREG(before.st_mode) or src.is_symlink():
        raise DeployError("stable-copy source is not a regular non-symlink")
    src_fd = os.open(src, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    opened = os.fstat(src_fd)
    if (
        not stat.S_ISREG(opened.st_mode)
        or (before.st_dev, before.st_ino, before.st_uid, before.st_mode)
        != (opened.st_dev, opened.st_ino, opened.st_uid, opened.st_mode)
    ):
        os.close(src_fd)
        raise DeployError("stable-copy source changed before open")
    dst.parent.mkdir(parents=True, exist_ok=True)
    temporary = dst.parent / f".{dst.name}.{secrets.token_hex(16)}.tmp"
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    dst_fd = os.open(temporary, flags, 0o600)
    digest = hashlib.sha256()
    try:
        while chunk := os.read(src_fd, 1024 * 1024):
            digest.update(chunk)
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(dst_fd, remaining)
                if written <= 0:
                    raise DeployError("short write during stable copy")
                remaining = remaining[written:]
        os.fsync(dst_fd)
        after = os.fstat(src_fd)
        identity_before = (
            opened.st_dev, opened.st_ino, opened.st_size,
            opened.st_mtime_ns, opened.st_ctime_ns,
        )
        identity_after = (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        )
        if identity_before != identity_after:
            raise DeployError("stable-copy source changed during copy")
        os.lseek(src_fd, 0, os.SEEK_SET)
        source_verify = hashlib.sha256()
        while chunk := os.read(src_fd, 1024 * 1024):
            source_verify.update(chunk)
        os.lseek(dst_fd, 0, os.SEEK_SET)
        destination_verify = hashlib.sha256()
        while chunk := os.read(dst_fd, 1024 * 1024):
            destination_verify.update(chunk)
        if digest.digest() != source_verify.digest() or digest.digest() != destination_verify.digest():
            raise DeployError("stable-copy digest mismatch")
    except Exception:
        os.close(src_fd)
        os.close(dst_fd)
        temporary.unlink(missing_ok=True)
        raise
    os.close(src_fd)
    os.close(dst_fd)
    os.replace(temporary, dst)
    _fsync_dir(dst.parent)
    return digest.hexdigest()


def snapshot_control_plane(
    source_db: str | Path,
    target_db: str | Path,
    cursor_path: str | Path,
) -> dict[str, object]:
    """Create and atomically promote a verified SQLite snapshot.

    Callers must freeze both legacy writers first. Cursor and DB identity are
    recorded inside the promoted DB, so they cannot be torn across two files.
    """

    source_lexical = Path(source_db).expanduser()
    target_lexical = Path(target_db).expanduser()
    if source_lexical.is_symlink() or target_lexical.is_symlink():
        raise DeployError("control database path must not be a symlink")
    source_path = source_lexical.resolve()
    target_path = target_lexical.resolve()
    source_info = source_path.lstat()
    if not stat.S_ISREG(source_info.st_mode):
        raise DeployError("source control database is not a regular non-symlink")
    parent_info = target_path.parent.lstat()
    if os.geteuid() == 0 and (
        not stat.S_ISDIR(parent_info.st_mode)
        or target_path.parent.is_symlink()
        or parent_info.st_uid != 0
        or stat.S_IMODE(parent_info.st_mode) & 0o022
    ):
        raise DeployError("root snapshot target directory is not root-owned and closed")
    cursor = int(Path(cursor_path).read_text(encoding="utf-8").strip())
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        raise DeployError("target control database already exists")
    candidate = target_path.with_name(
        f".{target_path.name}.candidate-{secrets.token_hex(16)}"
    )
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    target = sqlite3.connect(candidate)
    try:
        if source.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise DeployError("source SQLite quick_check failed")
        source_fp = sqlite_fingerprint(source)
        source.backup(target)
        if source.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise DeployError("source SQLite changed or failed quick_check during backup")
        if sqlite_fingerprint(source) != source_fp:
            raise DeployError("source SQLite changed during backup")
        if sqlite_fingerprint(target) != source_fp:
            raise DeployError("candidate content differs from frozen source")
        transformations = {
            "sessions_closed": 0,
            "approvals_cancelled": 0,
            "executing_quarantined": 0,
        }
        session_columns = {
            str(row[1]) for row in target.execute("PRAGMA table_info(sessions)")
        }
        if {"state", "closed_at", "heartbeat_at"}.issubset(session_columns):
            now = __import__("time").time()
            transformations["sessions_closed"] = target.execute(
                "UPDATE sessions SET state='closed',closed_at=?,heartbeat_at=? "
                "WHERE state!='closed'",
                (now, now),
            ).rowcount
        approval_columns = {
            str(row[1]) for row in target.execute("PRAGMA table_info(approvals)")
        }
        if {"status", "error_code"}.issubset(approval_columns):
            executing = target.execute(
                "SELECT a.approval_id,a.session_id,a.action_hash,s.agent "
                "FROM approvals a JOIN sessions s ON s.session_id=a.session_id "
                "WHERE a.status='executing'"
            ).fetchall()
            if executing:
                remote_columns = {
                    str(row[1])
                    for row in target.execute("PRAGMA table_info(remote_effect_operations)")
                }
                required = {
                    "operation_id", "session_id", "action_hash", "tool",
                    "arguments_json", "status", "armed_at", "completed_at",
                    "error_code", "effect_scope", "effect_key",
                }
                if not required.issubset(remote_columns) or not {
                    "effect_scope", "effect_key", "executed_at"
                }.issubset(approval_columns):
                    raise DeployError(
                        "legacy executing approval cannot be quarantined by this schema"
                    )
                now = __import__("time").time()
                for approval_id, session_id, action_hash, agent in executing:
                    effect_key = hashlib.sha256(
                        f"legacy-cutover\0{approval_id}\0{action_hash}".encode()
                    ).hexdigest()
                    envelope = json.dumps(
                        {
                            "arguments": {"redacted": True},
                            "effect_key": effect_key,
                            "tool": "legacy_unknown",
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    existing = target.execute(
                        "SELECT status FROM remote_effect_operations WHERE operation_id=?",
                        (approval_id,),
                    ).fetchone()
                    if existing is None:
                        target.execute(
                            "INSERT INTO remote_effect_operations("
                            "operation_id,session_id,action_hash,tool,arguments_json,status,"
                            "armed_at,completed_at,error_code,effect_scope,effect_key"
                            ") VALUES(?,?,?,?,?,'indeterminate',?,?,?,?,?)",
                            (
                                approval_id, session_id, action_hash, "legacy_unknown",
                                envelope, now, now, "legacy_cutover_unknown_effect",
                                str(agent), effect_key,
                            ),
                        )
                    elif str(existing[0]) not in {"armed", "observed", "indeterminate"}:
                        raise DeployError(
                            "executing approval conflicts with completed remote effect"
                        )
                    else:
                        target.execute(
                            "UPDATE remote_effect_operations SET status='indeterminate',"
                            "completed_at=?,error_code=?,effect_scope=?,effect_key=?,"
                            "arguments_json=? WHERE operation_id=?",
                            (
                                now, "legacy_cutover_unknown_effect", str(agent),
                                effect_key, envelope, approval_id,
                            ),
                        )
                    target.execute(
                        "UPDATE approvals SET status='failed',executed_at=?,error_code=?,"
                        "effect_scope=?,effect_key=? WHERE approval_id=? AND status='executing'",
                        (
                            now, "indeterminate:legacy_cutover_unknown_effect",
                            str(agent), effect_key, approval_id,
                        ),
                    )
                    transformations["executing_quarantined"] += 1
            transformations["approvals_cancelled"] = target.execute(
                "UPDATE approvals SET status='cancelled',error_code='cutover_requires_reapproval' "
                "WHERE status IN ('requested','approved')"
            ).rowcount
        target.commit()
        promoted_fp = sqlite_fingerprint(target)
        target.execute(
            "CREATE TABLE cutover_snapshot (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        target.executemany(
            "INSERT INTO cutover_snapshot(key,value) VALUES(?,?)",
            (
                ("legacy_cursor", str(cursor)),
                ("source_path_sha256", hashlib.sha256(str(source_path).encode()).hexdigest()),
                ("source_fingerprint", json.dumps(source_fp, sort_keys=True)),
                ("promoted_fingerprint", json.dumps(promoted_fp, sort_keys=True)),
                ("cutover_transformations", json.dumps(transformations, sort_keys=True)),
            ),
        )
        target.commit()
        if target.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise DeployError("candidate SQLite quick_check failed")
        if sqlite_fingerprint(target) != promoted_fp:
            raise DeployError("candidate changed after verified cutover transformations")
        target.close()
        source.close()
        fd = os.open(candidate, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(candidate, target_path)
        _fsync_dir(target_path.parent)
        return {
            "cursor": cursor,
            "tables": source_fp,
            "promoted_tables": promoted_fp,
            "transformations": transformations,
        }
    except Exception:
        try:
            target.close()
        except sqlite3.Error:
            pass
        try:
            source.close()
        except sqlite3.Error:
            pass
        candidate.unlink(missing_ok=True)
        raise


def read_snapshot_cursor(path: str | Path) -> int:
    db = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    try:
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise DeployError("control snapshot quick_check failed")
        row = db.execute(
            "SELECT value FROM cutover_snapshot WHERE key='legacy_cursor'"
        ).fetchone()
        if row is None:
            raise DeployError("control snapshot has no coherent cursor")
        return int(row[0])
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage")
    stage.add_argument("--source-root", required=True)
    stage.add_argument("--manifest", required=True)
    stage.add_argument("--expected-manifest-sha256", required=True)
    stage.add_argument("--stage-root", default="/var/lib/seal-release-staging/mcp-web-soul")
    snapshot = sub.add_parser("snapshot-control")
    snapshot.add_argument("--source", required=True)
    snapshot.add_argument("--target", required=True)
    snapshot.add_argument("--cursor", required=True)
    read_cursor = sub.add_parser("read-snapshot-cursor")
    read_cursor.add_argument("--database", required=True)
    closed = sub.add_parser("assert-closed")
    closed.add_argument("--path", action="append", required=True)
    closed.add_argument("--proc-root", default="/proc")
    write_cursor = sub.add_parser("write-private-cursor")
    write_cursor.add_argument("--path", required=True)
    write_cursor.add_argument("--value", required=True, type=int)
    write_cursor.add_argument("--owner-user", required=True)
    stable_copy = sub.add_parser("copy-stable-file")
    stable_copy.add_argument("--source", required=True)
    stable_copy.add_argument("--destination", required=True)
    verify_stage = sub.add_parser("verify-stage")
    verify_stage.add_argument("--stage-root", required=True)
    verify_stage.add_argument("--expected-manifest-sha256", required=True)
    verify_stage.add_argument("--stage-base", default="/var/lib/seal-release-staging/mcp-web-soul")
    args = parser.parse_args()
    if args.command == "stage":
        print(stage_bundle(
            args.source_root, args.manifest, args.expected_manifest_sha256, args.stage_root,
        ))
    elif args.command == "snapshot-control":
        print(json.dumps(snapshot_control_plane(args.source, args.target, args.cursor), sort_keys=True))
    elif args.command == "read-snapshot-cursor":
        print(read_snapshot_cursor(args.database))
    elif args.command == "assert-closed":
        assert_no_open_process_references(args.path, args.proc_root)
        print("closed")
    elif args.command == "write-private-cursor":
        write_private_cursor(args.path, args.value, args.owner_user)
        print("written")
    elif args.command == "copy-stable-file":
        print(copy_stable_file(args.source, args.destination))
    else:
        verify_staged_bundle(args.stage_root, args.expected_manifest_sha256, args.stage_base)
        print("verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
