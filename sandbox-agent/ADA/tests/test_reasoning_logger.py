"""Tests for ADA reasoning_logger — local trace lifecycle + Soul DB dual-write."""
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
    test_log = tmp_path / "test_traces.jsonl"
    monkeypatch.setattr(reasoning_logger, "TRACE_LOG", test_log)
    yield test_log


def test_store_trace_returns_uuid(isolated_log):
    tid = reasoning_logger.store_trace(
        task="implement", input_excerpt="x", premises=["p"],
        reasoning="r", decision="d",
    )
    assert isinstance(tid, str) and len(tid) == 32


def test_store_trace_writes_to_log(isolated_log):
    reasoning_logger.store_trace(task="t", input_excerpt="x", premises=[],
                                  reasoning="r", decision="d")
    entry = json.loads(isolated_log.read_text().splitlines()[0])
    assert entry["agent"] == "ADA"
    assert entry["task"] == "t"


def test_store_trace_caps_premises(isolated_log):
    long = [f"p{i}" for i in range(50)]
    reasoning_logger.store_trace(task="t", input_excerpt="x", premises=long,
                                  reasoning="r", decision="d")
    entry = json.loads(isolated_log.read_text().splitlines()[0])
    assert len(entry["premises"]) == 20


def test_update_outcome_records_latency(isolated_log):
    tid = reasoning_logger.store_trace(task="t", input_excerpt="x", premises=["p"],
                                        reasoning="r", decision="d")
    time.sleep(0.05)
    reasoning_logger.update_trace_outcome(tid, "done", True)
    update = json.loads(isolated_log.read_text().splitlines()[1])
    assert update["latency_ms"] >= 50


def test_search_returns_recent_first(isolated_log):
    tid1 = reasoning_logger.store_trace(task="alpha", input_excerpt="x", premises=[],
                                         reasoning="r", decision="d")
    time.sleep(0.01)
    tid2 = reasoning_logger.store_trace(task="beta", input_excerpt="x", premises=[],
                                         reasoning="r", decision="d")
    matches = reasoning_logger.search_traces(limit=10)
    assert matches[0]["trace_id"] == tid2


def test_search_merges_outcome_updates(isolated_log):
    tid = reasoning_logger.store_trace(task="t", input_excerpt="x", premises=[],
                                        reasoning="r", decision="d")
    reasoning_logger.update_trace_outcome(tid, "OK", True)
    matches = reasoning_logger.search_traces(limit=10)
    assert matches[0]["outcome"] == "OK"
    assert matches[0]["outcome_success"] is True


def test_stats_after_traces(isolated_log):
    tid1 = reasoning_logger.store_trace(task="t", input_excerpt="x", premises=[],
                                         reasoning="r", decision="d", action_type="emit")
    tid2 = reasoning_logger.store_trace(task="t", input_excerpt="x", premises=[],
                                         reasoning="r", decision="d", action_type="execute")
    reasoning_logger.update_trace_outcome(tid1, "OK", True)
    reasoning_logger.update_trace_outcome(tid2, "fail", False)
    s = reasoning_logger.stats()
    assert s["total"] == 2
    assert s["success_rate"] == 0.5


def test_store_trace_attempts_soul_db_persist(isolated_log, monkeypatch):
    persisted = []

    def fake_persist(local_id, entry):
        persisted.append((local_id, entry))

    monkeypatch.setattr(reasoning_logger, "_persist_sync", fake_persist)
    tid = reasoning_logger.store_trace(task="dual_write", input_excerpt="x",
                                        premises=[], reasoning="r", decision="d")
    assert len(persisted) == 1
    assert persisted[0][0] == tid


def test_update_outcome_attempts_soul_db_update(isolated_log, monkeypatch):
    updated = []

    def fake_update(local_id, outcome, success, latency_ms):
        updated.append((local_id, outcome, success))

    monkeypatch.setattr(reasoning_logger, "_update_outcome_sync", fake_update)
    tid = reasoning_logger.store_trace(task="t", input_excerpt="x", premises=[],
                                        reasoning="r", decision="d")
    reasoning_logger.update_trace_outcome(tid, "ok", True)
    assert len(updated) == 1
    assert updated[0][2] is True
