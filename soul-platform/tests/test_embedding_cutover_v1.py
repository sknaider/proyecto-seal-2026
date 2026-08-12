from __future__ import annotations

import hashlib
import asyncio
import json
import sqlite3
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from soul_framework import Soul
from soul_framework.config import SoulConfig
from soul_framework.backend.schema import SCHEMA_SQL

from soul_platform.bootstrap import initialize
from soul_platform.embedding_cutover import (
    _exclusive_sqlite_probe,
    activate,
    prepare,
    rollback,
    verify_candidate,
)
from soul_platform.proxy import ProxySettings


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _db(path: Path, marker: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES (?)", (marker,))


def _checkpoint(source: Path, candidate: Path, checkpoint: Path) -> dict:
    state = {
        "version": 1,
        "status": "completed",
        "plan": {
            "source": str(source.resolve()),
            "candidate": str(candidate.resolve()),
            "checkpoint": str(checkpoint.resolve()),
            "source_dimensions": 128,
            "target_dimensions": 1024,
            "source_sha256": _sha(source),
            "rows": {"memories": 0, "procedural_memories": 0},
        },
        "candidate_sha256": _sha(candidate),
    }
    checkpoint.write_text(json.dumps(state))
    return state


def _legacy_install(tmp_path: Path):
    result = initialize(
        root=tmp_path / "SOUL",
        upstream_kind="ollama",
        upstream_base_url="http://127.0.0.1:11434/v1",
        upstream_model="brain",
        enable_autostart=False,
    )
    text = result.config.read_text()
    start, end = text.index("[embedding]"), text.index("[proxy]")
    result.config.write_text(text[:start] + text[end:])
    _db(result.soul_db, "original-128")
    candidate = result.root / "MachineSoul.bge-m3.candidate.db"
    checkpoint = result.root / "MachineSoul.bge-m3.checkpoint.json"
    _db(candidate, "candidate-1024")
    return result, candidate, checkpoint


def _marker(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return str(connection.execute("SELECT value FROM marker").fetchone()[0])


def test_activate_and_rollback_preserve_both_database_generations(tmp_path, monkeypatch):
    result, candidate, checkpoint = _legacy_install(tmp_path)
    original_config = result.config.read_bytes()
    original_sha = _sha(result.soul_db)
    candidate_sha = _sha(candidate)
    _checkpoint(result.soul_db, candidate, checkpoint)
    monkeypatch.setattr("soul_platform.embedding_cutover._probe_bge", lambda: None)

    active = activate(result.config, checkpoint)
    assert active["activation"]["status"] == "active"
    assert _marker(result.soul_db) == "candidate-1024"
    assert _sha(result.soul_db) == candidate_sha
    assert not candidate.exists()
    assert Path(active["activation"]["source_backup"]).is_file()
    settings = ProxySettings.from_toml(result.config)
    assert (settings.embedding_provider, settings.embedding_dimensions) == ("bge-m3", 1024)
    assert settings.memory_vector_index == "auto"

    restored = rollback(result.config, checkpoint)
    assert restored["activation"]["status"] == "rolled-back"
    assert _marker(result.soul_db) == "original-128"
    assert _sha(result.soul_db) == original_sha
    assert result.config.read_bytes() == original_config
    retained = Path(restored["activation"]["retained_candidate"])
    assert retained.is_file() and _sha(retained) == candidate_sha
    legacy = ProxySettings.from_toml(result.config)
    assert (legacy.embedding_provider, legacy.embedding_dimensions) == ("simple", 128)

    async def startup_smoke():
        config = SoulConfig(
            backend="sqlite",
            backend_url=str(legacy.soul_db),
            embedding_provider="simple",
            embedding_dimensions=128,
            memory_vector_index="exact",
        )
        async with Soul.create(legacy.soul_name, config=config) as soul:
            assert isinstance(await soul.boot(), str)

    asyncio.run(startup_smoke())


def test_explicit_legacy_embedding_block_is_replaced_and_rollback_is_exact(
    tmp_path, monkeypatch
):
    result, candidate, checkpoint = _legacy_install(tmp_path)
    legacy_block = (
        '[embedding]\nprovider = "simple"\ndimensions = 128\nmodel = "simple"\n'
        'url = "http://127.0.0.1:11434/api/embed"\ntimeout_seconds = 60\n'
        'vector_index = "exact"\n\n'
    )
    config_text = result.config.read_text()
    insertion = config_text.index("[proxy]")
    result.config.write_text(config_text[:insertion] + legacy_block + config_text[insertion:])
    original = result.config.read_bytes()
    _checkpoint(result.soul_db, candidate, checkpoint)
    monkeypatch.setattr("soul_platform.embedding_cutover._probe_bge", lambda: None)

    activate(result.config, checkpoint)
    active = ProxySettings.from_toml(result.config)
    assert (
        active.embedding_provider,
        active.embedding_dimensions,
        active.embedding_model,
        active.memory_vector_index,
    ) == ("bge-m3", 1024, "bge-m3", "auto")
    assert result.config.read_text().count("[embedding]") == 1

    rollback(result.config, checkpoint)
    assert result.config.read_bytes() == original
    restored = ProxySettings.from_toml(result.config)
    assert (
        restored.embedding_provider,
        restored.embedding_dimensions,
        restored.embedding_model,
        restored.memory_vector_index,
    ) == ("simple", 128, "simple", "exact")


def test_candidate_or_source_byte_mismatch_fails_before_any_move(tmp_path):
    result, candidate, checkpoint = _legacy_install(tmp_path)
    _checkpoint(result.soul_db, candidate, checkpoint)
    candidate.write_bytes(candidate.read_bytes() + b"tamper")
    source_before = _sha(result.soul_db)
    with pytest.raises(ValueError, match="candidate bytes"):
        activate(result.config, checkpoint)
    assert _sha(result.soul_db) == source_before
    assert candidate.is_file()
    assert not list(result.root.glob("*.backup"))


def test_corrupt_wal_sidecar_fails_before_activation(tmp_path):
    result, candidate, checkpoint = _legacy_install(tmp_path)
    _checkpoint(result.soul_db, candidate, checkpoint)
    Path(f"{result.soul_db}-wal").write_bytes(b"live")
    with pytest.raises(RuntimeError, match="stop SOUL Platform"):
        activate(result.config, checkpoint)
    assert _marker(result.soul_db) == "original-128"
    assert candidate.is_file()


def test_clean_stopped_wal_is_checkpointed_before_fingerprinting(tmp_path):
    result, _candidate, _checkpoint = _legacy_install(tmp_path)
    crash_db = tmp_path / "crash-source.db"
    shutil.copy2(result.soul_db, crash_db)
    connection = sqlite3.connect(crash_db)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute(
        "INSERT INTO memories(agent, content, embedding, valid_from, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "MachineSoul",
            "committed before cutover",
            b"x" * 512,
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
    )
    connection.commit()
    shutil.copy2(crash_db, result.soul_db)
    shutil.copy2(f"{crash_db}-wal", f"{result.soul_db}-wal")
    shutil.copy2(f"{crash_db}-shm", f"{result.soul_db}-shm")
    connection.close()

    _exclusive_sqlite_probe(result.soul_db, checkpoint_wal=True)
    with sqlite3.connect(result.soul_db) as verified:
        count = verified.execute(
            "SELECT COUNT(*) FROM memories WHERE content='committed before cutover'"
        ).fetchone()[0]
    assert count == 1
    assert not Path(f"{result.soul_db}-wal").exists() or not Path(
        f"{result.soul_db}-wal"
    ).stat().st_size


def test_activation_rejects_wal_committed_after_candidate_fingerprint(
    tmp_path, monkeypatch
):
    result, candidate, checkpoint = _legacy_install(tmp_path)
    with sqlite3.connect(result.soul_db) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    _checkpoint(result.soul_db, candidate, checkpoint)

    script = """
import os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.execute('PRAGMA wal_autocheckpoint=0')
connection.execute(
    "INSERT INTO memories(agent, content, embedding, valid_from, created_at) "
    "VALUES (?, ?, ?, ?, ?)",
    ('MachineSoul', 'late committed row', b'x' * 512,
     '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'),
)
connection.commit()
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", script, str(result.soul_db)], check=True)
    assert Path(f"{result.soul_db}-wal").stat().st_size > 0
    source_before = _sha(result.soul_db)
    monkeypatch.setattr("soul_platform.embedding_cutover._probe_bge", lambda: None)

    with pytest.raises(RuntimeError, match="final WAL checkpoint"):
        activate(result.config, checkpoint)

    assert _sha(result.soul_db) != source_before
    assert _marker(result.soul_db) == "original-128"
    assert candidate.is_file()
    assert not list(result.root.glob("*.backup"))


def test_mixed_128d_candidate_is_rejected_even_with_matching_checkpoint(tmp_path):
    result, candidate, checkpoint = _legacy_install(tmp_path)
    with sqlite3.connect(candidate) as connection:
        connection.execute(
            "INSERT INTO memories(agent, content, embedding, valid_from, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "MachineSoul",
                "legacy vector",
                b"x" * 512,
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
    state = _checkpoint(result.soul_db, candidate, checkpoint)
    state["plan"]["rows"]["memories"] = 1
    checkpoint.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="embedding gate failed"):
        verify_candidate(checkpoint)


def test_source_symlink_is_rejected_before_probe_or_move(tmp_path, monkeypatch):
    result, candidate, checkpoint = _legacy_install(tmp_path)
    real_source = result.root / "real-source.db"
    result.soul_db.rename(real_source)
    result.soul_db.symlink_to(real_source)
    state = _checkpoint(real_source, candidate, checkpoint)
    state["plan"]["source"] = str(result.soul_db.absolute())
    checkpoint.write_text(json.dumps(state))
    called = False

    def probe():
        nonlocal called
        called = True

    monkeypatch.setattr("soul_platform.embedding_cutover._probe_bge", probe)
    with pytest.raises(ValueError, match="symlink/reparse"):
        activate(result.config, checkpoint)
    assert called is False
    assert candidate.is_file() and real_source.is_file()


def test_parent_symlink_is_rejected_before_resolve(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    source = real / "source.db"
    candidate = real / "candidate.db"
    checkpoint = real / "checkpoint.json"
    _db(source, "source")
    _db(candidate, "candidate")
    _checkpoint(source, candidate, checkpoint)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    state = json.loads(checkpoint.read_text())
    state["plan"]["source"] = str(linked / "source.db")
    checkpoint.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="symlink/reparse path component"):
        verify_candidate(checkpoint)


def test_bge_readiness_failure_prevents_activation(tmp_path, monkeypatch):
    result, candidate, checkpoint = _legacy_install(tmp_path)
    _checkpoint(result.soul_db, candidate, checkpoint)

    def fail():
        raise RuntimeError("local BGE-M3 readiness probe failed")

    monkeypatch.setattr("soul_platform.embedding_cutover._probe_bge", fail)
    with pytest.raises(RuntimeError, match="readiness"):
        activate(result.config, checkpoint)
    assert _marker(result.soul_db) == "original-128"
    assert candidate.is_file()


def test_late_source_writer_is_detected_before_rename(tmp_path, monkeypatch):
    result, candidate, checkpoint = _legacy_install(tmp_path)
    _checkpoint(result.soul_db, candidate, checkpoint)
    monkeypatch.setattr("soul_platform.embedding_cutover._probe_bge", lambda: None)
    real_sha = __import__("soul_platform.embedding_cutover", fromlist=["_sha256"])._sha256
    calls = 0

    def mutating_sha(path):
        nonlocal calls
        value = real_sha(path)
        if path == result.soul_db.resolve():
            calls += 1
            if calls == 2:
                with sqlite3.connect(path) as connection:
                    connection.execute("INSERT INTO marker VALUES ('late-writer')")
        return value

    monkeypatch.setattr("soul_platform.embedding_cutover._sha256", mutating_sha)
    with pytest.raises(RuntimeError, match="source changed"):
        activate(result.config, checkpoint)
    assert candidate.is_file()
    with sqlite3.connect(result.soul_db) as connection:
        values = {str(row[0]) for row in connection.execute("SELECT value FROM marker")}
    assert values == {"original-128", "late-writer"}


def test_failed_activation_checkpoint_is_retriable(tmp_path, monkeypatch):
    result, candidate, checkpoint = _legacy_install(tmp_path)
    state = _checkpoint(result.soul_db, candidate, checkpoint)
    state["activation"] = {"status": "activation-failed-rolled-back"}
    checkpoint.write_text(json.dumps(state))
    monkeypatch.setattr("soul_platform.embedding_cutover._probe_bge", lambda: None)
    activated = activate(result.config, checkpoint)
    assert activated["activation"]["status"] == "active"


async def test_prepare_delegates_exact_128_to_1024_contract(tmp_path, monkeypatch):
    captured = {}

    class Provider:
        dimensions = 1024

    async def fake_migrate(source, provider, **kwargs):
        captured.update(source=source, provider=provider, **kwargs)
        return {"status": "completed"}

    monkeypatch.setattr(
        "soul_platform.embedding_cutover.BgeM3Embedding", lambda **_kwargs: Provider()
    )
    monkeypatch.setattr(
        "soul_platform.embedding_cutover.migrate_sqlite_embeddings", fake_migrate
    )
    source = tmp_path / "source.db"
    source.touch()
    candidate = tmp_path / "candidate.db"
    checkpoint = tmp_path / "checkpoint.json"
    result = await prepare(
        source,
        candidate=candidate,
        checkpoint=checkpoint,
        batch_size=512,
        resume=True,
    )
    assert result == {"status": "completed"}
    assert captured["source_dimensions"] == 128
    assert captured["target_dimensions"] == 1024
    assert captured["batch_size"] == 512
    assert captured["resume"] is True
    assert captured["provider_name"] == "bge-m3:bge-m3"


def test_verify_rejects_non_completed_checkpoint(tmp_path):
    source, candidate, checkpoint = (
        tmp_path / "source.db",
        tmp_path / "candidate.db",
        tmp_path / "checkpoint.json",
    )
    _db(source, "source")
    _db(candidate, "candidate")
    state = _checkpoint(source, candidate, checkpoint)
    state["status"] = "paused"
    checkpoint.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="not completed"):
        verify_candidate(checkpoint)
