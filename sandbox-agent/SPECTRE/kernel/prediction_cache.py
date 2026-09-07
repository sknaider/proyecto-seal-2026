"""SPECTRE prediction_cache — D3 predictive coding cache layer.

Cache "expected response" for common queries so Nivel 4 (LLM)
is only invoked when there is prediction error (cache miss or deviation).

Estimated token reduction: 50%+ on repetitive queries (heartbeat, status,
routing decisions that re-appear within the TTL window).

Ref: spec_spectre_contract_v2.md D3, SUMMARY_JARVIS.md §D3
Uses: state/working_state.json → prediction_cache field (structure existed,
      this module adds the logic).
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

_STATE_PATH = Path(__file__).parent.parent / "state" / "working_state.json"
_DEFAULT_TTL_S = 300  # 5 minutes — matches LLM response cache heuristic
_MAX_CACHE_ENTRIES = 200


# ── State helpers ─────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if _STATE_PATH.exists():
        try:
            return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_cache(state: dict) -> dict:
    return state.get("prediction_cache", {})


def _set_cache(state: dict, cache: dict) -> None:
    state["prediction_cache"] = cache


# ── Hash ──────────────────────────────────────────────────────────────────────

def query_hash(query: str) -> str:
    """SHA-256 of normalized query → 16-char hex key."""
    normalized = " ".join(query.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# ── Public API ────────────────────────────────────────────────────────────────

def cache_put(qhash: str, response: str, ttl_s: float = _DEFAULT_TTL_S) -> None:
    """Store a response in the prediction cache with TTL."""
    state = _load_state()
    cache = _get_cache(state)

    # Evict oldest entries if at capacity
    if len(cache) >= _MAX_CACHE_ENTRIES:
        oldest_key = min(cache, key=lambda k: cache[k].get("stored_at", 0))
        del cache[oldest_key]

    cache[qhash] = {
        "response": response,
        "stored_at": time.time(),
        "expires_at": time.time() + ttl_s,
        "hit_count": cache.get(qhash, {}).get("hit_count", 0),
        "miss_count": cache.get(qhash, {}).get("miss_count", 0),
    }
    _set_cache(state, cache)
    _save_state(state)


def cache_get(qhash: str) -> str | None:
    """
    Retrieve cached response if it exists and has not expired.

    Returns None on miss or expiry (caller must invoke LLM).
    Records hit_count on success.
    """
    state = _load_state()
    cache = _get_cache(state)
    entry = cache.get(qhash)

    if entry is None:
        return None

    if time.time() > entry.get("expires_at", 0):
        # Expired: remove and return None
        del cache[qhash]
        _set_cache(state, cache)
        _save_state(state)
        return None

    # Hit — increment counter
    entry["hit_count"] = entry.get("hit_count", 0) + 1
    cache[qhash] = entry
    _set_cache(state, cache)
    _save_state(state)
    return entry["response"]


def cache_miss(qhash: str) -> None:
    """Record a miss (LLM was invoked). Increments miss_count for the entry if it exists."""
    state = _load_state()
    cache = _get_cache(state)
    if qhash in cache:
        cache[qhash]["miss_count"] = cache[qhash].get("miss_count", 0) + 1
        _set_cache(state, cache)
        _save_state(state)


def cache_invalidate(qhash: str) -> bool:
    """Remove entry from cache. Returns True if it existed."""
    state = _load_state()
    cache = _get_cache(state)
    if qhash in cache:
        del cache[qhash]
        _set_cache(state, cache)
        _save_state(state)
        return True
    return False


def prediction_error(qhash: str, actual_response: str) -> float:
    """
    Compare actual LLM response against cached prediction.

    Returns 0.0 (no error) if responses match exactly.
    Returns 1.0 (full error) if no cache entry or responses differ.
    Future: embedding similarity for partial match detection.
    """
    state = _load_state()
    cache = _get_cache(state)
    entry = cache.get(qhash)
    if entry is None:
        return 1.0
    cached = entry.get("response", "")
    return 0.0 if cached == actual_response else 1.0


def cache_stats() -> dict[str, Any]:
    """Return aggregate hit/miss statistics across all cache entries."""
    state = _load_state()
    cache = _get_cache(state)

    total_hits = sum(e.get("hit_count", 0) for e in cache.values())
    total_misses = sum(e.get("miss_count", 0) for e in cache.values())
    total = total_hits + total_misses
    now = time.time()
    active = sum(1 for e in cache.values() if now < e.get("expires_at", 0))

    return {
        "entries": len(cache),
        "active_entries": active,
        "total_hits": total_hits,
        "total_misses": total_misses,
        "hit_rate": round(total_hits / total, 4) if total > 0 else 0.0,
        "max_entries": _MAX_CACHE_ENTRIES,
    }


def cache_clear() -> int:
    """Remove all cache entries. Returns count removed. For dream_consolidator / reset."""
    state = _load_state()
    cache = _get_cache(state)
    count = len(cache)
    _set_cache(state, {})
    _save_state(state)
    return count
