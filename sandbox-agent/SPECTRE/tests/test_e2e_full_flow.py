"""SPECTRE E2E Full-Flow Tests — 3 scenarios.

Tests the complete pipeline: reflex (Nivel 2) → prediction_cache (D3) →
cortex (Nivel 3) → contract_gate (D6) → sandbox emit.

Scenarios:
  E2E-1: Cache HIT  — same query twice, LLM only called once
  E2E-2: Cache MISS — new query, LLM invoked, response stored, emit occurs
  E2E-3: Contract VIOLATION — production sink blocked, no emit, budget untouched

Ref: spec_spectre_contract_v2.md — JARVIS assignment 2026-05-02
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))
sys.path.insert(0, str(Path(__file__).parent.parent / "state"))
sys.path.insert(0, str(Path(__file__).parent.parent / "handlers"))

import prediction_cache as cache_pc
import cortex
from contract_layer import (
    _invocation_timestamps,
    budget_status,
    ContractViolation,
)

_pass = 0
_fail = 0


def test(name: str, fn) -> None:
    global _pass, _fail
    try:
        fn()
        print(f"  ✅ E2E-{_pass + _fail + 1}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ E2E-{_pass + _fail + 1}: {name} — {e}")
        _fail += 1


def _reset_all() -> None:
    """Reset cache, processed IDs, working_state, and invocation budget."""
    cache_pc.cache_clear()
    _invocation_timestamps.clear()
    if cortex.PROCESSED_IDS_PATH.exists():
        cortex.PROCESSED_IDS_PATH.unlink()
    if cortex.ESCALATION_QUEUE_PATH.exists():
        cortex.ESCALATION_QUEUE_PATH.unlink()
    state = cortex._read_state()
    state.pop("cortex_stats", None)
    state.pop("last_cortex_outputs", None)
    state.pop("goal_stack", None)
    cortex._write_state(state)


def _make_queue_entry(content: str, sender: str, event_id: str) -> dict:
    return {
        "event": {"id": event_id, "from": sender, "content": content, "message": content},
        "keywords": [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# E2E-1: Cache HIT — same query sent twice
#   Round 1: cache miss → LLM invoked → response stored → emit called
#   Round 2: cache hit → LLM NOT invoked → emit called with cached response
# ─────────────────────────────────────────────────────────────────────────────

def e2e_cache_hit():
    _reset_all()
    content = "e2e full flow: what is the spectre reflex interval"
    sender = "DUM"

    llm_calls: list[list] = []
    emit_calls: list[str] = []

    async def fake_chat(messages, max_tokens=150):
        llm_calls.append(messages)
        return "reflex interval is 50ms"

    async def fake_emit(msg: str, respond_to=None) -> None:
        emit_calls.append(msg)

    async def run():
        processed: set = set()
        with mock.patch.object(cortex._get_llm(), "chat", side_effect=fake_chat):
            with mock.patch("cortex._emit_cortex", side_effect=fake_emit):
                with mock.patch("cortex.contract_gate", return_value=(True, "ok", [])):
                    # Round 1: cache miss
                    e1 = _make_queue_entry(content, sender, "e2e1_r1")
                    await cortex._process_entry(e1, processed)

                    llm_round1 = len(llm_calls)
                    emit_round1 = len(emit_calls)

                    # Round 2: same content, cache hit
                    e2 = _make_queue_entry(content, sender, "e2e1_r2")
                    await cortex._process_entry(e2, processed)

        assert llm_round1 == 1, f"Round 1: expected 1 LLM call, got {llm_round1}"
        assert len(llm_calls) == 1, f"Round 2: LLM should NOT be called on hit, total={len(llm_calls)}"
        assert emit_round1 == 1, f"Round 1: expected 1 emit, got {emit_round1}"
        assert len(emit_calls) == 2, f"Round 2: emit should fire on cache hit too, got {len(emit_calls)}"
        assert "cached" in emit_calls[1], f"Round 2 emit should be tagged [cached], got: {emit_calls[1]}"

    asyncio.run(run())

test("full pipeline: reflex→cache hit→emit (LLM called only once for 2 identical queries)", e2e_cache_hit)


# ─────────────────────────────────────────────────────────────────────────────
# E2E-2: Cache MISS — new unique query
#   LLM invoked → response stored in cache → emit fires → working_state updated
# ─────────────────────────────────────────────────────────────────────────────

def e2e_cache_miss():
    _reset_all()
    content = "e2e full flow: unique query for cache miss scenario"
    sender = "JARVIS"

    llm_response = "understood, processing unique query via full cortex path"
    emit_calls: list[str] = []

    async def fake_chat(messages, max_tokens=150):
        return llm_response

    async def fake_emit(msg: str, respond_to=None) -> None:
        emit_calls.append(msg)

    async def run():
        processed: set = set()
        with mock.patch.object(cortex._get_llm(), "chat", side_effect=fake_chat):
            with mock.patch("cortex._emit_cortex", side_effect=fake_emit):
                with mock.patch("cortex.contract_gate", return_value=(True, "ok", [])):
                    entry = _make_queue_entry(content, sender, "e2e2_miss")
                    result = await cortex._process_entry(entry, processed)

        assert result is True, f"_process_entry should return True on success, got {result}"
        assert len(emit_calls) == 1, f"Expected 1 emit on cache miss, got {len(emit_calls)}"
        assert "[SPECTRE/cortex]" in emit_calls[0], f"Emit missing cortex tag: {emit_calls[0]}"
        assert llm_response[:50] in emit_calls[0], f"Emit should include LLM response, got: {emit_calls[0]}"

        # Cache should now hold the response for future hits
        qhash = cache_pc.query_hash(f"{sender}:{content[:100]}")
        cached = cache_pc.cache_get(qhash)
        assert cached == llm_response, f"Cache should store LLM response, got: {cached}"

        # working_state should record llm_calls
        state = cortex._read_state()
        llm_calls_count = state.get("cortex_stats", {}).get("llm_calls", 0)
        assert llm_calls_count >= 1, f"working_state llm_calls should be ≥1, got {llm_calls_count}"

    asyncio.run(run())

test("full pipeline: reflex→cache miss→LLM→store→emit→working_state updated", e2e_cache_miss)


# ─────────────────────────────────────────────────────────────────────────────
# E2E-3: Contract VIOLATION — production sink blocked by contract_gate
#   action targeting web_chat real → CV-2 hard block → no emit → budget unchanged
# ─────────────────────────────────────────────────────────────────────────────

def e2e_contract_violation():
    _reset_all()

    # Verify contract_gate blocks CV-2 (production sink) with a hard ContractViolation
    from contract_layer import check_core_values

    emit_calls: list[str] = []

    # Simulate what cortex._emit_cortex does internally:
    # It calls contract_gate with sandbox sink — that should pass.
    # But direct production access (web_chat real) must raise ContractViolation.

    violation_raised = False
    try:
        check_core_values(
            "emit to web_chat_real production endpoint",
            metadata={"sink": "web_chat", "channel": "web_chat", "target": "william"},
        )
    except ContractViolation as cv:
        violation_raised = True
        assert cv.value_id == "CV-2", f"Expected CV-2 violation, got {cv.value_id}"
        assert "real web_chat" in str(cv) or "production" in str(cv).lower(), f"CV-2 message should reference production/real_webchat: {cv}"

    assert violation_raised, "Production web_chat access must raise ContractViolation (CV-2)"

    # Invocation budget must remain unchanged — blocked before consume_invocation_budget()
    budget_before = budget_status()["used"]
    assert budget_before == 0, f"Budget should be 0 after violation (no emit consumed), got {budget_before}"

    # Sandbox sink (correct path) must NOT raise
    soft = check_core_values(
        "emit cortex response to SPECTRE_sandbox",
        metadata={"sink": "sandbox", "channel": "sandbox"},
    )
    assert isinstance(soft, list), f"Sandbox emit should return soft violations list, got {soft}"

    # E2E: contract_gate with production sink hard-blocks → allowed=False
    from contract_layer import contract_gate as cg
    try:
        allowed, reason, violations = cg(
            "emit to production web_chat",
            metadata={"sink": "web_chat", "channel": "web_chat", "target": "william"},
        )
        # If no exception: allowed should be False due to CV-2
        assert not allowed, f"Production sink should not be allowed, got allowed={allowed}: {reason}"
    except ContractViolation:
        pass  # hard violation is also acceptable — means it was blocked

test("contract violation: CV-2 blocks production web_chat, sandbox path clean, budget=0", e2e_contract_violation)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"E2E Full-Flow Tests: {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
