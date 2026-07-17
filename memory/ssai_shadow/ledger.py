"""Tamper-evident JSONL ledger for the isolated SSAI SHADOW prototype.

This module intentionally has no database, daemon, or private-key dependency.  A
ledger is a sequence of canonical JSON records linked by SHA-256 hashes.  The
separate witness file anchors a previously observed checkpoint so that a local
rollback or history rewrite is detectable.

The hash chain is tamper-evident, not an authorization mechanism.  Production
deployment still requires signed checkpoints and an independently protected
witness/transparency service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .canonical import canonicalize


GENESIS_HASH = "0" * 64
_RECORD_KEYS = frozenset(
    {"sequence", "prev_hash", "event", "event_digest", "event_hash"}
)
_WITNESS_KEYS = frozenset({"version", "sequence", "head_hash"})


class LedgerIntegrityError(RuntimeError):
    """Raised when an append/update would build on unverified state."""


def _canonical_bytes(value: Any) -> bytes:
    """Return the package's RFC 8785 / JCS canonical UTF-8 representation."""

    return canonicalize(value)


def _sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value)


def _json_object_no_duplicates(raw: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key!r}")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=reject_duplicates)
    if not isinstance(value, dict):
        raise ValueError("JSON value must be an object")
    return value


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    sequence: int
    prev_hash: str
    event: dict[str, Any]
    event_digest: str
    event_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "prev_hash": self.prev_hash,
            "event": self.event,
            "event_digest": self.event_digest,
            "event_hash": self.event_hash,
        }


@dataclass(frozen=True, slots=True)
class VerificationResult:
    ok: bool
    errors: tuple[str, ...]
    sequence: int
    head_hash: str
    _hashes: tuple[str, ...] = field(default=(), repr=False)
    _event_digests: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        return self.ok

    def hash_at(self, sequence: int) -> str:
        """Return the verified chain hash at ``sequence``.

        Sequence zero is the genesis anchor.  Calling this on a failed result
        or outside the verified prefix is an integrity error.
        """

        if not self.ok:
            raise LedgerIntegrityError("cannot query a failed verification")
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise TypeError("sequence must be an integer")
        if sequence < 0 or sequence > self.sequence:
            raise LedgerIntegrityError(
                f"sequence {sequence} is outside verified range 0..{self.sequence}"
            )
        return GENESIS_HASH if sequence == 0 else self._hashes[sequence - 1]


@dataclass(frozen=True, slots=True)
class WitnessCheckpoint:
    version: int
    sequence: int
    head_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "sequence": self.sequence,
            "head_hash": self.head_hash,
        }


@dataclass(frozen=True, slots=True)
class WitnessVerificationResult:
    ok: bool
    errors: tuple[str, ...]
    sequence: int
    head_hash: str
    ledger_sequence: int
    ledger_head_hash: str

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    """One immutable read used for both integrity and semantic verification."""

    verification: VerificationResult
    records: tuple[LedgerRecord, ...]


class ShadowLedger:
    """Durable append-only JSONL ledger with a strict SHA-256 hash chain."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    @staticmethod
    def _record_body(
        sequence: int,
        prev_hash: str,
        event: Mapping[str, Any],
        event_digest: str,
    ) -> dict[str, Any]:
        return {
            "sequence": sequence,
            "prev_hash": prev_hash,
            "event": dict(event),
            "event_digest": event_digest,
        }

    def append(self, event: Mapping[str, Any]) -> LedgerRecord:
        """Append one event after verifying the complete current ledger.

        A process-level advisory lock serializes local writers.  Data and file
        metadata are flushed with ``fsync`` before returning.
        """

        if not isinstance(event, Mapping):
            raise TypeError("event must be a mapping")

        # Validate the complete nested value before opening/mutating the file.
        try:
            _canonical_bytes(dict(event))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"event is not canonical JSON data: {exc}") from exc

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                current = self._verify_bytes(handle.read())
                if not current.ok:
                    raise LedgerIntegrityError(
                        "refusing append to invalid ledger: " + "; ".join(current.errors)
                    )

                sequence = current.sequence + 1
                prev_hash = current.head_hash
                event_digest = _sha256_hex(_canonical_bytes(dict(event)))
                if event_digest in current._event_digests:
                    raise LedgerIntegrityError(
                        "refusing semantic replay of an already recorded event"
                    )
                body = self._record_body(sequence, prev_hash, event, event_digest)
                event_hash = _sha256_hex(_canonical_bytes(body))
                record = LedgerRecord(
                    sequence=sequence,
                    prev_hash=prev_hash,
                    event=body["event"],
                    event_digest=event_digest,
                    event_hash=event_hash,
                )
                encoded = _canonical_bytes(record.as_dict()) + b"\n"

                handle.seek(0, os.SEEK_END)
                written = handle.write(encoded)
                if written != len(encoded):
                    raise OSError(
                        f"short ledger write: wrote {written} of {len(encoded)} bytes"
                    )
                handle.flush()
                os.fsync(handle.fileno())
                return record
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def verify(self) -> VerificationResult:
        """Verify the entire ledger and return the longest valid prefix."""

        return self.snapshot().verification

    def snapshot(self) -> LedgerSnapshot:
        """Read once under a shared lock, then verify and parse those exact bytes."""

        if not self.path.exists():
            result = VerificationResult(True, (), 0, GENESIS_HASH, ())
            return LedgerSnapshot(result, ())
        try:
            with self.path.open("rb") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
                try:
                    raw = handle.read()
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            result = VerificationResult(
                False, (f"cannot read ledger: {exc}",), 0, GENESIS_HASH, ()
            )
            return LedgerSnapshot(result, ())
        result = self._verify_bytes(raw)
        if not result.ok or not raw:
            return LedgerSnapshot(result, ())
        records = tuple(
            LedgerRecord(
                sequence=data["sequence"],
                prev_hash=data["prev_hash"],
                event=data["event"],
                event_digest=data["event_digest"],
                event_hash=data["event_hash"],
            )
            for data in (
                _json_object_no_duplicates(line) for line in raw.split(b"\n")[:-1]
            )
        )
        return LedgerSnapshot(result, records)

    def read_records(self) -> tuple[LedgerRecord, ...]:
        """Return records only after the complete ledger passes verification."""

        snapshot = self.snapshot()
        if not snapshot.verification.ok:
            raise LedgerIntegrityError(
                "cannot read invalid ledger: "
                + "; ".join(snapshot.verification.errors)
            )
        return snapshot.records

    @staticmethod
    def _verify_bytes(raw: bytes) -> VerificationResult:
        if not raw:
            return VerificationResult(True, (), 0, GENESIS_HASH, ())
        if not raw.endswith(b"\n"):
            return VerificationResult(
                False,
                ("truncated JSONL: final record is not newline-terminated",),
                0,
                GENESIS_HASH,
                (),
            )

        previous_hash = GENESIS_HASH
        verified_hashes: list[str] = []
        verified_event_digests: list[str] = []
        seen_record_hashes: set[str] = set()
        seen_event_digests: set[str] = set()

        for line_number, raw_line in enumerate(raw.split(b"\n")[:-1], start=1):
            if not raw_line:
                return VerificationResult(
                    False,
                    (f"line {line_number}: blank JSONL records are forbidden",),
                    len(verified_hashes),
                    previous_hash,
                    tuple(verified_hashes),
                )
            try:
                record = _json_object_no_duplicates(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                return VerificationResult(
                    False,
                    (f"line {line_number}: invalid JSON object: {exc}",),
                    len(verified_hashes),
                    previous_hash,
                    tuple(verified_hashes),
                )

            try:
                canonical_record = _canonical_bytes(record)
            except (TypeError, ValueError) as exc:
                return VerificationResult(
                    False,
                    (f"line {line_number}: record is not canonical JSON data: {exc}",),
                    len(verified_hashes),
                    previous_hash,
                    tuple(verified_hashes),
                )
            if raw_line != canonical_record:
                return VerificationResult(
                    False,
                    (f"line {line_number}: record is not encoded as JCS",),
                    len(verified_hashes),
                    previous_hash,
                    tuple(verified_hashes),
                )

            if frozenset(record) != _RECORD_KEYS:
                missing = sorted(_RECORD_KEYS.difference(record))
                extra = sorted(set(record).difference(_RECORD_KEYS))
                return VerificationResult(
                    False,
                    (
                        f"line {line_number}: invalid record keys "
                        f"(missing={missing}, extra={extra})",
                    ),
                    len(verified_hashes),
                    previous_hash,
                    tuple(verified_hashes),
                )

            expected_sequence = len(verified_hashes) + 1
            sequence = record["sequence"]
            if (
                isinstance(sequence, bool)
                or not isinstance(sequence, int)
                or sequence != expected_sequence
            ):
                return VerificationResult(
                    False,
                    (
                        f"line {line_number}: expected strict sequence "
                        f"{expected_sequence}, got {sequence!r}",
                    ),
                    len(verified_hashes),
                    previous_hash,
                    tuple(verified_hashes),
                )

            if record["prev_hash"] != previous_hash:
                return VerificationResult(
                    False,
                    (
                        f"line {line_number}: prev_hash mismatch "
                        f"(expected {previous_hash}, got {record['prev_hash']!r})",
                    ),
                    len(verified_hashes),
                    previous_hash,
                    tuple(verified_hashes),
                )

            if not isinstance(record["event"], dict):
                return VerificationResult(
                    False,
                    (f"line {line_number}: event must be a JSON object",),
                    len(verified_hashes),
                    previous_hash,
                    tuple(verified_hashes),
                )
            event_digest = record["event_digest"]
            if not _is_sha256(event_digest):
                return VerificationResult(
                    False,
                    (f"line {line_number}: event_digest is not lowercase SHA-256",),
                    len(verified_hashes),
                    previous_hash,
                    tuple(verified_hashes),
                )
            expected_event_digest = _sha256_hex(_canonical_bytes(record["event"]))
            if event_digest != expected_event_digest:
                return VerificationResult(
                    False,
                    (f"line {line_number}: event_digest mismatch",),
                    len(verified_hashes),
                    previous_hash,
                    tuple(verified_hashes),
                )
            if event_digest in seen_event_digests:
                return VerificationResult(
                    False,
                    (f"line {line_number}: semantic event replay detected",),
                    len(verified_hashes),
                    previous_hash,
                    tuple(verified_hashes),
                )
            event_hash = record["event_hash"]
            if not _is_sha256(event_hash):
                return VerificationResult(
                    False,
                    (f"line {line_number}: event_hash is not lowercase SHA-256",),
                    len(verified_hashes),
                    previous_hash,
                    tuple(verified_hashes),
                )

            body = {
                "sequence": sequence,
                "prev_hash": record["prev_hash"],
                "event": record["event"],
                "event_digest": event_digest,
            }
            try:
                expected_hash = _sha256_hex(_canonical_bytes(body))
            except (TypeError, ValueError) as exc:
                return VerificationResult(
                    False,
                    (f"line {line_number}: event is not canonical JSON data: {exc}",),
                    len(verified_hashes),
                    previous_hash,
                    tuple(verified_hashes),
                )
            if event_hash != expected_hash:
                return VerificationResult(
                    False,
                    (
                        f"line {line_number}: event_hash mismatch "
                        f"(expected {expected_hash}, got {event_hash})",
                    ),
                    len(verified_hashes),
                    previous_hash,
                    tuple(verified_hashes),
                )
            if event_hash in seen_record_hashes:
                return VerificationResult(
                    False,
                    (f"line {line_number}: replayed record hash {event_hash}",),
                    len(verified_hashes),
                    previous_hash,
                    tuple(verified_hashes),
                )

            seen_record_hashes.add(event_hash)
            seen_event_digests.add(event_digest)
            verified_hashes.append(event_hash)
            verified_event_digests.append(event_digest)
            previous_hash = event_hash

        return VerificationResult(
            True,
            (),
            len(verified_hashes),
            previous_hash,
            tuple(verified_hashes),
            tuple(verified_event_digests),
        )


class WitnessStore:
    """Atomic external checkpoint store for rollback/history-fork detection."""

    VERSION = 1

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def read(self) -> WitnessCheckpoint:
        """Load and strictly validate the checkpoint; fail closed on any issue."""

        try:
            raw = self.path.read_bytes()
        except FileNotFoundError as exc:
            raise LedgerIntegrityError(f"witness does not exist: {self.path}") from exc
        except OSError as exc:
            raise LedgerIntegrityError(f"cannot read witness: {exc}") from exc

        if not raw or not raw.endswith(b"\n") or raw.count(b"\n") != 1:
            raise LedgerIntegrityError(
                "witness must contain exactly one newline-terminated JSON object"
            )
        try:
            data = _json_object_no_duplicates(raw[:-1])
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise LedgerIntegrityError(f"invalid witness JSON: {exc}") from exc
        if frozenset(data) != _WITNESS_KEYS:
            raise LedgerIntegrityError("witness has missing or unexpected fields")
        if raw[:-1] != _canonical_bytes(data):
            raise LedgerIntegrityError("witness is not encoded as JCS")
        if data["version"] != self.VERSION or isinstance(data["version"], bool):
            raise LedgerIntegrityError(
                f"unsupported witness version: {data['version']!r}"
            )
        sequence = data["sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise LedgerIntegrityError("witness sequence must be a non-negative integer")
        head_hash = data["head_hash"]
        if not _is_sha256(head_hash):
            raise LedgerIntegrityError("witness head_hash is not lowercase SHA-256")
        if sequence == 0 and head_hash != GENESIS_HASH:
            raise LedgerIntegrityError("sequence-zero witness must use genesis hash")
        if sequence > 0 and head_hash == GENESIS_HASH:
            raise LedgerIntegrityError("non-empty witness cannot use genesis hash")
        return WitnessCheckpoint(self.VERSION, sequence, head_hash)

    def record(self, sequence: int, head_hash: str) -> WitnessCheckpoint:
        """Persist a monotonic checkpoint under an inter-process lock."""

        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("sequence must be a non-negative integer")
        if not _is_sha256(head_hash):
            raise ValueError("head_hash must be a lowercase SHA-256 hex digest")
        if sequence == 0 and head_hash != GENESIS_HASH:
            raise ValueError("sequence-zero checkpoint must use genesis hash")
        if sequence > 0 and head_hash == GENESIS_HASH:
            raise ValueError("non-empty checkpoint cannot use genesis hash")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        with lock_path.open("a+b") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                return self._record_locked(sequence, head_hash)
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _record_locked(self, sequence: int, head_hash: str) -> WitnessCheckpoint:
        checkpoint = WitnessCheckpoint(self.VERSION, sequence, head_hash)
        if self.path.exists():
            existing = self.read()
            if sequence < existing.sequence:
                raise LedgerIntegrityError(
                    f"refusing witness rollback {existing.sequence} -> {sequence}"
                )
            if sequence == existing.sequence:
                if head_hash != existing.head_hash:
                    raise LedgerIntegrityError(
                        "refusing conflicting witness at the same sequence"
                    )
                return existing

        payload = _canonical_bytes(checkpoint.as_dict()) + b"\n"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                written = handle.write(payload)
                if written != len(payload):
                    raise OSError(
                        f"short witness write: wrote {written} of {len(payload)} bytes"
                    )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
            directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
        return checkpoint

    def verify(self, ledger: VerificationResult) -> WitnessVerificationResult:
        """Verify the witnessed prefix against a complete ledger verification."""

        try:
            checkpoint = self.read()
        except LedgerIntegrityError as exc:
            return WitnessVerificationResult(
                False,
                (str(exc),),
                0,
                GENESIS_HASH,
                ledger.sequence,
                ledger.head_hash,
            )
        if not ledger.ok:
            return WitnessVerificationResult(
                False,
                ("ledger verification failed: " + "; ".join(ledger.errors),),
                checkpoint.sequence,
                checkpoint.head_hash,
                ledger.sequence,
                ledger.head_hash,
            )
        if ledger.sequence < checkpoint.sequence:
            return WitnessVerificationResult(
                False,
                (
                    f"ledger rollback detected: witness is at {checkpoint.sequence}, "
                    f"ledger ends at {ledger.sequence}",
                ),
                checkpoint.sequence,
                checkpoint.head_hash,
                ledger.sequence,
                ledger.head_hash,
            )
        try:
            observed_hash = ledger.hash_at(checkpoint.sequence)
        except LedgerIntegrityError as exc:
            return WitnessVerificationResult(
                False,
                (str(exc),),
                checkpoint.sequence,
                checkpoint.head_hash,
                ledger.sequence,
                ledger.head_hash,
            )
        if observed_hash != checkpoint.head_hash:
            return WitnessVerificationResult(
                False,
                (
                    f"history fork detected at sequence {checkpoint.sequence}: "
                    f"witness={checkpoint.head_hash}, ledger={observed_hash}",
                ),
                checkpoint.sequence,
                checkpoint.head_hash,
                ledger.sequence,
                ledger.head_hash,
            )
        return WitnessVerificationResult(
            True,
            (),
            checkpoint.sequence,
            checkpoint.head_hash,
            ledger.sequence,
            ledger.head_hash,
        )
