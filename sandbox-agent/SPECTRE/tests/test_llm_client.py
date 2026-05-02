"""SPECTRE LLMClient tests — v2 sandbox (multi-tier 4-backends).

L1: VLLMClient.chat() calls /v1/chat/completions with correct payload
L2: VLLMClient.health() returns True on 200, False on connection error
L3: MultiTierLLMClient tries T1 first, returns on success (no T2 call)
L4: MultiTierLLMClient falls through to T2 when T1 fails
L5: MultiTierLLMClient falls through all 4 tiers → raises LLMUnavailable
L6: FallbackLLMClient(primary, secondary) still works (backward compat)
L7: make_four_tier_client() returns MultiTierLLMClient with 4 backends
L8: health_all() returns per-tier status dict
L9: MultiTierLLMClient with single backend — succeeds on T1, no loop
L10: make_client("vllm") returns VLLMClient instance
"""
from __future__ import annotations

import asyncio
import sys
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))

from llm_client import (
    ClaudeClient,
    FallbackLLMClient,
    LLMClient,
    LLMUnavailable,
    MultiTierLLMClient,
    OllamaClient,
    VLLMClient,
    make_client,
    make_fallback_client,
    make_four_tier_client,
)

_pass = 0
_fail = 0


def test(name: str, fn) -> None:
    global _pass, _fail
    num = _pass + _fail + 1
    try:
        fn()
        print(f"  ✅ L{num}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ L{num}: {name} — {e}")
        _fail += 1


print("\n=== TEST LLM CLIENT v2 ===")


# ─────────────────────────────────────────────────────────────────────────────
# L1: VLLMClient.chat() sends correct OpenAI-compatible payload
# ─────────────────────────────────────────────────────────────────────────────

def l1_vllm_chat_payload():
    client = VLLMClient(base_url="http://localhost:8000", model="test-model")
    captured = []

    async def fake_post(url, **kwargs):
        captured.append({"url": url, "json": kwargs.get("json", {})})
        resp = mock.MagicMock()
        resp.raise_for_status = mock.MagicMock()
        resp.json.return_value = {
            "choices": [{"message": {"content": "vllm response"}}]
        }
        return resp

    async def run():
        with mock.patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__ = mock.AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = mock.AsyncMock(return_value=False)
            mock_ctx.post = mock.AsyncMock(side_effect=fake_post)
            mock_client_cls.return_value = mock_ctx
            result = await client.chat([{"role": "user", "content": "hello"}], max_tokens=256)
        return result

    result = asyncio.run(run())
    assert result == "vllm response", f"Unexpected result: {result}"
    assert len(captured) == 1
    assert "/v1/chat/completions" in captured[0]["url"]
    assert captured[0]["json"]["model"] == "test-model"
    assert captured[0]["json"]["max_tokens"] == 256
    assert captured[0]["json"]["stream"] is False

test("VLLMClient.chat() → correct OpenAI payload + response", l1_vllm_chat_payload)


# ─────────────────────────────────────────────────────────────────────────────
# L2: VLLMClient.health() True on 200, False on connection error
# ─────────────────────────────────────────────────────────────────────────────

def l2_vllm_health():
    client = VLLMClient(base_url="http://localhost:8000")

    async def run_healthy():
        with mock.patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__ = mock.AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = mock.AsyncMock(return_value=False)
            mock_ctx.get = mock.AsyncMock(return_value=mock.MagicMock(status_code=200))
            mock_cls.return_value = mock_ctx
            return await client.health()

    async def run_error():
        with mock.patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = mock.AsyncMock()
            mock_ctx.__aenter__ = mock.AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = mock.AsyncMock(return_value=False)
            mock_ctx.get = mock.AsyncMock(side_effect=ConnectionError("refused"))
            mock_cls.return_value = mock_ctx
            return await client.health()

    assert asyncio.run(run_healthy()) is True
    assert asyncio.run(run_error()) is False

test("VLLMClient.health(): True on 200, False on connection error", l2_vllm_health)


# ─────────────────────────────────────────────────────────────────────────────
# L3: MultiTierLLMClient — T1 succeeds → T2 never called
# ─────────────────────────────────────────────────────────────────────────────

def l3_multi_tier_t1_success():
    t1 = mock.AsyncMock(spec=LLMClient)
    t1.backend_name = "mock_t1"
    t1.chat = mock.AsyncMock(return_value="t1 response")

    t2 = mock.AsyncMock(spec=LLMClient)
    t2.backend_name = "mock_t2"
    t2.chat = mock.AsyncMock(return_value="t2 response")

    client = MultiTierLLMClient(t1, t2)

    async def run():
        return await client.chat([{"role": "user", "content": "hi"}])

    result = asyncio.run(run())
    assert result == "t1 response", f"Expected t1 response, got: {result}"
    t1.chat.assert_called_once()
    t2.chat.assert_not_called()

test("MultiTierLLMClient: T1 succeeds → T2 never called", l3_multi_tier_t1_success)


# ─────────────────────────────────────────────────────────────────────────────
# L4: MultiTierLLMClient — T1 fails → T2 succeeds
# ─────────────────────────────────────────────────────────────────────────────

def l4_multi_tier_t1_fail_t2_success():
    t1 = mock.AsyncMock(spec=LLMClient)
    t1.backend_name = "mock_t1"
    t1.chat = mock.AsyncMock(side_effect=Exception("t1 down"))

    t2 = mock.AsyncMock(spec=LLMClient)
    t2.backend_name = "mock_t2"
    t2.chat = mock.AsyncMock(return_value="t2 saved it")

    t3 = mock.AsyncMock(spec=LLMClient)
    t3.backend_name = "mock_t3"

    client = MultiTierLLMClient(t1, t2, t3)

    async def run():
        return await client.chat([{"role": "user", "content": "hi"}])

    result = asyncio.run(run())
    assert result == "t2 saved it", f"Expected t2 response, got: {result}"
    t1.chat.assert_called_once()
    t2.chat.assert_called_once()
    t3.chat.assert_not_called()

test("MultiTierLLMClient: T1 fails → T2 succeeds (T3 not called)", l4_multi_tier_t1_fail_t2_success)


# ─────────────────────────────────────────────────────────────────────────────
# L5: MultiTierLLMClient — all 4 tiers fail → LLMUnavailable raised
# ─────────────────────────────────────────────────────────────────────────────

def l5_all_tiers_fail():
    tiers = []
    for i in range(4):
        t = mock.AsyncMock(spec=LLMClient)
        t.backend_name = f"mock_t{i+1}"
        t.chat = mock.AsyncMock(side_effect=Exception(f"tier{i+1} down"))
        tiers.append(t)

    client = MultiTierLLMClient(*tiers)

    async def run():
        return await client.chat([{"role": "user", "content": "hi"}])

    raised = False
    try:
        asyncio.run(run())
    except LLMUnavailable as e:
        raised = True
        assert "4 LLM tiers exhausted" in str(e), f"Unexpected error message: {e}"

    assert raised, "Expected LLMUnavailable to be raised"
    for t in tiers:
        t.chat.assert_called_once()

test("MultiTierLLMClient: all 4 tiers fail → LLMUnavailable", l5_all_tiers_fail)


# ─────────────────────────────────────────────────────────────────────────────
# L6: FallbackLLMClient backward compat — still works as 2-backend chain
# ─────────────────────────────────────────────────────────────────────────────

def l6_fallback_backward_compat():
    primary = mock.AsyncMock(spec=LLMClient)
    primary.backend_name = "primary"
    primary.chat = mock.AsyncMock(side_effect=Exception("primary down"))

    secondary = mock.AsyncMock(spec=LLMClient)
    secondary.backend_name = "secondary"
    secondary.chat = mock.AsyncMock(return_value="secondary saved it")

    client = FallbackLLMClient(primary, secondary)
    assert isinstance(client, MultiTierLLMClient), "FallbackLLMClient must be MultiTierLLMClient subclass"
    assert client.tier_count == 2

    async def run():
        return await client.chat([{"role": "user", "content": "test"}])

    result = asyncio.run(run())
    assert result == "secondary saved it"

test("FallbackLLMClient(primary, secondary) — backward compat, 2-tier chain", l6_fallback_backward_compat)


# ─────────────────────────────────────────────────────────────────────────────
# L7: make_four_tier_client() returns 4-tier MultiTierLLMClient
# ─────────────────────────────────────────────────────────────────────────────

def l7_make_four_tier():
    client = make_four_tier_client()
    assert isinstance(client, MultiTierLLMClient), "Must be MultiTierLLMClient"
    assert client.tier_count == 4, f"Expected 4 tiers, got {client.tier_count}"

    names = client.backend_name.split("→")
    assert len(names) == 4

    tiers = client._backends
    assert isinstance(tiers[0], OllamaClient), f"T1 must be OllamaClient, got {type(tiers[0])}"
    assert isinstance(tiers[1], VLLMClient), f"T2 must be VLLMClient, got {type(tiers[1])}"
    assert isinstance(tiers[2], ClaudeClient), f"T3 must be ClaudeClient, got {type(tiers[2])}"
    assert isinstance(tiers[3], ClaudeClient), f"T4 must be ClaudeClient, got {type(tiers[3])}"

    # T3 must be haiku, T4 must be sonnet
    assert "haiku" in tiers[2].backend_name, f"T3 should be Haiku, got {tiers[2].backend_name}"
    assert "sonnet" in tiers[3].backend_name, f"T4 should be Sonnet, got {tiers[3].backend_name}"

test("make_four_tier_client(): 4 tiers Ollama→VLLM→Haiku→Sonnet", l7_make_four_tier)


# ─────────────────────────────────────────────────────────────────────────────
# L8: health_all() returns per-tier health dict
# ─────────────────────────────────────────────────────────────────────────────

def l8_health_all():
    t1 = mock.AsyncMock(spec=LLMClient)
    t1.backend_name = "local/fast"
    t1.health = mock.AsyncMock(return_value=True)

    t2 = mock.AsyncMock(spec=LLMClient)
    t2.backend_name = "local/deep"
    t2.health = mock.AsyncMock(return_value=False)

    t3 = mock.AsyncMock(spec=LLMClient)
    t3.backend_name = "cloud/haiku"
    t3.health = mock.AsyncMock(return_value=True)

    client = MultiTierLLMClient(t1, t2, t3)

    async def run():
        return await client.health_all()

    result = asyncio.run(run())
    assert result == {
        "local/fast": True,
        "local/deep": False,
        "cloud/haiku": True,
    }, f"Unexpected health_all result: {result}"

test("health_all(): per-tier status dict with correct True/False per backend", l8_health_all)


# ─────────────────────────────────────────────────────────────────────────────
# L9: Single-backend MultiTierLLMClient works (no loop overhead)
# ─────────────────────────────────────────────────────────────────────────────

def l9_single_backend():
    t1 = mock.AsyncMock(spec=LLMClient)
    t1.backend_name = "solo"
    t1.chat = mock.AsyncMock(return_value="solo answer")

    client = MultiTierLLMClient(t1)
    assert client.tier_count == 1

    async def run():
        return await client.chat([{"role": "user", "content": "hi"}])

    result = asyncio.run(run())
    assert result == "solo answer"
    t1.chat.assert_called_once()

test("MultiTierLLMClient single-backend: works without fallback overhead", l9_single_backend)


# ─────────────────────────────────────────────────────────────────────────────
# L10: make_client("vllm") returns VLLMClient
# ─────────────────────────────────────────────────────────────────────────────

def l10_make_client_vllm():
    client = make_client("vllm")
    assert isinstance(client, VLLMClient), f"Expected VLLMClient, got {type(client)}"
    assert "vllm" in client.backend_name

    client_ollama = make_client("ollama")
    assert isinstance(client_ollama, OllamaClient)

    client_claude = make_client("claude")
    assert isinstance(client_claude, ClaudeClient)

    try:
        make_client("unknown_backend")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "unknown_backend" in str(e)

test("make_client('vllm') → VLLMClient; invalid → ValueError", l10_make_client_vllm)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"LLM Client v2 Tests: {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
