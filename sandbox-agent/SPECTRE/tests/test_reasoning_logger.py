"""SPECTRE reasoning_logger tests — D5 reasoning trace invariant.

R1: trace_store() returns a UUID string (36 chars)
R2: trace_store() writes entry to working_state["reasoning_traces"]
R3: trace_store() entry has all required D5 fields
R4: trace_store() keeps only last _MAX_LOCAL_TRACES (50) entries
R5: trace_update() returns True when trace found, updates fields
R6: trace_update() returns False when trace_id not found
R7: trace_search() filters by task_prefix
R8: trace_search() filters by action_type
R9: trace_search() returns most recent first, respects limit
R10: trace_stats() returns correct totals and success_rate
R11: trace_stats() on empty state returns all zeros
R12: trace_store() output is capped at 50; oldest dropped
"""
from __future__ import annotations

import json
import sys
import tempfile
import uuid
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))

import reasoning_logger as rl

_pass = 0
_fail = 0


def test(name: str, fn) -> None:
    global _pass, _fail
    num = _pass + _fail + 1
    try:
        fn()
        print(f"  ✅ R{num}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ R{num}: {name} — {e}")
        _fail += 1


print("\n=== TEST REASONING LOGGER ===")


def _patched_state(state_path: Path):
    return mock.patch.object(rl, "WORKING_STATE_PATH", state_path)


def _fresh_state(tmp: Path) -> Path:
    p = tmp / "working_state.json"
    p.write_text("{}")
    return p


# ─────────────────────────────────────────────────────────────────────────────
# R1: trace_store() returns a UUID string (36 chars with dashes)
# ─────────────────────────────────────────────────────────────────────────────

def r1_returns_uuid():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh_state(Path(d))
        with _patched_state(sp):
            tid = rl.trace_store(
                task="test_task",
                premises=["p1"],
                reasoning="r1",
                conclusion="c1",
            )
    assert isinstance(tid, str), "trace_id must be a string"
    assert len(tid) == 36, f"UUID string must be 36 chars, got {len(tid)}"
    parts = tid.split("-")
    assert len(parts) == 5, f"UUID must have 5 parts: {tid}"

test("trace_store(): returns 36-char UUID string", r1_returns_uuid)


# ─────────────────────────────────────────────────────────────────────────────
# R2: trace_store() writes entry to working_state["reasoning_traces"]
# ─────────────────────────────────────────────────────────────────────────────

def r2_writes_to_state():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh_state(Path(d))
        with _patched_state(sp):
            tid = rl.trace_store(
                task="write_test",
                premises=["a"],
                reasoning="b",
                conclusion="c",
            )
        state = json.loads(sp.read_text())

    assert "reasoning_traces" in state, "reasoning_traces key must exist in state"
    traces = state["reasoning_traces"]
    assert len(traces) == 1
    assert traces[0]["trace_id"] == tid

test("trace_store(): writes entry to working_state[reasoning_traces]", r2_writes_to_state)


# ─────────────────────────────────────────────────────────────────────────────
# R3: entry has all required D5 fields
# ─────────────────────────────────────────────────────────────────────────────

def r3_required_fields():
    required = {
        "trace_id", "agent", "task", "action_type",
        "premises", "reasoning", "conclusion", "confidence",
        "outcome", "outcome_success", "created_at", "updated_at",
    }
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh_state(Path(d))
        with _patched_state(sp):
            rl.trace_store(
                task="fields_test",
                premises=["x"],
                reasoning="y",
                conclusion="z",
                confidence=0.9,
                action_type="emit",
            )
        state = json.loads(sp.read_text())

    entry = state["reasoning_traces"][0]
    missing = required - set(entry.keys())
    assert not missing, f"Missing D5 fields: {missing}"
    assert entry["agent"] == "SPECTRE"
    assert entry["action_type"] == "emit"
    assert entry["confidence"] == 0.9
    assert entry["outcome"] is None
    assert entry["outcome_success"] is None

test("trace_store(): entry has all required D5 fields with correct defaults", r3_required_fields)


# ─────────────────────────────────────────────────────────────────────────────
# R4: keeps only last _MAX_LOCAL_TRACES entries (50)
# ─────────────────────────────────────────────────────────────────────────────

def r4_capped_at_max():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh_state(Path(d))
        with _patched_state(sp):
            for i in range(55):
                rl.trace_store(
                    task=f"task_{i:03d}",
                    premises=[],
                    reasoning="r",
                    conclusion="c",
                )
            state = json.loads(sp.read_text())

    traces = state["reasoning_traces"]
    assert len(traces) == rl._MAX_LOCAL_TRACES, f"Expected {rl._MAX_LOCAL_TRACES}, got {len(traces)}"
    # Most recent should be task_054
    assert traces[-1]["task"] == "task_054", f"Last task: {traces[-1]['task']}"
    # Oldest should be task_005 (55-50=5)
    assert traces[0]["task"] == "task_005", f"First task: {traces[0]['task']}"

test(f"trace_store(): caps at {rl._MAX_LOCAL_TRACES} entries, drops oldest", r4_capped_at_max)


# ─────────────────────────────────────────────────────────────────────────────
# R5: trace_update() returns True and updates fields when found
# ─────────────────────────────────────────────────────────────────────────────

def r5_update_found():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh_state(Path(d))
        with _patched_state(sp):
            tid = rl.trace_store(
                task="update_me",
                premises=[],
                reasoning="r",
                conclusion="c",
            )
            result = rl.trace_update(tid, outcome="done successfully", outcome_success=True)
            state = json.loads(sp.read_text())

    assert result is True, "trace_update must return True when found"
    entry = state["reasoning_traces"][0]
    assert entry["outcome"] == "done successfully"
    assert entry["outcome_success"] is True
    assert entry["updated_at"] is not None

test("trace_update(): returns True, updates outcome + outcome_success when found", r5_update_found)


# ─────────────────────────────────────────────────────────────────────────────
# R6: trace_update() returns False when trace_id not found
# ─────────────────────────────────────────────────────────────────────────────

def r6_update_not_found():
    fake_id = str(uuid.uuid4())
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh_state(Path(d))
        with _patched_state(sp):
            result = rl.trace_update(fake_id, outcome="nope", outcome_success=False)

    assert result is False, "trace_update must return False when trace_id not found"

test("trace_update(): returns False when trace_id not found", r6_update_not_found)


# ─────────────────────────────────────────────────────────────────────────────
# R7: trace_search() filters by task_prefix
# ─────────────────────────────────────────────────────────────────────────────

def r7_search_task_prefix():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh_state(Path(d))
        with _patched_state(sp):
            rl.trace_store(task="alpha_1", premises=[], reasoning="r", conclusion="c")
            rl.trace_store(task="alpha_2", premises=[], reasoning="r", conclusion="c")
            rl.trace_store(task="beta_1", premises=[], reasoning="r", conclusion="c")
            results = rl.trace_search(task_prefix="alpha")

    assert len(results) == 2, f"Expected 2 alpha results, got {len(results)}"
    assert all(t["task"].startswith("alpha") for t in results)

test("trace_search(): filters correctly by task_prefix", r7_search_task_prefix)


# ─────────────────────────────────────────────────────────────────────────────
# R8: trace_search() filters by action_type
# ─────────────────────────────────────────────────────────────────────────────

def r8_search_action_type():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh_state(Path(d))
        with _patched_state(sp):
            rl.trace_store(task="t1", premises=[], reasoning="r", conclusion="c", action_type="emit")
            rl.trace_store(task="t2", premises=[], reasoning="r", conclusion="c", action_type="escalate")
            rl.trace_store(task="t3", premises=[], reasoning="r", conclusion="c", action_type="emit")
            results = rl.trace_search(action_type="emit")

    assert len(results) == 2, f"Expected 2 emit results, got {len(results)}"
    assert all(t["action_type"] == "emit" for t in results)

test("trace_search(): filters correctly by action_type", r8_search_action_type)


# ─────────────────────────────────────────────────────────────────────────────
# R9: trace_search() returns most recent first, respects limit
# ─────────────────────────────────────────────────────────────────────────────

def r9_search_order_and_limit():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh_state(Path(d))
        with _patched_state(sp):
            for i in range(10):
                rl.trace_store(task=f"t_{i:02d}", premises=[], reasoning="r", conclusion="c")
            results = rl.trace_search(limit=3)

    assert len(results) == 3, f"Expected limit=3 results, got {len(results)}"
    # Most recent first — last stored was t_09
    assert results[0]["task"] == "t_09", f"First result must be most recent: {results[0]['task']}"
    assert results[2]["task"] == "t_07"

test("trace_search(): most recent first, limit respected", r9_search_order_and_limit)


# ─────────────────────────────────────────────────────────────────────────────
# R10: trace_stats() returns correct totals and success_rate
# ─────────────────────────────────────────────────────────────────────────────

def r10_stats_correct():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh_state(Path(d))
        with _patched_state(sp):
            t1 = rl.trace_store(task="s1", premises=[], reasoning="r", conclusion="c")
            t2 = rl.trace_store(task="s2", premises=[], reasoning="r", conclusion="c")
            t3 = rl.trace_store(task="s3", premises=[], reasoning="r", conclusion="c")
            rl.trace_update(t1, outcome="ok", outcome_success=True)
            rl.trace_update(t2, outcome="fail", outcome_success=False)
            # t3 is pending
            stats = rl.trace_stats()

    assert stats["total"] == 3
    assert stats["completed"] == 2
    assert stats["pending"] == 1
    assert stats["successes"] == 1
    assert stats["failures"] == 1
    assert stats["success_rate"] == 0.5

test("trace_stats(): correct total/completed/pending/successes/failures/success_rate", r10_stats_correct)


# ─────────────────────────────────────────────────────────────────────────────
# R11: trace_stats() on empty state returns all zeros
# ─────────────────────────────────────────────────────────────────────────────

def r11_stats_empty():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh_state(Path(d))
        with _patched_state(sp):
            stats = rl.trace_stats()

    assert stats["total"] == 0
    assert stats["completed"] == 0
    assert stats["pending"] == 0
    assert stats["success_rate"] == 0.0

test("trace_stats(): empty state returns all zeros", r11_stats_empty)


# ─────────────────────────────────────────────────────────────────────────────
# R12: oldest entries dropped when cap exceeded
# ─────────────────────────────────────────────────────────────────────────────

def r12_oldest_dropped():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh_state(Path(d))
        with _patched_state(sp):
            # Store exactly MAX+1 traces
            max_n = rl._MAX_LOCAL_TRACES
            ids = []
            for i in range(max_n + 1):
                tid = rl.trace_store(task=f"drop_{i}", premises=[], reasoning="r", conclusion="c")
                ids.append(tid)

            # Try to update the OLDEST trace (drop_0) — should not be found
            result = rl.trace_update(ids[0], outcome="ghost", outcome_success=True)

    assert result is False, "Oldest trace (dropped) must not be findable after cap"

test("trace_store(): oldest trace not findable after cap exceeded (dropped from buffer)", r12_oldest_dropped)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Reasoning Logger Tests: {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
