#!/usr/bin/env python3
"""Witness monotónico externo para el audit de mcp-web-soul.

El servicio corre bajo un UID dedicado y conserva fuera del working tree el
último ``(sequence, head_hash)`` de cada stream. Un proceso con el UID compartido
puede reescribir su JSONL local, pero no puede hacer retroceder este witness.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
import grp
import pwd
import re
import socket
import socketserver
import sqlite3
import struct
import threading
from pathlib import Path


ZERO_HASH = "0" * 64
MAX_REQUEST = 16 * 1024
HASH_RE = re.compile(r"[0-9a-f]{64}")
STREAM_RE = re.compile(r"[A-Za-z0-9_.:-]{1,160}")


class WitnessError(RuntimeError):
    pass


class WitnessStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None, check_same_thread=False)
        os.chmod(self.path, 0o600)
        self._lock = threading.RLock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS heads ("
            "stream TEXT PRIMARY KEY, sequence INTEGER NOT NULL, "
            "previous_hash TEXT NOT NULL, head_hash TEXT NOT NULL, "
            "owner_uid INTEGER)"
        )
        columns = {
            str(row[1]) for row in self.db.execute("PRAGMA table_info(heads)").fetchall()
        }
        if "owner_uid" not in columns:
            self.db.execute("ALTER TABLE heads ADD COLUMN owner_uid INTEGER")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "stream TEXT NOT NULL, sequence INTEGER NOT NULL, event_hash TEXT NOT NULL, "
            "record_json TEXT NOT NULL, PRIMARY KEY(stream, sequence))"
        )

    @staticmethod
    def _validate(stream: str, sequence: int, previous_hash: str, head_hash: str) -> None:
        if not STREAM_RE.fullmatch(stream):
            raise WitnessError("invalid stream")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise WitnessError("invalid sequence")
        if not HASH_RE.fullmatch(previous_hash) or not HASH_RE.fullmatch(head_hash):
            raise WitnessError("invalid hash")

    def append(
        self, stream: str, sequence: int, previous_hash: str, head_hash: str,
        record: dict | None = None, *, owner_uid: int | None = None,
    ) -> dict:
        self._validate(stream, sequence, previous_hash, head_hash)
        if owner_uid is None:
            owner_uid = os.getuid()
        record_json = ""
        if record is not None:
            if not isinstance(record, dict) or record.get("event_hash") != head_hash:
                raise WitnessError("record/head mismatch")
            unhashed = dict(record)
            unhashed.pop("event_hash", None)
            canonical = json.dumps(
                unhashed, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            if not hmac.compare_digest(hashlib.sha256(canonical).hexdigest(), head_hash):
                raise WitnessError("record hash invalid")
            if record.get("previous_hash") != previous_hash:
                raise WitnessError("record previous hash mismatch")
            record_json = json.dumps(record, ensure_ascii=False, sort_keys=True)
        # sqlite3 permite compartir la conexión si check_same_thread=False, pero no
        # serializa transacciones. Sin este lock, dos clientes simultáneos ejecutaban
        # BEGIN sobre la misma conexión y el witness perdía requests.
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT sequence, previous_hash, head_hash, owner_uid FROM heads WHERE stream=?", (stream,),
                ).fetchone()
                if row is None:
                    if sequence != 1 or previous_hash != ZERO_HASH:
                        raise WitnessError("first event must start at sequence 1 and ZERO_HASH")
                    self.db.execute(
                        "INSERT INTO heads(stream, sequence, previous_hash, head_hash, owner_uid) "
                        "VALUES(?,?,?,?,?)",
                        (stream, sequence, previous_hash, head_hash, owner_uid),
                    )
                    if record_json:
                        self.db.execute(
                            "INSERT INTO events(stream, sequence, event_hash, record_json) VALUES(?,?,?,?)",
                            (stream, sequence, head_hash, record_json),
                        )
                elif row[3] is None:
                    raise WitnessError("stream owner is unassigned; offline migration required")
                elif owner_uid is None or int(row[3]) != int(owner_uid):
                    raise WitnessError("stream owned by another uid")
                elif sequence == row[0] and hmac.compare_digest(head_hash, row[2]):
                    if not hmac.compare_digest(previous_hash, row[1]):
                        raise WitnessError("idempotent event has conflicting previous hash")
                elif sequence != row[0] + 1 or not hmac.compare_digest(previous_hash, row[2]):
                    raise WitnessError("rollback, fork, or sequence gap denied")
                else:
                    self.db.execute(
                        "UPDATE heads SET sequence=?, previous_hash=?, head_hash=? WHERE stream=?",
                        (sequence, previous_hash, head_hash, stream),
                    )
                    if record_json:
                        self.db.execute(
                            "INSERT INTO events(stream, sequence, event_hash, record_json) VALUES(?,?,?,?)",
                            (stream, sequence, head_hash, record_json),
                        )
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        return {"ok": True, "stream": stream, "sequence": sequence, "head_hash": head_hash}

    def verify(
        self, stream: str, sequence: int, head_hash: str, *, owner_uid: int | None = None,
    ) -> dict:
        self._validate(stream, sequence, ZERO_HASH, head_hash)
        with self._lock:
            row = self.db.execute(
                "SELECT sequence, head_hash, owner_uid FROM heads WHERE stream=?", (stream,),
            ).fetchone()
        if row is None:
            ok = sequence == 0 and head_hash == ZERO_HASH
            witnessed_sequence, witnessed_head = 0, ZERO_HASH
        else:
            if row[2] is None:
                raise WitnessError("stream owner is unassigned; offline migration required")
            if owner_uid is not None and int(row[2]) != int(owner_uid):
                raise WitnessError("stream owned by another uid")
            witnessed_sequence, witnessed_head = int(row[0]), str(row[1])
            ok = sequence == witnessed_sequence and hmac.compare_digest(head_hash, witnessed_head)
        return {
            "ok": ok,
            "stream": stream,
            "sequence": witnessed_sequence,
            "head_hash": witnessed_head,
        }

    def head(self, stream: str, *, owner_uid: int | None = None) -> dict:
        if not STREAM_RE.fullmatch(stream):
            raise WitnessError("invalid stream")
        with self._lock:
            row = self.db.execute(
                "SELECT sequence, head_hash, owner_uid FROM heads WHERE stream=?", (stream,),
            ).fetchone()
        if row is not None:
            if row[2] is None:
                raise WitnessError("stream owner is unassigned; offline migration required")
            if owner_uid is not None and int(row[2]) != int(owner_uid):
                raise WitnessError("stream owned by another uid")
        sequence, head_hash = (0, ZERO_HASH) if row is None else (int(row[0]), str(row[1]))
        return {
            "ok": True,
            "stream": stream,
            "sequence": sequence,
            "head_hash": head_hash,
        }

    def event(self, stream: str, sequence: int, *, owner_uid: int | None = None) -> dict:
        if not STREAM_RE.fullmatch(stream):
            raise WitnessError("invalid stream")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise WitnessError("invalid sequence")
        with self._lock:
            owner = self.db.execute(
                "SELECT owner_uid FROM heads WHERE stream=?", (stream,),
            ).fetchone()
            if owner is not None:
                if owner[0] is None:
                    raise WitnessError("stream owner is unassigned; offline migration required")
                if owner_uid is not None and int(owner[0]) != int(owner_uid):
                    raise WitnessError("stream owned by another uid")
            row = self.db.execute(
                "SELECT event_hash, record_json FROM events WHERE stream=? AND sequence=?",
                (stream, sequence),
            ).fetchone()
        if row is None or not row[1]:
            raise WitnessError("witness event unavailable")
        record = json.loads(str(row[1]))
        if not isinstance(record, dict) or record.get("event_hash") != row[0]:
            raise WitnessError("stored witness event corrupt")
        return {
            "ok": True,
            "stream": stream,
            "sequence": sequence,
            "head_hash": str(row[0]),
            "record": record,
        }

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def bind_stream(self, stream: str, owner_uid: int) -> dict[str, int | str]:
        """Provision an exact stream owner offline before clients can connect."""

        if not STREAM_RE.fullmatch(stream):
            raise WitnessError("invalid stream")
        owner_uid = int(owner_uid)
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT sequence,previous_hash,head_hash,owner_uid FROM heads WHERE stream=?",
                    (stream,),
                ).fetchone()
                if row is None:
                    self.db.execute(
                        "INSERT INTO heads(stream,sequence,previous_hash,head_hash,owner_uid) "
                        "VALUES(?,?,?,?,?)",
                        (stream, 0, ZERO_HASH, ZERO_HASH, owner_uid),
                    )
                    row = (0, ZERO_HASH, ZERO_HASH, owner_uid)
                elif row[3] is None:
                    if int(row[0]) != 0 or row[1] != ZERO_HASH or row[2] != ZERO_HASH:
                        raise WitnessError("non-empty stream has no trustworthy owner")
                    self.db.execute(
                        "UPDATE heads SET owner_uid=? WHERE stream=? AND owner_uid IS NULL",
                        (owner_uid, stream),
                    )
                    row = (row[0], row[1], row[2], owner_uid)
                elif int(row[3]) != owner_uid:
                    raise WitnessError("bound stream is owned by another uid")
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        return {
            "stream": stream,
            "sequence": int(row[0]),
            "head_hash": str(row[2]),
            "owner_uid": owner_uid,
        }


def _validated_jsonl_handle(handle) -> tuple[str, int, str]:
    """Return ``(head, count, previous_to_head)`` for an open JSONL chain."""

    handle.seek(0)
    previous = ZERO_HASH
    previous_to_head = ZERO_HASH
    count = 0
    for raw in handle:
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WitnessError("legacy audit contains invalid JSON") from exc
        if not isinstance(record, dict):
            raise WitnessError("legacy audit record must be an object")
        unhashed = dict(record)
        event_hash = str(unhashed.pop("event_hash", ""))
        canonical = json.dumps(
            unhashed, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        if (
            unhashed.get("previous_hash") != previous
            or not HASH_RE.fullmatch(event_hash)
            or not hmac.compare_digest(hashlib.sha256(canonical).hexdigest(), event_hash)
        ):
            raise WitnessError("legacy audit chain invalid")
        previous_to_head = previous
        previous = event_hash
        count += 1
    return previous, count, previous_to_head


def seed_and_backfill_jsonl(
    store: WitnessStore,
    path: str | Path,
    *,
    owner_uid: int | None = None,
    stream_path: str | Path | None = None,
) -> dict[str, int | str]:
    """Backfill complete legacy records without ever replacing an existing head.

    A first install may establish a head only when the stream does not exist yet.
    Once present, the head is immutable here: the full local chain must end at the
    exact witnessed ``(sequence, previous_hash, head_hash)`` or the transaction is
    rolled back before any historical row is inserted.
    """

    source = Path(path).expanduser().resolve()
    if owner_uid is None:
        owner_uid = os.getuid()
    if not source.is_file():
        raise WitnessError(f"legacy audit missing: {source}")
    logical = Path(stream_path).expanduser().resolve() if stream_path is not None else source
    stream = "audit:" + hashlib.sha256(str(logical).encode("utf-8")).hexdigest()
    inserted = 0
    with source.open(encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        expected_head, expected_count, expected_previous = _validated_jsonl_handle(handle)
        with store._lock:
            store.db.execute("BEGIN IMMEDIATE")
            try:
                row = store.db.execute(
                    "SELECT sequence, previous_hash, head_hash, owner_uid FROM heads WHERE stream=?",
                    (stream,),
                ).fetchone()
                expected = (expected_count, expected_previous, expected_head)
                if row is None:
                    store.db.execute(
                        "INSERT INTO heads(stream,sequence,previous_hash,head_hash,owner_uid) "
                        "VALUES(?,?,?,?,?)",
                        (stream, *expected, owner_uid),
                    )
                elif tuple(row[:3]) != expected:
                    raise WitnessError("legacy audit conflicts with immutable witness head")
                elif row[3] is None:
                    store.db.execute(
                        "UPDATE heads SET owner_uid=? WHERE stream=? AND owner_uid IS NULL",
                        (owner_uid, stream),
                    )
                elif int(row[3]) != int(owner_uid):
                    raise WitnessError("legacy audit stream owned by another uid")

                handle.seek(0)
                sequence = 0
                for raw in handle:
                    if not raw.strip():
                        continue
                    sequence += 1
                    record = json.loads(raw)
                    event_hash = str(record["event_hash"])
                    record_json = json.dumps(record, ensure_ascii=False, sort_keys=True)
                    existing = store.db.execute(
                        "SELECT event_hash, record_json FROM events WHERE stream=? AND sequence=?",
                        (stream, sequence),
                    ).fetchone()
                    if existing is None:
                        store.db.execute(
                            "INSERT INTO events(stream,sequence,event_hash,record_json) VALUES(?,?,?,?)",
                            (stream, sequence, event_hash, record_json),
                        )
                        inserted += 1
                    elif (
                        not hmac.compare_digest(str(existing[0]), event_hash)
                        or json.loads(str(existing[1])) != record
                    ):
                        raise WitnessError("legacy event conflicts with stored witness record")
                # Detect in-place writers that ignored flock during the second pass.
                if _validated_jsonl_handle(handle) != (
                    expected_head, expected_count, expected_previous,
                ):
                    raise WitnessError("legacy audit changed during backfill")
                store.db.execute("COMMIT")
            except Exception:
                store.db.execute("ROLLBACK")
                raise
    return {
        "stream": stream,
        "sequence": expected_count,
        "head_hash": expected_head,
        "inserted": inserted,
    }


class _Handler(socketserver.StreamRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(2.0)
        peer_size = struct.calcsize("3i")
        try:
            peer = self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, peer_size)
            self.peer_pid, self.peer_uid, self.peer_gid = struct.unpack("3i", peer)
        except (AttributeError, OSError, struct.error):
            self.peer_pid, self.peer_uid, self.peer_gid = -1, -1, -1
        self.peer_authorized = self.peer_uid in self.server.allowed_uids  # type: ignore[attr-defined]

    def handle(self) -> None:
        # Authenticate from kernel-owned peer credentials before consuming even
        # one byte supplied by the client. JSON fields cannot forge this identity.
        if not self.peer_authorized:
            response = {"ok": False, "error": "unauthorized peer"}
            try:
                self.wfile.write(json.dumps(response, sort_keys=True).encode() + b"\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        try:
            raw = self.rfile.readline(MAX_REQUEST + 1)
            if not raw or len(raw) > MAX_REQUEST or not raw.endswith(b"\n"):
                raise WitnessError("invalid request framing")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise WitnessError("request must be an object")
            operation = request.get("op")
            if operation in {"append", "verify", "head", "event"}:
                stream = str(request.get("stream", ""))
                expected_uid = self.server.stream_owners.get(stream)  # type: ignore[attr-defined]
                if expected_uid is None or int(expected_uid) != int(self.peer_uid):
                    raise WitnessError("stream is not bound to this peer uid")
            if operation == "append":
                response = self.server.store.append(  # type: ignore[attr-defined]
                    request.get("stream", ""), request.get("sequence"),
                    request.get("previous_hash", ""), request.get("head_hash", ""),
                    request.get("record"), owner_uid=self.peer_uid,
                )
            elif operation == "verify":
                response = self.server.store.verify(  # type: ignore[attr-defined]
                    request.get("stream", ""), request.get("sequence"), request.get("head_hash", ""),
                    owner_uid=self.peer_uid,
                )
            elif operation == "head":
                response = self.server.store.head(  # type: ignore[attr-defined]
                    request.get("stream", ""), owner_uid=self.peer_uid,
                )
            elif operation == "event":
                response = self.server.store.event(  # type: ignore[attr-defined]
                    request.get("stream", ""), request.get("sequence"), owner_uid=self.peer_uid,
                )
            elif operation == "health":
                response = {"ok": True, "service": "mcp-web-soul-witness-v1"}
            else:
                raise WitnessError("unsupported operation")
        except (
            WitnessError, ValueError, TypeError, AttributeError,
            json.JSONDecodeError, sqlite3.Error, socket.timeout,
        ) as exc:
            response = {"ok": False, "error": str(exc)[:200]}
        try:
            self.wfile.write(json.dumps(response, sort_keys=True).encode() + b"\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    # El default de socketserver es 5 y provoca EAGAIN aun cuando SQLite está
    # correctamente serializado. El witness es un gate de auditoría: debe
    # absorber ráfagas de todos los agentes sin convertir carga legítima en
    # pérdida de eventos.
    request_queue_size = 128

    def __init__(
        self,
        socket_path: str | Path,
        store: WitnessStore,
        *,
        socket_group: str | None = None,
        allowed_uids: set[int] | frozenset[int] | None = None,
        stream_owners: dict[str, int] | None = None,
    ) -> None:
        self.store = store
        self.allowed_uids = frozenset({os.getuid()} if allowed_uids is None else allowed_uids)
        if not self.allowed_uids:
            raise WitnessError("at least one allowed peer uid is required")
        self.stream_owners = dict(stream_owners or {})
        if not self.stream_owners:
            raise WitnessError("at least one stream owner binding is required")
        if not set(self.stream_owners.values()).issubset(self.allowed_uids):
            raise WitnessError("stream owner is outside the peer allowlist")
        path = Path(socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        super().__init__(str(path), _Handler)
        if socket_group:
            gid = grp.getgrnam(socket_group).gr_gid
            os.chown(path.parent, -1, gid)
            os.chown(path, -1, gid)
            os.chmod(path.parent, 0o750)
        os.chmod(path, 0o660)


class WitnessClient:
    def __init__(self, socket_path: str | Path, *, timeout: float = 2.0) -> None:
        self.socket_path = str(socket_path)
        self.timeout = timeout

    def request(self, payload: dict) -> dict:
        wire = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        if len(wire) > MAX_REQUEST:
            raise WitnessError("request too large")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(self.timeout)
            client.connect(self.socket_path)
            client.sendall(wire)
            chunks = bytearray()
            while not chunks.endswith(b"\n"):
                part = client.recv(4096)
                if not part:
                    raise WitnessError("witness closed without response")
                chunks.extend(part)
                if len(chunks) > MAX_REQUEST:
                    raise WitnessError("witness response too large")
        response = json.loads(chunks)
        if not response.get("ok"):
            raise WitnessError(str(response.get("error") or "witness rejected request"))
        return response


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--socket-group")
    parser.add_argument("--allow-user", action="append", required=True)
    parser.add_argument("--bind-stream", action="append", required=True)
    args = parser.parse_args()
    try:
        allowed_uids = {pwd.getpwnam(name).pw_uid for name in args.allow_user}
    except KeyError as exc:
        raise SystemExit(f"unknown allowed user: {exc.args[0]}") from exc
    stream_owners: dict[str, int] = {}
    for binding in args.bind_stream:
        try:
            name, stream = binding.split("=", 1)
            uid = pwd.getpwnam(name).pw_uid
        except (ValueError, KeyError) as exc:
            raise SystemExit("invalid stream owner binding") from exc
        if not stream.startswith("audit:") or not HASH_RE.fullmatch(stream.removeprefix("audit:")):
            raise SystemExit("invalid bound stream")
        if stream in stream_owners and stream_owners[stream] != uid:
            raise SystemExit("duplicate stream owner binding")
        stream_owners[stream] = uid
    store = WitnessStore(args.db)
    try:
        with _Server(
            args.socket,
            store,
            socket_group=args.socket_group,
            allowed_uids=allowed_uids,
            stream_owners=stream_owners,
        ) as server:
            server.serve_forever()
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
