from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory.ssai_shadow.ledger import (
    GENESIS_HASH,
    LedgerIntegrityError,
    ShadowLedger,
    WitnessStore,
)


def _lines(path: Path) -> list[bytes]:
    return path.read_bytes().splitlines(keepends=True)


def test_append_and_verify_deterministic_hash_chain(tmp_path: Path) -> None:
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"
    first = ShadowLedger(first_path)
    second = ShadowLedger(second_path)

    event_one = {"type": "genesis", "agent": "ADA", "nested": {"z": 2, "a": 1}}
    event_two = {"type": "policy", "threshold": 2}
    first_records = [first.append(event_one), first.append(event_two)]
    second_records = [second.append(event_one), second.append(event_two)]

    result = first.verify()
    assert result.ok
    assert result.errors == ()
    assert result.sequence == 2
    assert result.head_hash == first_records[-1].event_hash
    assert result.hash_at(0) == GENESIS_HASH
    assert result.hash_at(1) == first_records[0].event_hash
    assert [record.event_hash for record in first_records] == [
        record.event_hash for record in second_records
    ]
    assert first_path.read_bytes().endswith(b"\n")


def test_snapshot_binds_records_and_head_to_one_immutable_read(tmp_path: Path) -> None:
    first = ShadowLedger(tmp_path / "first.jsonl")
    second = ShadowLedger(tmp_path / "second.jsonl")
    first.append({"type": "genesis", "agent": "ADA"})
    second.append({"type": "genesis", "agent": "CLONE"})

    snapshot = first.snapshot()
    first.path.write_bytes(second.path.read_bytes())

    assert snapshot.verification.ok
    assert snapshot.records[0].event["agent"] == "ADA"
    assert snapshot.verification.head_hash != first.verify().head_hash


def test_append_calls_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from memory.ssai_shadow import ledger as ledger_module

    observed: list[int] = []
    real_fsync = ledger_module.os.fsync

    def recording_fsync(fd: int) -> None:
        observed.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(ledger_module.os, "fsync", recording_fsync)
    ShadowLedger(tmp_path / "ledger.jsonl").append({"type": "genesis"})
    assert observed


@pytest.mark.parametrize("bad_event", [{"bad": float("nan")}, {"bad": object()}])
def test_invalid_event_is_rejected_before_file_creation(
    tmp_path: Path, bad_event: dict[str, object]
) -> None:
    path = tmp_path / "ledger.jsonl"
    with pytest.raises(ValueError, match="canonical JSON"):
        ShadowLedger(path).append(bad_event)
    assert not path.exists()


def test_modified_payload_is_detected_and_append_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = ShadowLedger(path)
    ledger.append({"type": "genesis", "owner": "William"})

    record = json.loads(path.read_text(encoding="utf-8"))
    record["event"]["owner"] = "attacker"
    path.write_text(json.dumps(record, separators=(",", ":")) + "\n", encoding="utf-8")

    result = ledger.verify()
    assert not result.ok
    assert "event_digest mismatch" in result.errors[0]
    with pytest.raises(LedgerIntegrityError, match="refusing append"):
        ledger.append({"type": "next"})


def test_reordered_records_are_detected(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = ShadowLedger(path)
    ledger.append({"type": "one"})
    ledger.append({"type": "two"})
    lines = _lines(path)
    path.write_bytes(lines[1] + lines[0])

    result = ledger.verify()
    assert not result.ok
    assert "expected strict sequence 1" in result.errors[0]


def test_replayed_record_is_detected_by_strict_sequence(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = ShadowLedger(path)
    ledger.append({"type": "one"})
    ledger.append({"type": "two"})
    lines = _lines(path)
    path.write_bytes(b"".join(lines) + lines[0])

    result = ledger.verify()
    assert not result.ok
    assert "expected strict sequence 3" in result.errors[0]


def test_semantically_identical_event_cannot_be_appended_twice(tmp_path: Path) -> None:
    ledger = ShadowLedger(tmp_path / "ledger.jsonl")
    event = {"type": "manifest", "manifest_digest": "sha256:" + "a" * 64}
    ledger.append(event)
    with pytest.raises(LedgerIntegrityError, match="semantic replay"):
        ledger.append(event)
    assert ledger.verify().sequence == 1


def test_semantically_valid_but_non_jcs_record_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = ShadowLedger(path)
    ledger.append({"type": "genesis"})
    record = json.loads(path.read_text())
    reordered = {
        "sequence": record["sequence"],
        "prev_hash": record["prev_hash"],
        "event": record["event"],
        "event_digest": record["event_digest"],
        "event_hash": record["event_hash"],
    }
    path.write_text(json.dumps(reordered, separators=(",", ":")) + "\n")
    result = ledger.verify()
    assert not result.ok
    assert "not encoded as JCS" in result.errors[0]


def test_crlf_is_rejected_when_contract_requires_jcs_plus_lf(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = ShadowLedger(path)
    ledger.append({"type": "genesis"})
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    result = ledger.verify()
    assert not result.ok
    assert "not encoded as JCS" in result.errors[0]


def test_sequence_and_prev_hash_are_strictly_validated(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = ShadowLedger(path)
    ledger.append({"type": "genesis"})
    ledger.append({"type": "next"})
    records = [json.loads(line) for line in path.read_text().splitlines()]

    records[1]["sequence"] = 3
    path.write_text(
        "\n".join(json.dumps(record, separators=(",", ":")) for record in records)
        + "\n",
        encoding="utf-8",
    )
    sequence_result = ledger.verify()
    assert not sequence_result.ok
    assert "expected strict sequence 2" in sequence_result.errors[0]

    records[1]["sequence"] = 2
    records[1]["prev_hash"] = "f" * 64
    path.write_text(
        "\n".join(json.dumps(record, separators=(",", ":")) for record in records)
        + "\n",
        encoding="utf-8",
    )
    prev_result = ledger.verify()
    assert not prev_result.ok
    assert "prev_hash mismatch" in prev_result.errors[0]


def test_truncated_final_line_and_blank_line_are_detected(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = ShadowLedger(path)
    ledger.append({"type": "genesis"})
    raw = path.read_bytes()

    path.write_bytes(raw[:-1])
    truncated = ledger.verify()
    assert not truncated.ok
    assert "truncated JSONL" in truncated.errors[0]

    path.write_bytes(raw + b"\n")
    blank = ledger.verify()
    assert not blank.ok
    assert "blank JSONL" in blank.errors[0]


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(
        (
            '{"event":{},"event":{},"event_hash":"'
            + "a" * 64
            + '","prev_hash":"'
            + GENESIS_HASH
            + '","sequence":1}\n'
        ).encode("utf-8")
    )
    result = ShadowLedger(path).verify()
    assert not result.ok
    assert "duplicate JSON key" in result.errors[0]


def test_witness_records_and_verifies_checkpoint_atomically(tmp_path: Path) -> None:
    ledger = ShadowLedger(tmp_path / "ledger.jsonl")
    witness_path = tmp_path / "external" / "witness.json"
    witness = WitnessStore(witness_path)
    ledger.append({"type": "genesis"})
    verified = ledger.verify()

    checkpoint = witness.record(verified.sequence, verified.head_hash)
    witnessed = witness.verify(verified)

    assert checkpoint.sequence == 1
    assert witnessed.ok
    assert witnessed.sequence == verified.sequence
    assert witnessed.head_hash == verified.head_hash
    assert witness_path.read_bytes().endswith(b"\n")
    assert not list(witness_path.parent.glob(f".{witness_path.name}.*.tmp"))


def test_stale_witness_accepts_valid_ledger_extension(tmp_path: Path) -> None:
    ledger = ShadowLedger(tmp_path / "ledger.jsonl")
    witness = WitnessStore(tmp_path / "witness.json")
    ledger.append({"type": "genesis"})
    initial = ledger.verify()
    witness.record(initial.sequence, initial.head_hash)
    ledger.append({"type": "extension"})

    result = witness.verify(ledger.verify())
    assert result.ok
    assert result.sequence == 1
    assert result.ledger_sequence == 2


def test_witness_detects_complete_line_rollback(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = ShadowLedger(path)
    witness = WitnessStore(tmp_path / "witness.json")
    ledger.append({"type": "genesis"})
    ledger.append({"type": "policy"})
    witness.record(2, ledger.verify().head_hash)

    path.write_bytes(_lines(path)[0])
    rolled_back = ledger.verify()
    assert rolled_back.ok
    result = witness.verify(rolled_back)
    assert not result.ok
    assert "rollback detected" in result.errors[0]


def test_witness_detects_rewritten_history(tmp_path: Path) -> None:
    original = ShadowLedger(tmp_path / "original.jsonl")
    original.append({"type": "genesis", "agent": "ADA"})
    original.append({"type": "policy", "threshold": 2})
    original_result = original.verify()
    witness = WitnessStore(tmp_path / "witness.json")
    witness.record(original_result.sequence, original_result.head_hash)

    rewritten = ShadowLedger(tmp_path / "rewritten.jsonl")
    rewritten.append({"type": "genesis", "agent": "CLONE"})
    rewritten.append({"type": "policy", "threshold": 2})
    rewritten_result = rewritten.verify()
    assert rewritten_result.ok

    result = witness.verify(rewritten_result)
    assert not result.ok
    assert "history fork detected" in result.errors[0]


def test_witness_is_monotonic_and_same_sequence_is_idempotent(tmp_path: Path) -> None:
    ledger = ShadowLedger(tmp_path / "ledger.jsonl")
    witness = WitnessStore(tmp_path / "witness.json")
    ledger.append({"type": "one"})
    first = ledger.verify()
    checkpoint = witness.record(first.sequence, first.head_hash)
    assert witness.record(first.sequence, first.head_hash) == checkpoint

    with pytest.raises(LedgerIntegrityError, match="conflicting witness"):
        witness.record(first.sequence, "a" * 64)

    ledger.append({"type": "two"})
    second = ledger.verify()
    witness.record(second.sequence, second.head_hash)
    with pytest.raises(LedgerIntegrityError, match="witness rollback"):
        witness.record(first.sequence, first.head_hash)


def test_concurrent_witness_writers_cannot_regress_checkpoint(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    ledger = ShadowLedger(tmp_path / "ledger.jsonl")
    witness = WitnessStore(tmp_path / "witness.json")
    ledger.append({"type": "one"})
    first = ledger.verify()
    ledger.append({"type": "two"})
    second = ledger.verify()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(witness.record, first.sequence, first.head_hash),
            pool.submit(witness.record, second.sequence, second.head_hash),
        ]
        for future in futures:
            try:
                future.result()
            except LedgerIntegrityError:
                pass

    assert witness.read().sequence == second.sequence
    assert witness.read().head_hash == second.head_hash


@pytest.mark.parametrize(
    "payload",
    [b"", b"{}", b"{}\n{}\n", b'{"version":1,"sequence":0,"head_hash":"bad"}\n'],
)
def test_missing_or_malformed_witness_fails_closed(
    tmp_path: Path, payload: bytes
) -> None:
    ledger = ShadowLedger(tmp_path / "ledger.jsonl")
    ledger_result = ledger.verify()
    witness_path = tmp_path / "witness.json"
    witness = WitnessStore(witness_path)

    missing = witness.verify(ledger_result)
    assert not missing.ok

    witness_path.write_bytes(payload)
    malformed = witness.verify(ledger_result)
    assert not malformed.ok


def test_witness_rejects_failed_ledger_verification(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = ShadowLedger(path)
    witness = WitnessStore(tmp_path / "witness.json")
    ledger.append({"type": "genesis"})
    valid = ledger.verify()
    witness.record(valid.sequence, valid.head_hash)
    path.write_bytes(path.read_bytes()[:-1])

    result = witness.verify(ledger.verify())
    assert not result.ok
    assert "ledger verification failed" in result.errors[0]
