"""Tests for ProviderFallbackPool and CredentialPool error_rate extensions."""

import sys
import pathlib
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.multi_model import ModelAdapter, ModelError, ModelResponse, ChatMessage
from tools.gateway.provider_fallback import ProviderFallbackPool
from seal.credential_pool import (
    CredentialPool, STRATEGY_LOWEST_ERROR_RATE,
    STRATEGY_FILL_FIRST, STATUS_EXHAUSTED,
)


# ── Stub adapter ─────────────────────────────────────────────────────────────

class _StubAdapter(ModelAdapter):
    def __init__(self, name: str, responses):
        self._name = name
        self._responses = list(responses)
        self._calls = 0

    def complete(self, messages, model="", timeout=120) -> ModelResponse:
        self._calls += 1
        if not self._responses:
            raise ModelError(f"{self._name}: no response configured")
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def available(self) -> bool:
        return True


def _ok(provider="stub") -> ModelResponse:
    return ModelResponse(content="ok", model="m", provider=provider)


# ── ProviderFallbackPool tests ────────────────────────────────────────────────

def test_pool_first_provider_success():
    pool = ProviderFallbackPool()
    pool.add(_StubAdapter("a", [_ok("a")]), "a")
    pool.add(_StubAdapter("b", [_ok("b")]), "b")
    resp = pool.complete([])
    assert resp.provider == "a"


def test_pool_fallback_on_error():
    pool = ProviderFallbackPool()
    pool.add(_StubAdapter("a", [ModelError("a failed")]), "a", "a")
    pool.add(_StubAdapter("b", [_ok("b")]), "b", "b")
    resp = pool.complete([])
    assert resp.provider == "b"


def test_pool_all_fail_raises():
    pool = ProviderFallbackPool()
    pool.add(_StubAdapter("a", [ModelError("a down")]), "a", "a")
    pool.add(_StubAdapter("b", [ModelError("b down")]), "b", "b")
    raised = False
    try:
        pool.complete([])
    except ModelError as e:
        raised = True
        assert "all providers failed" in str(e)
        assert "a down" in str(e)
        assert "b down" in str(e)
    assert raised


def test_pool_error_rate_routing():
    """Provider with lower error_rate is tried first even if added second."""
    a = _StubAdapter("a", [_ok("a")])
    b = _StubAdapter("b", [_ok("b")])

    pool = ProviderFallbackPool()
    pool.add(a, "a", "a")
    pool.add(b, "b", "b")

    # Poison a with errors so its error_rate > 0
    pool._entries[0].stats.error_count = 3
    pool._entries[0].stats.success_count = 1  # error_rate = 0.75

    # b has error_rate = 0.0, should be tried first
    resp = pool.complete([])
    assert resp.provider == "b"
    assert b._calls == 1
    assert a._calls == 0


def test_pool_exhausted_provider_skipped():
    a = _StubAdapter("a", [_ok("a")])
    b = _StubAdapter("b", [_ok("b")])

    pool = ProviderFallbackPool()
    pool.add(a, "a", "a")
    pool.add(b, "b", "b")

    # Mark a as exhausted
    pool._entries[0].stats.exhausted_until = time.time() + 3600

    resp = pool.complete([])
    assert resp.provider == "b"
    assert a._calls == 0


def test_pool_exhausted_429_sets_cooldown():
    pool = ProviderFallbackPool()
    pool.add(_StubAdapter("a", [ModelError("429 rate limit")]), "a", "a")
    pool.add(_StubAdapter("b", [_ok("b")]), "b", "b")

    pool.complete([])

    stats = pool.stats()
    assert stats["a"]["exhausted_until"] is not None
    assert stats["a"]["exhausted_until"] > time.time()
    assert stats["a"]["error_count"] == 1


def test_pool_success_increments_counter():
    pool = ProviderFallbackPool()
    pool.add(_StubAdapter("a", [_ok("a"), _ok("a")]), "a", "a")

    pool.complete([])
    pool.complete([])

    assert pool.stats()["a"]["success_count"] == 2
    assert pool.stats()["a"]["error_count"] == 0
    assert pool.stats()["a"]["error_rate"] == 0.0


def test_pool_error_rate_computed_correctly():
    """After a fails once, b takes over — verify error_rate tracking is correct."""
    pool = ProviderFallbackPool()
    a_stub = _StubAdapter("a", [_ok("a"), ModelError("x")])
    b_stub = _StubAdapter("b", [_ok("b"), _ok("b")])
    pool.add(a_stub, "a", "a")
    pool.add(b_stub, "b", "b")

    # Round 1: a and b both at error_rate=0 → a goes first, succeeds
    pool.complete([])
    assert pool.stats()["a"]["success_count"] == 1
    assert pool.stats()["a"]["error_count"] == 0

    # Round 2: still equal error_rate, a tried first, fails → b picks up
    pool.complete([])
    assert pool.stats()["a"]["error_count"] == 1
    assert pool.stats()["b"]["success_count"] == 1
    assert round(pool.stats()["a"]["error_rate"], 2) == 0.5

    # Round 3: b.error_rate=0.0 < a.error_rate=0.5 → b goes first
    pool.complete([])
    assert b_stub._calls == 2
    assert a_stub._calls == 2  # a not called again this round


def test_pool_stats_structure():
    pool = ProviderFallbackPool()
    pool.add(_StubAdapter("a", []), "a", "my-key")
    s = pool.stats()
    assert "my-key" in s
    assert "error_rate" in s["my-key"]
    assert "available" in s["my-key"]
    assert "success_count" in s["my-key"]


def test_pool_providers_list():
    pool = ProviderFallbackPool()
    pool.add(_StubAdapter("x", []), "xai", "x")
    pool.add(_StubAdapter("g", []), "gemini", "g")
    assert pool.providers() == ["xai", "gemini"]


def test_pool_all_exhausted_raises():
    pool = ProviderFallbackPool()
    pool.add(_StubAdapter("a", []), "a", "a")
    pool._entries[0].stats.exhausted_until = time.time() + 3600

    raised = False
    try:
        pool.complete([])
    except ModelError as e:
        raised = True
        assert "exhausted" in str(e)
    assert raised


# ── CredentialPool error_rate extension tests ─────────────────────────────────

def test_credential_error_rate_zero_by_default():
    pool = CredentialPool()
    pool.add("openai", "key-1", label="k1")
    cred = pool.acquire("openai")
    assert cred.error_rate == 0.0


def test_credential_mark_success_increments():
    pool = CredentialPool()
    pool.add("openai", "key-1", label="k1")
    cred = pool.acquire("openai")
    pool.mark_success("openai", cred.id)
    cred2 = pool.acquire("openai")
    assert cred2.success_count == 1


def test_credential_mark_error_increments():
    pool = CredentialPool()
    pool.add("openai", "key-1", label="k1")
    cred = pool.acquire("openai")
    pool.mark_error("openai", cred.id)
    cred2 = pool.acquire("openai")
    assert cred2.error_count == 1
    assert cred2.status != STATUS_EXHAUSTED


def test_credential_mark_error_exhausting():
    pool = CredentialPool()
    pool.add("openai", "key-1", label="k1")
    cred = pool.acquire("openai")
    pool.mark_error("openai", cred.id, exhausting=True, error_code=429)
    cred2 = pool._find("openai", cred.id)
    assert cred2.status == STATUS_EXHAUSTED
    assert cred2.error_count == 1


def test_strategy_lowest_error_rate_picks_best():
    pool = CredentialPool(strategy=STRATEGY_LOWEST_ERROR_RATE)
    pool.add("openai", "key-bad",  label="bad")
    pool.add("openai", "key-good", label="good")

    bad_id  = pool._pools["openai"][0].id
    good_id = pool._pools["openai"][1].id

    # Give bad key a 50% error rate
    pool._pools["openai"][0].success_count = 1
    pool._pools["openai"][0].error_count   = 1

    cred = pool.acquire("openai")
    assert cred.id == good_id


def test_strategy_lowest_error_rate_tiebreak_by_requests():
    """When error_rates are equal, prefer key with fewer requests."""
    pool = CredentialPool(strategy=STRATEGY_LOWEST_ERROR_RATE)
    pool.add("openai", "key-heavy", label="heavy")
    pool.add("openai", "key-light", label="light")

    pool._pools["openai"][0].request_count = 100
    pool._pools["openai"][1].request_count = 5

    cred = pool.acquire("openai")
    assert cred.label == "light"


def main() -> int:
    tests = [
        test_pool_first_provider_success,
        test_pool_fallback_on_error,
        test_pool_all_fail_raises,
        test_pool_error_rate_routing,
        test_pool_exhausted_provider_skipped,
        test_pool_exhausted_429_sets_cooldown,
        test_pool_success_increments_counter,
        test_pool_error_rate_computed_correctly,
        test_pool_stats_structure,
        test_pool_providers_list,
        test_pool_all_exhausted_raises,
        test_credential_error_rate_zero_by_default,
        test_credential_mark_success_increments,
        test_credential_mark_error_increments,
        test_credential_mark_error_exhausting,
        test_strategy_lowest_error_rate_picks_best,
        test_strategy_lowest_error_rate_tiebreak_by_requests,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"[OK] {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(main())
