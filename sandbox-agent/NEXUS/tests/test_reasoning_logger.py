"""Tests for NEXUS reasoning_logger (D5 trace store + Soul DB dual-write)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

_KERNEL = Path(__file__).resolve().parent.parent / "kernel"
sys.path.insert(0, str(_KERNEL))

import reasoning_logger  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_log(tmp_path, monkeypatch):
    """Redirect TRACE_LOG to a temp file for each test."""
    test_log = tmp_path / "test_traces.jsonl"
    monkeypatch.setattr(reasoning_logger, "TRACE_LOG", test_log)
    yield test_log


# ── store_trace ───────────────────────────────────────────────────────────────

def test_store_trace_returns_uuid(isolated_log):
    tid = reasoning_logger.store_trace(
        task="respond_william",
        input_excerpt="que pasa con el cluster?",
        premises=["triangle 3-spark estable", "vLLM cargando"],
        reasoning="William pregunta status; tengo info reciente de JARVIS",
        decision="responder con status detallado",
        confidence=0.85,
    )
    assert isinstance(tid, str)
    assert len(tid) == 32  # uuid hex


def test_store_trace_writes_to_log(isolated_log):
    reasoning_logger.store_trace(
        task="t",
        input_excerpt="x",
        premises=["p1"],
        reasoning="r",
        decision="d",
    )
    assert isolated_log.exists()
    lines = isolated_log.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["agent"] == "NEXUS"
    assert entry["task"] == "t"
    assert entry["confidence"] == 0.7  # default


def test_store_trace_caps_premises(isolated_log):
    long_premises = [f"p{i}" for i in range(50)]
    reasoning_logger.store_trace(
        task="t", input_excerpt="x", premises=long_premises,
        reasoning="r", decision="d",
    )
    entry = json.loads(isolated_log.read_text().splitlines()[0])
    assert len(entry["premises"]) == 20  # cap


def test_store_trace_caps_long_strings(isolated_log):
    long_str = "x" * 5000
    reasoning_logger.store_trace(
        task="t",
        input_excerpt=long_str,
        premises=["p"],
        reasoning=long_str,
        decision=long_str,
    )
    entry = json.loads(isolated_log.read_text().splitlines()[0])
    assert len(entry["input_excerpt"]) <= 500
    assert len(entry["reasoning"]) <= 2000
    assert len(entry["decision"]) <= 1000


# ── update_trace_outcome ──────────────────────────────────────────────────────

def test_update_outcome_writes_second_line(isolated_log):
    tid = reasoning_logger.store_trace(
        task="t", input_excerpt="x", premises=["p"],
        reasoning="r", decision="d",
    )
    ok = reasoning_logger.update_trace_outcome(tid, "completado OK", True)
    assert ok is True
    lines = isolated_log.read_text().splitlines()
    assert len(lines) == 2
    update = json.loads(lines[1])
    assert update["_kind"] == "outcome_update"
    assert update["trace_id"] == tid
    assert update["outcome_success"] is True


def test_update_records_latency(isolated_log):
    tid = reasoning_logger.store_trace(
        task="t", input_excerpt="x", premises=["p"],
        reasoning="r", decision="d",
    )
    time.sleep(0.05)
    reasoning_logger.update_trace_outcome(tid, "done", True)
    update = json.loads(isolated_log.read_text().splitlines()[1])
    assert update["latency_ms"] is not None
    assert update["latency_ms"] >= 50


# ── search_traces ─────────────────────────────────────────────────────────────

def test_search_returns_recent_first(isolated_log):
    tid1 = reasoning_logger.store_trace(
        task="alpha", input_excerpt="x", premises=[],
        reasoning="r", decision="d",
    )
    time.sleep(0.01)
    tid2 = reasoning_logger.store_trace(
        task="beta", input_excerpt="x", premises=[],
        reasoning="r", decision="d",
    )
    matches = reasoning_logger.search_traces(limit=10)
    assert len(matches) == 2
    assert matches[0]["trace_id"] == tid2  # most recent first
    assert matches[1]["trace_id"] == tid1


def test_search_filter_by_task_prefix(isolated_log):
    reasoning_logger.store_trace(
        task="alpha_one", input_excerpt="x", premises=[],
        reasoning="r", decision="d",
    )
    reasoning_logger.store_trace(
        task="beta_one", input_excerpt="x", premises=[],
        reasoning="r", decision="d",
    )
    matches = reasoning_logger.search_traces(task_prefix="alpha", limit=10)
    assert len(matches) == 1
    assert matches[0]["task"] == "alpha_one"


def test_search_filter_by_action_type(isolated_log):
    reasoning_logger.store_trace(
        task="t", input_excerpt="x", premises=[],
        reasoning="r", decision="d", action_type="emit",
    )
    reasoning_logger.store_trace(
        task="t", input_excerpt="x", premises=[],
        reasoning="r", decision="d", action_type="execute",
    )
    matches = reasoning_logger.search_traces(action_type="execute", limit=10)
    assert len(matches) == 1
    assert matches[0]["action_type"] == "execute"


def test_search_merges_outcome_updates(isolated_log):
    tid = reasoning_logger.store_trace(
        task="t", input_excerpt="x", premises=[],
        reasoning="r", decision="d",
    )
    reasoning_logger.update_trace_outcome(tid, "OK", True)
    matches = reasoning_logger.search_traces(limit=10)
    assert len(matches) == 1
    assert matches[0]["outcome"] == "OK"
    assert matches[0]["outcome_success"] is True


# ── stats ─────────────────────────────────────────────────────────────────────

def test_stats_empty(isolated_log):
    s = reasoning_logger.stats()
    assert s["total"] == 0
    assert s["completed"] == 0
    assert s["pending"] == 0


def test_stats_after_traces(isolated_log):
    tid1 = reasoning_logger.store_trace(
        task="t", input_excerpt="x", premises=[],
        reasoning="r", decision="d", action_type="emit",
    )
    tid2 = reasoning_logger.store_trace(
        task="t", input_excerpt="x", premises=[],
        reasoning="r", decision="d", action_type="execute",
    )
    reasoning_logger.update_trace_outcome(tid1, "OK", True)
    reasoning_logger.update_trace_outcome(tid2, "fail", False)

    s = reasoning_logger.stats()
    assert s["total"] == 2
    assert s["completed"] == 2
    assert s["success_rate"] == 0.5
    assert s["by_action_type"]["emit"] == 1
    assert s["by_action_type"]["execute"] == 1


# ── Soul DB dual-write contract ───────────────────────────────────────────────

def test_store_trace_attempts_soul_db_persist(isolated_log, monkeypatch):
    """When store_trace runs, it should call _persist_sync (which schedules Soul DB write)."""
    persisted = []

    def fake_persist(local_id, entry):
        persisted.append((local_id, entry))

    monkeypatch.setattr(reasoning_logger, "_persist_sync", fake_persist)
    tid = reasoning_logger.store_trace(
        task="dual_write", input_excerpt="x", premises=[],
        reasoning="r", decision="d",
    )
    assert len(persisted) == 1
    assert persisted[0][0] == tid
    assert persisted[0][1]["task"] == "dual_write"


def test_store_trace_continues_when_soul_db_fails(isolated_log, monkeypatch):
    """If Soul DB persist raises, JSONL still has the trace (fail-soft)."""
    def boom(local_id, entry):
        raise RuntimeError("simulated Soul DB unreachable")

    monkeypatch.setattr(reasoning_logger, "_persist_sync", boom)
    # store_trace should still raise (fail-soft is at the persist layer, not at append)
    # but the JSONL line should already be written before the exception bubbles
    try:
        tid = reasoning_logger.store_trace(
            task="dual_write_fail", input_excerpt="x", premises=[],
            reasoning="r", decision="d",
        )
    except RuntimeError:
        tid = None
    # Verify JSONL has the entry regardless
    assert isolated_log.exists()
    content = isolated_log.read_text()
    assert "dual_write_fail" in content


def test_update_outcome_attempts_soul_db_update(isolated_log, monkeypatch):
    """update_trace_outcome should attempt Soul DB sync via _update_outcome_sync."""
    updated = []

    def fake_update(local_id, outcome, success, latency_ms):
        updated.append((local_id, outcome, success, latency_ms))

    monkeypatch.setattr(reasoning_logger, "_update_outcome_sync", fake_update)
    tid = reasoning_logger.store_trace(
        task="t", input_excerpt="x", premises=[],
        reasoning="r", decision="d",
    )
    reasoning_logger.update_trace_outcome(tid, "ok", True)
    assert len(updated) == 1
    assert updated[0][0] == tid
    assert updated[0][1] == "ok"
    assert updated[0][2] is True


def test_persist_async_populates_id_map(isolated_log, monkeypatch):
    """When _persist_to_soul_db returns a db_id, _id_map gets populated for later updates."""
    import asyncio as aio

    async def fake_persist(local_id, entry):
        return 12345  # simulated DB BIGSERIAL

    monkeypatch.setattr(reasoning_logger, "_persist_to_soul_db", fake_persist)
    reasoning_logger._id_map.clear()

    aio.run(reasoning_logger._persist_async_and_map("local-uuid-abc", {"task": "t"}))
    assert reasoning_logger._id_map.get("local-uuid-abc") == 12345


def test_id_map_isolates_local_to_db(isolated_log):
    """trace_id (UUID hex) and Soul DB id (int) are correlated, not equal."""
    reasoning_logger._id_map["abc123"] = 999
    reasoning_logger._id_map["def456"] = 1000
    assert reasoning_logger._id_map["abc123"] != reasoning_logger._id_map["def456"]
    assert isinstance(reasoning_logger._id_map["abc123"], int)
    reasoning_logger._id_map.clear()
