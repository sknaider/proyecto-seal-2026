"""Tests for external memory provider adapters — Mem0, Honcho, Hindsight, Pool."""

import json
import sys
import pathlib
import urllib.error
import urllib.request
from io import BytesIO
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.memory.external_providers import (
    ExternalMemoryProvider, MemoryItem, MemoryProviderPool,
    Mem0Adapter, HonchoAdapter, HindsightAdapter, ProviderError,
)


# ── HTTP mock helpers ─────────────────────────────────────────────────────────

def _mock_response(body: dict, status: int = 200):
    raw = json.dumps(body).encode()
    resp = MagicMock()
    resp.read.return_value = raw
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_error(status: int, body: dict = {}):
    raw = json.dumps(body).encode()
    return urllib.error.HTTPError(
        url="http://x", code=status, msg="err",
        hdrs=None, fp=BytesIO(raw),
    )


# ── MemoryItem ────────────────────────────────────────────────────────────────

def test_memory_item_to_dict():
    m = MemoryItem(id="1", content="hello", agent="ADA", score=0.9)
    d = m.to_dict()
    assert d["id"] == "1"
    assert d["content"] == "hello"
    assert d["score"] == 0.9


# ── Mem0Adapter ───────────────────────────────────────────────────────────────

def test_mem0_available_with_key():
    a = Mem0Adapter(api_key="m0-test")
    assert a.available() is True


def test_mem0_not_available_without_key():
    a = Mem0Adapter(api_key="")
    assert a.available() is False


def test_mem0_store_returns_id():
    a = Mem0Adapter(api_key="m0-key")
    resp_body = {"results": [{"id": "abc123", "memory": "test"}]}
    with patch("urllib.request.urlopen", return_value=_mock_response(resp_body)):
        mid = a.store("test memory", agent="ADA")
    assert mid == "abc123"


def test_mem0_store_sends_correct_payload():
    a = Mem0Adapter(api_key="m0-key")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        captured["url"] = req.full_url
        return _mock_response({"results": [{"id": "x"}]})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        a.store("hello ADA", agent="ADA")

    assert captured["body"]["user_id"] == "ADA"
    assert captured["body"]["messages"][0]["content"] == "hello ADA"
    assert "/memories/" in captured["url"]


def test_mem0_search_returns_items():
    a = Mem0Adapter(api_key="m0-key")
    resp_body = [
        {"id": "1", "memory": "first result", "user_id": "ADA", "score": 0.95},
        {"id": "2", "memory": "second result", "user_id": "ADA", "score": 0.80},
    ]
    with patch("urllib.request.urlopen", return_value=_mock_response(resp_body)):
        results = a.search("query", agent="ADA")
    assert len(results) == 2
    assert results[0].score == 0.95
    assert results[0].content == "first result"


def test_mem0_search_empty_results():
    a = Mem0Adapter(api_key="m0-key")
    with patch("urllib.request.urlopen", return_value=_mock_response([])):
        results = a.search("nothing")
    assert results == []


def test_mem0_delete_returns_true_on_success():
    a = Mem0Adapter(api_key="m0-key")
    with patch("urllib.request.urlopen", return_value=_mock_response({})):
        assert a.delete("mem-1") is True


def test_mem0_delete_returns_false_on_error():
    a = Mem0Adapter(api_key="m0-key")
    with patch("urllib.request.urlopen", side_effect=_http_error(404)):
        assert a.delete("bad-id") is False


# ── HonchoAdapter ─────────────────────────────────────────────────────────────

def test_honcho_available_with_key():
    a = HonchoAdapter(app_id="seal", api_key="hk-test")
    assert a.available() is True


def test_honcho_not_available_without_key():
    a = HonchoAdapter(app_id="seal", api_key="")
    assert a.available() is False


def test_honcho_store_returns_id():
    a = HonchoAdapter(app_id="seal", api_key="hk-key")
    with patch("urllib.request.urlopen", return_value=_mock_response({"id": "h-42"})):
        mid = a.store("context stored", agent="JARVIS")
    assert mid == "h-42"


def test_honcho_store_sends_to_correct_url():
    a = HonchoAdapter(app_id="seal", api_key="hk-key")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _mock_response({"id": "x"})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        a.store("fact", agent="ADA")

    assert "/apps/seal/users/ADA/" in captured["url"]


def test_honcho_search_returns_items():
    a = HonchoAdapter(app_id="seal", api_key="hk-key")
    resp = {"items": [
        {"id": "h1", "content": "memory", "score": 0.7},
    ]}
    with patch("urllib.request.urlopen", return_value=_mock_response(resp)):
        results = a.search("query", agent="ADA")
    assert len(results) == 1
    assert results[0].id == "h1"


def test_honcho_delete_returns_true():
    a = HonchoAdapter(app_id="seal", api_key="hk-key")
    with patch("urllib.request.urlopen", return_value=_mock_response({})):
        assert a.delete("h-1") is True


# ── HindsightAdapter ──────────────────────────────────────────────────────────

def test_hindsight_available_with_key():
    a = HindsightAdapter(api_key="hs-test")
    assert a.available() is True


def test_hindsight_store_returns_id():
    a = HindsightAdapter(api_key="hs-key")
    with patch("urllib.request.urlopen", return_value=_mock_response({"id": "hs-99"})):
        mid = a.store("insight", agent="ALICE")
    assert mid == "hs-99"


def test_hindsight_search_with_list_response():
    a = HindsightAdapter(api_key="hs-key")
    resp = [{"id": "r1", "content": "result", "agent": "ALICE", "score": 0.6}]
    with patch("urllib.request.urlopen", return_value=_mock_response(resp)):
        results = a.search("insight", agent="ALICE")
    assert len(results) == 1
    assert results[0].agent == "ALICE"


def test_hindsight_search_includes_agent_param():
    a = HindsightAdapter(api_key="hs-key", base_url="http://test")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _mock_response([])

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        a.search("query", agent="NEXUS")

    assert "agent=NEXUS" in captured["url"]


def test_hindsight_delete_returns_false_on_error():
    a = HindsightAdapter(api_key="hs-key")
    with patch("urllib.request.urlopen", side_effect=_http_error(500)):
        assert a.delete("bad") is False


# ── MemoryProviderPool ────────────────────────────────────────────────────────

def _make_pool(store_id="pool-id", search_items=None, delete_ok=True):
    p = MagicMock(spec=ExternalMemoryProvider)
    p.available.return_value = True
    p.store.return_value = store_id
    p.search.return_value = search_items or []
    p.delete.return_value = delete_ok
    return p


def test_pool_available_if_any_available():
    p1 = MagicMock(spec=ExternalMemoryProvider)
    p1.available.return_value = False
    p2 = _make_pool()
    pool = MemoryProviderPool([p1, p2])
    assert pool.available() is True


def test_pool_not_available_if_none_available():
    p1 = MagicMock(spec=ExternalMemoryProvider)
    p1.available.return_value = False
    pool = MemoryProviderPool([p1])
    assert pool.available() is False


def test_pool_store_uses_first_available():
    p1 = MagicMock(spec=ExternalMemoryProvider)
    p1.available.return_value = False
    p2 = _make_pool(store_id="from-p2")
    pool = MemoryProviderPool([p1, p2])
    mid = pool.store("content", agent="ADA")
    assert mid == "from-p2"
    p1.store.assert_not_called()


def test_pool_store_falls_back_on_error():
    p1 = _make_pool()
    p1.store.side_effect = ProviderError("p1 down")
    p2 = _make_pool(store_id="fallback-id")
    pool = MemoryProviderPool([p1, p2])
    mid = pool.store("content", agent="ADA")
    assert mid == "fallback-id"


def test_pool_store_raises_when_all_fail():
    p1 = _make_pool()
    p1.store.side_effect = ProviderError("p1 down")
    p2 = _make_pool()
    p2.store.side_effect = ProviderError("p2 down")
    pool = MemoryProviderPool([p1, p2])
    raised = False
    try:
        pool.store("content", agent="ADA")
    except ProviderError:
        raised = True
    assert raised


def test_pool_search_returns_first_success():
    items = [MemoryItem(id="1", content="x", agent="ADA")]
    p1 = _make_pool(search_items=items)
    p2 = _make_pool(search_items=[])
    pool = MemoryProviderPool([p1, p2])
    results = pool.search("query")
    assert len(results) == 1
    p2.search.assert_not_called()


def test_pool_search_falls_back_on_error():
    items = [MemoryItem(id="2", content="y", agent="JARVIS")]
    p1 = _make_pool()
    p1.search.side_effect = ProviderError("down")
    p2 = _make_pool(search_items=items)
    pool = MemoryProviderPool([p1, p2])
    results = pool.search("query")
    assert len(results) == 1


def test_pool_search_returns_empty_when_all_fail():
    p1 = _make_pool()
    p1.search.side_effect = ProviderError("down")
    pool = MemoryProviderPool([p1])
    assert pool.search("q") == []


def test_pool_available_providers_lists_names():
    p1 = Mem0Adapter(api_key="m0-key")
    p2 = HonchoAdapter(app_id="x", api_key="")
    pool = MemoryProviderPool([p1, p2])
    names = pool.available_providers()
    assert "Mem0Adapter" in names
    assert "HonchoAdapter" not in names


def test_pool_from_env_empty_when_no_keys():
    import os
    env_backup = {}
    for k in ("MEM0_API_KEY", "HONCHO_API_KEY", "HINDSIGHT_API_KEY"):
        env_backup[k] = os.environ.pop(k, None)
    try:
        pool = MemoryProviderPool.from_env()
        assert pool.available() is False
    finally:
        for k, v in env_backup.items():
            if v is not None:
                os.environ[k] = v


def test_pool_from_env_includes_configured_providers():
    import os
    os.environ["MEM0_API_KEY"] = "m0-test"
    try:
        pool = MemoryProviderPool.from_env()
        names = pool.available_providers()
        assert "Mem0Adapter" in names
    finally:
        del os.environ["MEM0_API_KEY"]


# ── Runner ────────────────────────────────────────────────────────────────────

def main() -> int:
    tests = [
        test_memory_item_to_dict,
        test_mem0_available_with_key,
        test_mem0_not_available_without_key,
        test_mem0_store_returns_id,
        test_mem0_store_sends_correct_payload,
        test_mem0_search_returns_items,
        test_mem0_search_empty_results,
        test_mem0_delete_returns_true_on_success,
        test_mem0_delete_returns_false_on_error,
        test_honcho_available_with_key,
        test_honcho_not_available_without_key,
        test_honcho_store_returns_id,
        test_honcho_store_sends_to_correct_url,
        test_honcho_search_returns_items,
        test_honcho_delete_returns_true,
        test_hindsight_available_with_key,
        test_hindsight_store_returns_id,
        test_hindsight_search_with_list_response,
        test_hindsight_search_includes_agent_param,
        test_hindsight_delete_returns_false_on_error,
        test_pool_available_if_any_available,
        test_pool_not_available_if_none_available,
        test_pool_store_uses_first_available,
        test_pool_store_falls_back_on_error,
        test_pool_store_raises_when_all_fail,
        test_pool_search_returns_first_success,
        test_pool_search_falls_back_on_error,
        test_pool_search_returns_empty_when_all_fail,
        test_pool_available_providers_lists_names,
        test_pool_from_env_empty_when_no_keys,
        test_pool_from_env_includes_configured_providers,
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
