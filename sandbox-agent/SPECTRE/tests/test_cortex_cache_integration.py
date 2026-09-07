"""SPECTRE cortex↔prediction_cache integration — 5 tests.

Verifica que cortex.py use prediction_cache.py correctamente:
  - Cache miss → LLM invocado → respuesta guardada
  - Cache hit → LLM NO invocado
  - cache_stats refleja hits/misses reales
  - LLM error → degraded mode → cache no contaminado
  - cortex_stats() devuelve métricas fusionadas (cache_pc + working_state)
"""
from __future__ import annotations

import asyncio
import sys
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))
sys.path.insert(0, str(Path(__file__).parent.parent / "state"))

import prediction_cache as cache_pc
import cortex

_pass = 0
_fail = 0


def test(name: str, fn) -> None:
    global _pass, _fail
    try:
        fn()
        print(f"  ✅ CI{_pass + _fail + 1}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ CI{_pass + _fail + 1}: {name} — {e}")
        _fail += 1


def _make_entry(content: str, sender: str = "test_agent", event_id: str = "evt_001") -> dict:
    return {
        "event": {"id": event_id, "from": sender, "content": content, "message": content},
        "keywords": [],
    }


def _reset():
    cache_pc.cache_clear()
    if cortex.PROCESSED_IDS_PATH.exists():
        cortex.PROCESSED_IDS_PATH.unlink()
    state = cortex._read_state()
    state.pop("cortex_stats", None)
    state.pop("last_cortex_outputs", None)
    state.pop("goal_stack", None)  # ensure D1 filter is inactive between tests
    cortex._write_state(state)


# ─────────────────────────────────────────────────────────────────────────────
# CI1: First call → cache miss → LLM invoked → response stored in cache
# ─────────────────────────────────────────────────────────────────────────────

def ci1_miss_then_store():
    _reset()
    entry = _make_entry("what is the heartbeat interval", "DUM", "evt_ci1")
    llm_calls = []

    async def fake_chat(messages, max_tokens=150):
        llm_calls.append(messages)
        return "heartbeat interval is 100ms"

    async def run():
        processed = set()
        with mock.patch.object(cortex._get_llm(), "chat", side_effect=fake_chat):
            with mock.patch("cortex._emit_cortex", return_value=None):
                with mock.patch("cortex.contract_gate", return_value=(True, "ok", False)):
                    await cortex._process_entry(entry, processed)

    asyncio.run(run())

    assert len(llm_calls) == 1, f"Expected 1 LLM call, got {len(llm_calls)}"
    qhash = cache_pc.query_hash("DUM:what is the heartbeat interval")
    cached = cache_pc.cache_get(qhash)
    assert cached == "heartbeat interval is 100ms", f"Cache should store LLM response, got: {cached}"

test("cache miss → LLM invoked → response stored for future hits", ci1_miss_then_store)


# ─────────────────────────────────────────────────────────────────────────────
# CI2: Second call same query → cache hit → LLM NOT invoked
# ─────────────────────────────────────────────────────────────────────────────

def ci2_hit_skips_llm():
    _reset()
    entry = _make_entry("what is the heartbeat interval", "DUM", "evt_ci2a")
    llm_calls = []

    async def fake_chat(messages, max_tokens=150):
        llm_calls.append(messages)
        return "heartbeat interval is 100ms"

    async def run():
        processed: set = set()
        with mock.patch("cortex._get_llm") as mock_llm:
            mock_llm.return_value.chat = fake_chat
            with mock.patch("cortex._emit_cortex", return_value=None):
                with mock.patch("cortex.contract_gate", return_value=(True, "ok", False)):
                    # First call — miss, stores in cache
                    await cortex._process_entry(entry, processed)
                    llm_calls.clear()

                    # Second call — same content, different event_id
                    entry2 = _make_entry("what is the heartbeat interval", "DUM", "evt_ci2b")
                    await cortex._process_entry(entry2, processed)

    asyncio.run(run())

    assert len(llm_calls) == 0, f"LLM should NOT be called on cache hit, got {len(llm_calls)} calls"

test("cache hit → LLM skipped (0 LLM calls on repeat query)", ci2_hit_skips_llm)


# ─────────────────────────────────────────────────────────────────────────────
# CI3: cache_stats shows correct hit/miss counts after sequence
# ─────────────────────────────────────────────────────────────────────────────

def ci3_stats_accurate():
    _reset()

    async def fake_chat(messages, max_tokens=150):
        return "kernel is alive"

    async def run():
        processed: set = set()
        with mock.patch("cortex._get_llm") as mock_llm:
            mock_llm.return_value.chat = fake_chat
            with mock.patch("cortex._emit_cortex", return_value=None):
                with mock.patch("cortex.contract_gate", return_value=(True, "ok", False)):
                    e1 = _make_entry("kernel status check", "ADA", "evt_ci3a")
                    e2 = _make_entry("kernel status check", "ADA", "evt_ci3b")  # hit
                    e3 = _make_entry("kernel status check", "ADA", "evt_ci3c")  # hit
                    await cortex._process_entry(e1, processed)
                    await cortex._process_entry(e2, processed)
                    await cortex._process_entry(e3, processed)

    asyncio.run(run())

    stats = cortex.cortex_stats()
    assert stats["cache_hits"] == 2, f"Expected 2 hits, got {stats['cache_hits']}"
    assert stats["cache_misses"] == 1, f"Expected 1 miss, got {stats['cache_misses']}"
    assert abs(stats["cache_hit_rate"] - 0.6667) < 0.01, f"Expected ~0.67 hit_rate, got {stats['cache_hit_rate']}"

test("cache_stats: 2 hits + 1 miss after 3 calls (1 unique query)", ci3_stats_accurate)


# ─────────────────────────────────────────────────────────────────────────────
# CI4: LLM error → degraded mode → cache not polluted
# ─────────────────────────────────────────────────────────────────────────────

def ci4_llm_error_no_cache_pollution():
    _reset()
    entry = _make_entry("predict this will fail", "DUM", "evt_ci4")

    async def failing_chat(messages, max_tokens=150):
        raise cortex.LLMUnavailable("all backends down")

    async def run():
        processed: set = set()
        with mock.patch("cortex._get_llm") as mock_llm:
            mock_llm.return_value.chat = failing_chat
            with mock.patch("cortex._emit_cortex", return_value=None):
                result = await cortex._process_entry(entry, processed)
                assert result is False, f"Degraded mode should return False, got {result}"

    asyncio.run(run())

    qhash = cache_pc.query_hash("DUM:predict this will fail")
    cached = cache_pc.cache_get(qhash)
    assert cached is None, f"Cache should be empty after LLM error, got: {cached}"

test("LLM error → degraded mode (False) → cache not polluted", ci4_llm_error_no_cache_pollution)


# ─────────────────────────────────────────────────────────────────────────────
# CI5: cortex_stats() merges cache_pc metrics + working_state llm_failures
# ─────────────────────────────────────────────────────────────────────────────

def ci5_cortex_stats_merged():
    _reset()
    # Inject a failure count into working_state
    state = cortex._read_state()
    state["cortex_stats"] = {"llm_failures": 3}
    cortex._write_state(state)

    # Add a cache hit manually
    qh = cache_pc.query_hash("test:merged stats")
    cache_pc.cache_put(qh, "ok", ttl_s=60)
    cache_pc.cache_get(qh)  # generates 1 hit

    stats = cortex.cortex_stats()
    assert stats["cache_hits"] == 1, f"Expected 1 hit from cache_pc, got {stats['cache_hits']}"
    assert stats["llm_failures"] == 3, f"Expected 3 failures from working_state, got {stats['llm_failures']}"
    assert "cache_hit_rate" in stats, "cortex_stats should include cache_hit_rate"
    assert "cache_entries" in stats, "cortex_stats should include cache_entries"

test("cortex_stats(): merges cache_pc metrics + working_state llm_failures", ci5_cortex_stats_merged)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Cortex↔Cache Integration Tests: {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
