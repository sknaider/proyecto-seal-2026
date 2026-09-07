from __future__ import annotations

import concurrent.futures
import grp
import hashlib
import json
import os
import socket
import threading

import pytest

from mcp_web_soul_security import AuditTrail
from mcp_web_soul_witness import (
    ZERO_HASH,
    WitnessClient,
    WitnessError,
    WitnessStore,
    _Server,
    seed_and_backfill_jsonl,
)


def _event(previous_hash: str, *, tool: str = "probe") -> tuple[dict, str]:
    record = {"agent": "ADA", "previous_hash": previous_hash, "tool": tool}
    head = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    record["event_hash"] = head
    return record, head


@pytest.fixture()
def live_witness(tmp_path):
    store = WitnessStore(tmp_path / "witness.sqlite3")
    uid = os.getuid()
    stream_owners = {"audit:one": uid, "audit:parallel": uid}
    for name in ("audit.jsonl", "recover.jsonl"):
        path = (tmp_path / name).resolve()
        stream_owners["audit:" + hashlib.sha256(str(path).encode()).hexdigest()] = uid
    server = _Server(
        tmp_path / "witness.sock", store,
        allowed_uids={uid}, stream_owners=stream_owners,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield WitnessClient(tmp_path / "witness.sock"), store
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        store.close()


def test_monotonic_chain_accepts_idempotency_and_rejects_fork(live_witness):
    client, _ = live_witness
    first, head1 = _event(ZERO_HASH, tool="one")
    payload = {
        "op": "append", "stream": "audit:one", "sequence": 1,
        "previous_hash": ZERO_HASH, "head_hash": head1, "record": first,
    }
    assert client.request(payload)["sequence"] == 1
    assert client.request(payload)["sequence"] == 1

    fork, fork_head = _event(ZERO_HASH, tool="fork")
    with pytest.raises(WitnessError, match="rollback|fork|sequence"):
        client.request({
            "op": "append", "stream": "audit:one", "sequence": 2,
            "previous_hash": ZERO_HASH, "head_hash": fork_head, "record": fork,
        })
    assert client.request({
        "op": "verify", "stream": "audit:one", "sequence": 1, "head_hash": head1,
    })["ok"] is True


def test_concurrent_idempotent_appends_are_lossless(live_witness):
    client, _ = live_witness
    record, head = _event(ZERO_HASH)
    payload = {
        "op": "append", "stream": "audit:parallel", "sequence": 1,
        "previous_hash": ZERO_HASH, "head_hash": head, "record": record,
    }

    def append(_index: int) -> bool:
        return WitnessClient(client.socket_path).request(payload)["ok"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(append, range(50)))
    assert results == [True] * 50


def test_stream_owner_uid_is_persistent_and_cross_role_access_is_denied(tmp_path):
    db = tmp_path / "owned.sqlite3"
    first, head = _event(ZERO_HASH, tool="owned")
    store = WitnessStore(db)
    try:
        store.append(
            "audit:owned", 1, ZERO_HASH, head, first, owner_uid=41001,
        )
        assert store.db.execute(
            "SELECT owner_uid FROM heads WHERE stream='audit:owned'"
        ).fetchone() == (41001,)
        with pytest.raises(WitnessError, match="another uid"):
            store.head("audit:owned", owner_uid=41002)
        with pytest.raises(WitnessError, match="another uid"):
            store.verify("audit:owned", 1, head, owner_uid=41002)
        with pytest.raises(WitnessError, match="another uid"):
            store.event("audit:owned", 1, owner_uid=41002)
        with pytest.raises(WitnessError, match="another uid"):
            store.append(
                "audit:owned", 1, ZERO_HASH, head, first, owner_uid=41002,
            )
    finally:
        store.close()

    reopened = WitnessStore(db)
    try:
        assert reopened.head("audit:owned", owner_uid=41001)["head_hash"] == head
        with pytest.raises(WitnessError, match="another uid"):
            reopened.head("audit:owned", owner_uid=41002)
    finally:
        reopened.close()


def test_unowned_legacy_stream_is_fail_closed_until_offline_assignment(tmp_path):
    db = tmp_path / "legacy-owner.sqlite3"
    store = WitnessStore(db)
    try:
        store.db.execute(
            "INSERT INTO heads(stream,sequence,previous_hash,head_hash,owner_uid) "
            "VALUES(?,?,?,?,NULL)",
            ("audit:legacy-owner", 0, ZERO_HASH, ZERO_HASH),
        )
        with pytest.raises(WitnessError, match="offline migration"):
            store.head("audit:legacy-owner", owner_uid=41001)
    finally:
        store.close()


def test_invalid_json_and_non_object_fail_closed(live_witness):
    client, _ = live_witness
    for wire in (b"not-json\n", b"[]\n"):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
            raw.settimeout(2)
            raw.connect(client.socket_path)
            raw.sendall(wire)
            response = json.loads(raw.recv(4096))
        assert response["ok"] is False


def test_peer_credentials_are_denied_before_reading_request_body(tmp_path):
    store = WitnessStore(tmp_path / "denied.sqlite3")
    server = _Server(
        tmp_path / "denied.sock",
        store,
        allowed_uids={os.getuid() + 1},
        stream_owners={"audit:denied": os.getuid() + 1},
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
            raw.settimeout(0.5)
            raw.connect(str(tmp_path / "denied.sock"))
            # No request bytes are sent. A body-first implementation would time
            # out here; peer authentication must reject immediately.
            response = json.loads(raw.recv(4096))
        assert response == {"error": "unauthorized peer", "ok": False}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        store.close()


def test_allowed_peer_cannot_first_claim_stream_bound_to_other_uid(tmp_path):
    store = WitnessStore(tmp_path / "bound.sqlite3")
    uid = os.getuid()
    stream = "audit:" + "a" * 64
    server = _Server(
        tmp_path / "bound.sock", store,
        allowed_uids={uid, uid + 1}, stream_owners={stream: uid + 1},
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        record, head = _event(ZERO_HASH, tool="forged-first-claim")
        with pytest.raises(WitnessError, match="not bound"):
            WitnessClient(tmp_path / "bound.sock").request({
                "op": "append", "stream": stream, "sequence": 1,
                "previous_hash": ZERO_HASH, "head_hash": head, "record": record,
            })
        assert store.db.execute("SELECT count(*) FROM heads").fetchone() == (0,)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        store.close()


def test_store_files_are_private(tmp_path):
    store = WitnessStore(tmp_path / "private" / "witness.sqlite3")
    try:
        assert store.path.stat().st_mode & 0o077 == 0
        assert store.path.parent.stat().st_mode & 0o077 == 0
    finally:
        store.close()


def test_audit_can_publish_read_only_to_an_explicit_reader_group(tmp_path):
    reader_group = grp.getgrgid(os.getgid()).gr_name
    trail = AuditTrail(tmp_path / "shared" / "audit.jsonl", reader_group=reader_group)

    trail.append(tool="probe", arguments={}, ok=True, required=True)

    assert trail.path.stat().st_gid == os.getgid()
    assert trail.path.stat().st_mode & 0o777 == 0o640
    assert trail.path.parent.stat().st_mode & 0o777 == 0o750


def test_full_local_audit_deletion_is_detected_by_external_witness(live_witness, tmp_path):
    client, _ = live_witness
    audit_path = tmp_path / "audit.jsonl"
    trail = AuditTrail(
        audit_path,
        agent="ADA",
        witness_socket=client.socket_path,
        witness_required=True,
    )
    trail.append(tool="probe", arguments={"value": 1}, ok=True, required=True)
    assert trail.verify() == (True, 1)

    audit_path.unlink()

    # Borrar todo el archivo local no puede degradar la verificacion a
    # "cadena vacia sana" mientras el witness conserva una cabeza avanzada.
    assert trail.verify() == (False, 0)


def test_witness_ahead_record_is_recovered_before_next_append(live_witness, tmp_path):
    client, _ = live_witness
    audit_path = tmp_path / "recover.jsonl"
    trail = AuditTrail(
        audit_path,
        agent="ADA",
        witness_socket=client.socket_path,
        witness_required=True,
    )
    first = {
        "ts": "2026-08-14T00:00:00+00:00",
        "agent": "ADA",
        "session_id": "",
        "tool": "crash-window",
        "action_class": "READ",
        "arguments": {},
        "ok": True,
        "error": None,
        "previous_hash": trail.ZERO_HASH,
    }
    first["event_hash"] = trail._hash(first)
    client.request({
        "op": "append",
        "stream": trail.stream,
        "sequence": 1,
        "previous_hash": trail.ZERO_HASH,
        "head_hash": first["event_hash"],
        "record": first,
    })
    assert not audit_path.exists()

    trail.append(tool="after-restart", arguments={}, ok=True, required=True)

    assert trail.verify() == (True, 2)
    records = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert [record["tool"] for record in records] == ["crash-window", "after-restart"]


def _write_legacy_chain(path, count: int = 3) -> list[dict]:
    previous = ZERO_HASH
    records = []
    for index in range(1, count + 1):
        record, previous = _event(previous, tool=f"legacy-{index}")
        records.append(record)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in records))
    return records


def test_legacy_backfill_is_complete_idempotent_and_never_moves_head(tmp_path):
    source = tmp_path / "legacy.jsonl"
    records = _write_legacy_chain(source)
    store = WitnessStore(tmp_path / "legacy.sqlite3")
    stream = "audit:" + hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    try:
        for sequence, record in enumerate(records, 1):
            store.append(
                stream,
                sequence,
                record["previous_hash"],
                record["event_hash"],
                record=None,
            )
        before = store.head(stream)

        first = seed_and_backfill_jsonl(store, source)
        second = seed_and_backfill_jsonl(store, source)

        assert first["inserted"] == 3
        assert second["inserted"] == 0
        assert store.head(stream) == before
        assert [store.event(stream, seq)["record"] for seq in range(1, 4)] == records
    finally:
        store.close()


def test_nonempty_legacy_stream_seeds_before_owner_bind_and_rejects_wrong_owner(tmp_path):
    source = tmp_path / "staged.jsonl"
    logical = tmp_path / "served" / "events.jsonl"
    records = _write_legacy_chain(source, count=4)
    owner_uid = 41001
    stream = "audit:" + hashlib.sha256(str(logical.resolve()).encode()).hexdigest()
    store = WitnessStore(tmp_path / "seed-first.sqlite3")
    try:
        result = seed_and_backfill_jsonl(
            store, source, owner_uid=owner_uid, stream_path=logical,
        )
        assert result["sequence"] == 4
        bound = store.bind_stream(stream, owner_uid)
        assert bound["sequence"] == 4
        assert bound["head_hash"] == records[-1]["event_hash"]
        with pytest.raises(WitnessError, match="owned by another uid"):
            store.bind_stream(stream, owner_uid + 1)
    finally:
        store.close()


def test_legacy_backfill_conflict_rolls_back_all_new_rows(tmp_path):
    source = tmp_path / "legacy-conflict.jsonl"
    records = _write_legacy_chain(source)
    store = WitnessStore(tmp_path / "legacy-conflict.sqlite3")
    stream = "audit:" + hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    try:
        for sequence, record in enumerate(records, 1):
            store.append(
                stream,
                sequence,
                record["previous_hash"],
                record["event_hash"],
                record=None,
            )
        store.db.execute(
            "INSERT INTO events(stream,sequence,event_hash,record_json) VALUES(?,?,?,?)",
            (stream, 3, "f" * 64, json.dumps({"event_hash": "f" * 64})),
        )
        before = store.head(stream)

        with pytest.raises(WitnessError, match="conflicts"):
            seed_and_backfill_jsonl(store, source)

        assert store.head(stream) == before
        assert store.db.execute(
            "SELECT count(*) FROM events WHERE stream=?", (stream,),
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_legacy_backfill_rejects_head_mismatch_without_redefining_it(tmp_path):
    source = tmp_path / "legacy-head.jsonl"
    records = _write_legacy_chain(source, count=2)
    store = WitnessStore(tmp_path / "legacy-head.sqlite3")
    stream = "audit:" + hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    try:
        store.append(
            stream, 1, records[0]["previous_hash"], records[0]["event_hash"], record=None,
        )
        before = store.head(stream)

        with pytest.raises(WitnessError, match="immutable witness head"):
            seed_and_backfill_jsonl(store, source)

        assert store.head(stream) == before
        assert store.db.execute(
            "SELECT count(*) FROM events WHERE stream=?", (stream,),
        ).fetchone()[0] == 0
    finally:
        store.close()
