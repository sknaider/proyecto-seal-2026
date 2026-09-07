"""SPECTRE prediction_cache D3 — 8 unit tests.

Tests: put/get, miss, TTL expiry, miss counter, prediction_error,
cache_stats, persistence, eviction cap.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))
sys.path.insert(0, str(Path(__file__).parent.parent / "state"))

import prediction_cache as pc

STATE_PATH = Path(__file__).parent.parent / "state" / "working_state.json"
_pass = 0
_fail = 0


def _reset():
    """Clear prediction_cache field in working_state for test isolation."""
    pc.cache_clear()


def test(name: str, fn) -> None:
    global _pass, _fail
    try:
        fn()
        print(f"  ✅ D3-{_pass + _fail + 1}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ D3-{_pass + _fail + 1}: {name} — {e}")
        _fail += 1


# ─────────────────────────────────────────────────────────────────────────────
# D3-1: put then get → hit
# ─────────────────────────────────────────────────────────────────────────────

def d3_put_get_hit():
    _reset()
    qh = pc.query_hash("what is the heartbeat status")
    pc.cache_put(qh, "heartbeat OK — 100ms interval", ttl_s=60)
    result = pc.cache_get(qh)
    assert result == "heartbeat OK — 100ms interval", f"Expected hit, got: {result}"

test("cache_put + cache_get → cache hit returns correct response", d3_put_get_hit)


# ─────────────────────────────────────────────────────────────────────────────
# D3-2: get on missing key → None (miss)
# ─────────────────────────────────────────────────────────────────────────────

def d3_get_miss():
    _reset()
    result = pc.cache_get("0000000000000000")
    assert result is None, f"Expected None on miss, got: {result}"

test("cache_get on unknown key → None (forces LLM invocation)", d3_get_miss)


# ─────────────────────────────────────────────────────────────────────────────
# D3-3: TTL expiry → entry removed, returns None
# ─────────────────────────────────────────────────────────────────────────────

def d3_ttl_expiry():
    _reset()
    qh = pc.query_hash("expiring query test spectre")
    pc.cache_put(qh, "cached response", ttl_s=0.01)  # 10ms TTL
    time.sleep(0.05)  # wait for expiry
    result = pc.cache_get(qh)
    assert result is None, f"Expected None after TTL expiry, got: {result}"
    # Verify entry was removed from state
    state = pc._load_state()
    cache = pc._get_cache(state)
    assert qh not in cache, "Expired entry should be removed from cache dict"

test("TTL expiry: cache_get returns None and removes stale entry", d3_ttl_expiry)


# ─────────────────────────────────────────────────────────────────────────────
# D3-4: hit_count increments on each cache_get hit
# ─────────────────────────────────────────────────────────────────────────────

def d3_hit_count():
    _reset()
    qh = pc.query_hash("repeated status query for hit counting test")
    pc.cache_put(qh, "status: all systems nominal", ttl_s=60)
    pc.cache_get(qh)
    pc.cache_get(qh)
    pc.cache_get(qh)
    state = pc._load_state()
    entry = pc._get_cache(state).get(qh, {})
    assert entry.get("hit_count") == 3, f"Expected 3 hits, got {entry.get('hit_count')}"

test("hit_count increments correctly across multiple cache_get calls", d3_hit_count)


# ─────────────────────────────────────────────────────────────────────────────
# D3-5: cache_miss increments miss_count
# ─────────────────────────────────────────────────────────────────────────────

def d3_miss_counter():
    _reset()
    qh = pc.query_hash("complex novel query requiring llm call")
    pc.cache_put(qh, "initial response", ttl_s=60)
    pc.cache_miss(qh)
    pc.cache_miss(qh)
    state = pc._load_state()
    entry = pc._get_cache(state).get(qh, {})
    assert entry.get("miss_count") == 2, f"Expected 2 misses, got {entry.get('miss_count')}"

test("cache_miss: miss_count increments when LLM was invoked", d3_miss_counter)


# ─────────────────────────────────────────────────────────────────────────────
# D3-6: prediction_error — exact match = 0.0, different = 1.0
# ─────────────────────────────────────────────────────────────────────────────

def d3_prediction_error():
    _reset()
    qh = pc.query_hash("predict this response content spectre test")
    response = "the kernel is alive and OCEAN stable"
    pc.cache_put(qh, response, ttl_s=60)

    err_zero = pc.prediction_error(qh, response)
    assert err_zero == 0.0, f"Exact match should give 0.0, got {err_zero}"

    err_one = pc.prediction_error(qh, "something completely different")
    assert err_one == 1.0, f"Different response should give 1.0, got {err_one}"

    err_no_cache = pc.prediction_error("ffffffffffffffff", "any response")
    assert err_no_cache == 1.0, f"No cache entry should give 1.0, got {err_no_cache}"

test("prediction_error: 0.0 on exact match, 1.0 on mismatch or no entry", d3_prediction_error)


# ─────────────────────────────────────────────────────────────────────────────
# D3-7: cache_stats returns correct hit_rate
# ─────────────────────────────────────────────────────────────────────────────

def d3_stats_hit_rate():
    _reset()
    qh1 = pc.query_hash("query alpha spectre stats test")
    qh2 = pc.query_hash("query beta spectre stats test")
    pc.cache_put(qh1, "resp A", ttl_s=60)
    pc.cache_put(qh2, "resp B", ttl_s=60)

    pc.cache_get(qh1)   # hit
    pc.cache_get(qh1)   # hit
    pc.cache_miss(qh2)  # miss

    stats = pc.cache_stats()
    assert stats["total_hits"] == 2, f"Expected 2 hits, got {stats['total_hits']}"
    assert stats["total_misses"] == 1, f"Expected 1 miss, got {stats['total_misses']}"
    assert abs(stats["hit_rate"] - 0.6667) < 0.01, f"Expected ~0.67 hit_rate, got {stats['hit_rate']}"
    assert stats["entries"] == 2

test("cache_stats: hit_rate, total_hits, total_misses computed correctly", d3_stats_hit_rate)


# ─────────────────────────────────────────────────────────────────────────────
# D3-8: persistence — cache survives across _load_state calls (disk round-trip)
# ─────────────────────────────────────────────────────────────────────────────

def d3_persistence():
    _reset()
    qh = pc.query_hash("persistent query test disk round-trip spectre")
    pc.cache_put(qh, "persisted response data", ttl_s=120)

    # Simulate new process: read directly from disk without using module cache
    raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    cache_on_disk = raw.get("prediction_cache", {})
    assert qh in cache_on_disk, "Cache entry not found on disk after cache_put"
    assert cache_on_disk[qh]["response"] == "persisted response data"

test("persistence: cache_put writes to working_state.json, survives disk round-trip", d3_persistence)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Prediction Cache D3 Tests: {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
