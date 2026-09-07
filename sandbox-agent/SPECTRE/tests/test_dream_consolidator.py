"""SPECTRE dream_consolidator tests.

DC1: run_consolidation() returns stats dict with required keys
DC2: run_consolidation() idempotent — returns skipped=True if already ran today
DC3: run_consolidation(force=True) re-runs even if already ran today
DC4: episodic purge removes entries older than TTL_DAYS
DC5: episodic purge keeps entries within TTL_DAYS
DC6: trace consolidation archives completed traces, keeps last MAX_COMPLETED_TRACES
DC7: trace consolidation keeps pending traces untouched
DC8: working_state cleanup removes stale keys
DC9: prediction_cache trimmed to MAX_PREDICTION_CACHE
DC10: ocean snapshot written to ocean_daily_snapshots (capped at 30)
DC11: run_consolidation returns skipped if lock already held
DC12: dream_last_run_date written to working_state after successful run
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest.mock as mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))

import dream_consolidator as dc

_pass = 0
_fail = 0


def test(name: str, fn) -> None:
    global _pass, _fail
    num = _pass + _fail + 1
    try:
        fn()
        print(f"  ✅ DC{num}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ DC{num}: {name} — {e}")
        _fail += 1


print("\n=== TEST DREAM CONSOLIDATOR ===")


def _patched(state_path: Path, queue_path: Path | None = None, lock_path: Path | None = None):
    patches = [mock.patch.object(dc, "WORKING_STATE_PATH", state_path)]
    if queue_path is not None:
        patches.append(mock.patch.object(dc, "ESCALATION_QUEUE_PATH", queue_path))
    if lock_path is not None:
        patches.append(mock.patch.object(dc, "LOCK_PATH", lock_path))
    return _MultiPatch(patches)


class _MultiPatch:
    def __init__(self, patches):
        self._patches = patches
        self._mocks = []

    def __enter__(self):
        for p in self._patches:
            self._mocks.append(p.__enter__())
        return self

    def __exit__(self, *args):
        for p in reversed(self._patches):
            p.__exit__(*args)


def _fresh_state(tmp: Path, content: dict | None = None) -> Path:
    p = tmp / "working_state.json"
    p.write_text(json.dumps(content or {}))
    return p


def _fresh_queue(tmp: Path, entries: list | None = None) -> Path:
    p = tmp / "escalation_queue.json"
    p.write_text(json.dumps(entries or []))
    return p


def _fresh_lock(tmp: Path) -> Path:
    return tmp / "dream.lock"


# ─────────────────────────────────────────────────────────────────────────────
# DC1: run_consolidation() returns stats dict with required keys
# ─────────────────────────────────────────────────────────────────────────────

def dc1_stats_keys():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sp = _fresh_state(tmp)
        qp = _fresh_queue(tmp)
        lp = _fresh_lock(tmp)
        with _patched(sp, qp, lp):
            result = dc.run_consolidation(force=True)

    assert "date" in result
    assert "started_at" in result
    assert "completed_at" in result
    assert "episodic_purged" in result
    assert result.get("skipped") is not True

test("run_consolidation(): returns stats with required keys", dc1_stats_keys)


# ─────────────────────────────────────────────────────────────────────────────
# DC2: idempotent — skipped if already ran today
# ─────────────────────────────────────────────────────────────────────────────

def dc2_idempotent():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sp = _fresh_state(tmp, {"dream_last_run_date": today})
        qp = _fresh_queue(tmp)
        lp = _fresh_lock(tmp)
        with _patched(sp, qp, lp):
            result = dc.run_consolidation()

    assert result.get("skipped") is True
    assert result.get("reason") == "already_ran_today"

test("run_consolidation(): skipped=True if already ran today", dc2_idempotent)


# ─────────────────────────────────────────────────────────────────────────────
# DC3: force=True bypasses idempotency
# ─────────────────────────────────────────────────────────────────────────────

def dc3_force():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sp = _fresh_state(tmp, {"dream_last_run_date": today})
        qp = _fresh_queue(tmp)
        lp = _fresh_lock(tmp)
        with _patched(sp, qp, lp):
            result = dc.run_consolidation(force=True)

    assert result.get("skipped") is not True, "force=True must bypass idempotency check"
    assert "completed_at" in result

test("run_consolidation(force=True): re-runs even if already ran today", dc3_force)


# ─────────────────────────────────────────────────────────────────────────────
# DC4: episodic purge removes old entries
# ─────────────────────────────────────────────────────────────────────────────

def dc4_purge_old():
    old_ts = (datetime.now(timezone.utc) - timedelta(days=dc.TTL_DAYS + 1)).isoformat()
    new_ts = datetime.now(timezone.utc).isoformat()

    entries = [
        {"event": {"content": "old"}, "queued_at": old_ts},
        {"event": {"content": "new"}, "queued_at": new_ts},
    ]
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sp = _fresh_state(tmp)
        qp = _fresh_queue(tmp, entries)
        lp = _fresh_lock(tmp)
        with _patched(sp, qp, lp):
            result = dc.run_consolidation(force=True)
            remaining = json.loads(qp.read_text())

    assert result["episodic_purged"] == 1, f"Expected 1 purged, got {result['episodic_purged']}"
    assert len(remaining) == 1
    assert remaining[0]["event"]["content"] == "new"

test("episodic purge: removes entries older than TTL_DAYS", dc4_purge_old)


# ─────────────────────────────────────────────────────────────────────────────
# DC5: episodic purge keeps entries within TTL
# ─────────────────────────────────────────────────────────────────────────────

def dc5_purge_keeps_recent():
    recent_ts = (datetime.now(timezone.utc) - timedelta(days=dc.TTL_DAYS - 1)).isoformat()
    entries = [{"event": {}, "queued_at": recent_ts}]
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sp = _fresh_state(tmp)
        qp = _fresh_queue(tmp, entries)
        lp = _fresh_lock(tmp)
        with _patched(sp, qp, lp):
            result = dc.run_consolidation(force=True)
            remaining = json.loads(qp.read_text())

    assert result["episodic_purged"] == 0
    assert len(remaining) == 1

test("episodic purge: keeps entries within TTL_DAYS", dc5_purge_keeps_recent)


# ─────────────────────────────────────────────────────────────────────────────
# DC6: trace consolidation archives completed, keeps last MAX_COMPLETED_TRACES
# ─────────────────────────────────────────────────────────────────────────────

def dc6_trace_consolidation():
    # Create MAX+5 completed traces
    n = dc.MAX_COMPLETED_TRACES + 5
    traces = [
        {"trace_id": f"t{i}", "task": f"task_{i}", "outcome_success": True,
         "outcome": "ok", "created_at": "2026-05-01T00:00:00Z"}
        for i in range(n)
    ]
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sp = _fresh_state(tmp, {"reasoning_traces": traces})
        qp = _fresh_queue(tmp)
        lp = _fresh_lock(tmp)
        with _patched(sp, qp, lp):
            result = dc.run_consolidation(force=True)
            state = json.loads(sp.read_text())

    assert result["traces_consolidated"] == 5
    remaining = state["reasoning_traces"]
    assert len(remaining) == dc.MAX_COMPLETED_TRACES, (
        f"Expected {dc.MAX_COMPLETED_TRACES} traces, got {len(remaining)}"
    )
    # Summaries should exist
    assert len(state.get("dream_summaries", [])) >= 1

test("trace consolidation: archives old completed traces, keeps last MAX_COMPLETED_TRACES", dc6_trace_consolidation)


# ─────────────────────────────────────────────────────────────────────────────
# DC7: pending traces never touched by consolidation
# ─────────────────────────────────────────────────────────────────────────────

def dc7_pending_untouched():
    n = dc.MAX_COMPLETED_TRACES + 3
    completed = [
        {"trace_id": f"c{i}", "task": f"done_{i}", "outcome_success": True, "outcome": "ok",
         "created_at": "2026-05-01T00:00:00Z"}
        for i in range(n)
    ]
    pending = [
        {"trace_id": f"p{i}", "task": f"pend_{i}", "outcome_success": None, "outcome": None,
         "created_at": "2026-05-01T00:00:00Z"}
        for i in range(4)
    ]
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sp = _fresh_state(tmp, {"reasoning_traces": completed + pending})
        qp = _fresh_queue(tmp)
        lp = _fresh_lock(tmp)
        with _patched(sp, qp, lp):
            dc.run_consolidation(force=True)
            state = json.loads(sp.read_text())

    remaining = state["reasoning_traces"]
    pending_ids = {t["trace_id"] for t in remaining if t["outcome_success"] is None}
    assert pending_ids == {"p0", "p1", "p2", "p3"}, f"Pending traces modified: {pending_ids}"

test("trace consolidation: pending traces untouched", dc7_pending_untouched)


# ─────────────────────────────────────────────────────────────────────────────
# DC8: working_state cleanup removes known stale keys
# ─────────────────────────────────────────────────────────────────────────────

def dc8_stale_keys_removed():
    initial = {"last_event_stale": "old_data", "debug_scratch": "x", "normal_key": "keep"}
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sp = _fresh_state(tmp, initial)
        qp = _fresh_queue(tmp)
        lp = _fresh_lock(tmp)
        with _patched(sp, qp, lp):
            result = dc.run_consolidation(force=True)
            state = json.loads(sp.read_text())

    assert "last_event_stale" not in state
    assert "debug_scratch" not in state
    assert "normal_key" in state
    assert "last_event_stale" in result["stale_keys_removed"]

test("working_state cleanup: stale keys removed, normal keys kept", dc8_stale_keys_removed)


# ─────────────────────────────────────────────────────────────────────────────
# DC9: prediction_cache trimmed to MAX_PREDICTION_CACHE
# ─────────────────────────────────────────────────────────────────────────────

def dc9_prediction_cache_trimmed():
    big_cache = {f"key_{i}": f"val_{i}" for i in range(dc.MAX_PREDICTION_CACHE + 20)}
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sp = _fresh_state(tmp, {"prediction_cache": big_cache})
        qp = _fresh_queue(tmp)
        lp = _fresh_lock(tmp)
        with _patched(sp, qp, lp):
            result = dc.run_consolidation(force=True)
            state = json.loads(sp.read_text())

    assert len(state["prediction_cache"]) == dc.MAX_PREDICTION_CACHE
    assert result["prediction_cache_trimmed"] == 20

test("working_state cleanup: prediction_cache trimmed to MAX_PREDICTION_CACHE", dc9_prediction_cache_trimmed)


# ─────────────────────────────────────────────────────────────────────────────
# DC10: ocean snapshot written, capped at 30 days
# ─────────────────────────────────────────────────────────────────────────────

def dc10_ocean_snapshot():
    existing_snaps = [{"ts": f"2026-0{i+1}-01T00:00:00Z", "baseline": {}, "runtime": {}, "total_delta_events": 0}
                      for i in range(30)]
    ocean_b = {"O": 0.774, "C": 0.949, "E": 0.662, "A": 0.507, "N": 0.172}
    initial = {
        "ocean_baseline": ocean_b,
        "ocean_runtime": ocean_b,
        "ocean_daily_snapshots": existing_snaps,
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sp = _fresh_state(tmp, initial)
        qp = _fresh_queue(tmp)
        lp = _fresh_lock(tmp)
        with _patched(sp, qp, lp):
            result = dc.run_consolidation(force=True)
            state = json.loads(sp.read_text())

    snaps = state.get("ocean_daily_snapshots", [])
    assert len(snaps) == 30, f"Expected 30 snapshots (cap), got {len(snaps)}"
    assert result["ocean_snapshot"] == "written"

test("ocean snapshot: written and capped at 30 daily entries", dc10_ocean_snapshot)


# ─────────────────────────────────────────────────────────────────────────────
# DC11: run_consolidation returns skipped if lock already held
# ─────────────────────────────────────────────────────────────────────────────

def dc11_lock_contention():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sp = _fresh_state(tmp)
        qp = _fresh_queue(tmp)
        lp = _fresh_lock(tmp)

        # Simulate lock held by patching _acquire_run_lock to return None
        with _patched(sp, qp, lp):
            with mock.patch.object(dc, "_acquire_run_lock", return_value=None):
                result = dc.run_consolidation(force=True)

    assert result.get("skipped") is True
    assert result.get("reason") == "lock_held"

test("run_consolidation(): skipped=True when lock already held", dc11_lock_contention)


# ─────────────────────────────────────────────────────────────────────────────
# DC12: dream_last_run_date written after successful run
# ─────────────────────────────────────────────────────────────────────────────

def dc12_last_run_date_written():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sp = _fresh_state(tmp)
        qp = _fresh_queue(tmp)
        lp = _fresh_lock(tmp)
        with _patched(sp, qp, lp):
            dc.run_consolidation(force=True)
            state = json.loads(sp.read_text())

    assert state.get("dream_last_run_date") == today
    assert "dream_report" in state

test("run_consolidation(): dream_last_run_date and dream_report written to state", dc12_last_run_date_written)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Dream Consolidator Tests: {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
