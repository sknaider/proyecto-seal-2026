"""SPECTRE hippocampus D2 — 7 unit tests.

Tests cubren: store_episode, retrieve_by_hash, retrieve_by_date,
episode_exists, hash_from_context. Mocks asyncpg — no DB real.
"""
from __future__ import annotations

import asyncio
import sys
import unittest.mock as mock
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))

import hippocampus as hip
from episodic_api import compute_context_hash, extract_keywords

_pass = 0
_fail = 0


def test(name: str, fn) -> None:
    global _pass, _fail
    try:
        fn()
        print(f"  ✅ H{_pass + _fail + 1}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ H{_pass + _fail + 1}: {name} — {e}")
        _fail += 1


# ─────────────────────────────────────────────────────────────────────────────
# H1 — hash_from_context: mismo texto → mismo hash determinístico
# ─────────────────────────────────────────────────────────────────────────────

def h1_hash_deterministic():
    text = "SPECTRE processing episodic memory recall hippocampus sandbox"
    h1 = asyncio.run(hip.hash_from_context(["ADA", "JARVIS"], text, hour_bucket=8))
    h2 = asyncio.run(hip.hash_from_context(["ADA", "JARVIS"], text, hour_bucket=8))
    assert h1 == h2, f"Hash not deterministic: {h1} vs {h2}"
    assert len(h1) == 8, f"Hash should be 8 hex chars, got {len(h1)}"

test("hash_from_context: determinístico, 8 hex chars", h1_hash_deterministic)


# ─────────────────────────────────────────────────────────────────────────────
# H2 — hash_from_context: diferentes contextos → diferentes hashes
# ─────────────────────────────────────────────────────────────────────────────

def h2_different_context_different_hash():
    h1 = asyncio.run(hip.hash_from_context(["ADA"], "kernel soul heartbeat boot", hour_bucket=0))
    h2 = asyncio.run(hip.hash_from_context(["ADA"], "spectre episodic memory retrieval", hour_bucket=0))
    assert h1 != h2, "Different contexts must produce different hashes"

test("hash_from_context: contextos distintos → hashes distintos", h2_different_context_different_hash)


# ─────────────────────────────────────────────────────────────────────────────
# H3 — store_episode: llama episodic_index correctamente
# ─────────────────────────────────────────────────────────────────────────────

def h3_store_episode_calls_episodic_index():
    calls: list[dict] = []

    async def fake_episodic_index(agent, memory_id, context, participants=None, timestamp_bucket=None):
        calls.append({"agent": agent, "memory_id": memory_id, "context": context})
        return 42

    with mock.patch("hippocampus.episodic_index", side_effect=fake_episodic_index):
        row_id = asyncio.run(hip.store_episode("ADA", 1001, "kernel boot validation", ["ADA", "JARVIS"]))

    assert row_id == 42
    assert len(calls) == 1
    assert calls[0]["agent"] == "ADA"
    assert calls[0]["memory_id"] == 1001

test("store_episode: delega a episodic_index con parámetros correctos", h3_store_episode_calls_episodic_index)


# ─────────────────────────────────────────────────────────────────────────────
# H4 — retrieve_by_hash: sin content → retorna solo memory_ids
# ─────────────────────────────────────────────────────────────────────────────

def h4_retrieve_by_hash_ids_only():
    fake_ids = [100, 200, 300]

    async def fake_lookup(agent, context_hash, days_back):
        return fake_ids

    with mock.patch("hippocampus.episodic_lookup", side_effect=fake_lookup):
        results = asyncio.run(hip.retrieve_by_hash("ADA", "deadbeef", days_back=7, with_content=False))

    assert len(results) == 3
    assert all("memory_id" in r for r in results)
    assert [r["memory_id"] for r in results] == fake_ids
    assert "content" not in results[0], "with_content=False should not fetch content"

test("retrieve_by_hash: with_content=False → solo memory_ids, sin JOIN", h4_retrieve_by_hash_ids_only)


# ─────────────────────────────────────────────────────────────────────────────
# H5 — retrieve_by_hash: con content → enriquece con datos de soul_v3.memories
# ─────────────────────────────────────────────────────────────────────────────

def h5_retrieve_by_hash_with_content():
    fake_ids = [777]
    fake_content = {777: {"id": 777, "content": "boot context ADA", "agent": "ADA",
                          "importance": 4, "created_at": None}}

    async def fake_lookup(agent, context_hash, days_back):
        return fake_ids

    async def fake_conn():
        conn = mock.AsyncMock()
        conn.fetch.return_value = [
            {"id": 777, "content": "boot context ADA", "agent": "ADA",
             "importance": 4, "created_at": None}
        ]
        return conn

    with mock.patch("hippocampus.episodic_lookup", side_effect=fake_lookup):
        with mock.patch("hippocampus._get_conn", side_effect=fake_conn):
            results = asyncio.run(hip.retrieve_by_hash("ADA", "deadbeef", days_back=7, with_content=True))

    assert len(results) == 1
    assert results[0]["memory_id"] == 777
    assert results[0].get("content") == "boot context ADA"

test("retrieve_by_hash: with_content=True → enriquece con content de memories", h5_retrieve_by_hash_with_content)


# ─────────────────────────────────────────────────────────────────────────────
# H6 — episode_exists: retorna True cuando hay IDs, False cuando vacío
# ─────────────────────────────────────────────────────────────────────────────

def h6_episode_exists():
    async def lookup_with_results(agent, ctx, days):
        return [1, 2, 3]

    async def lookup_empty(agent, ctx, days):
        return []

    with mock.patch("hippocampus.episodic_lookup", side_effect=lookup_with_results):
        exists = asyncio.run(hip.episode_exists("ADA", "cafebabe", days_back=3))
    assert exists is True

    with mock.patch("hippocampus.episodic_lookup", side_effect=lookup_empty):
        not_exists = asyncio.run(hip.episode_exists("ADA", "00000000", days_back=3))
    assert not_exists is False

test("episode_exists: True si hay IDs, False si vacío", h6_episode_exists)


# ─────────────────────────────────────────────────────────────────────────────
# H7 — retrieve_by_date: filtra por rango de fechas correctamente
# ─────────────────────────────────────────────────────────────────────────────

def h7_retrieve_by_date():
    today = date.today()
    three_days_ago = today - timedelta(days=3)
    fake_rows = [
        {"id": 1, "agent": "ADA", "timestamp_bucket": today,
         "context_hash": "aabbccdd", "memory_id": 500},
        {"id": 2, "agent": "ADA", "timestamp_bucket": three_days_ago,
         "context_hash": "11223344", "memory_id": 501},
    ]

    async def fake_conn():
        conn = mock.AsyncMock()
        conn.fetch.return_value = [mock.MagicMock(**{**r, "__iter__": lambda self: iter(r.items()),
                                                     "keys": lambda: r.keys()}) for r in fake_rows]
        conn.fetch.return_value = fake_rows
        return conn

    with mock.patch("hippocampus._get_conn", side_effect=fake_conn):
        results = asyncio.run(hip.retrieve_by_date("ADA", three_days_ago, today))

    assert len(results) == 2
    assert results[0]["memory_id"] == 500
    assert results[1]["memory_id"] == 501

test("retrieve_by_date: retorna entradas en rango de fechas", h7_retrieve_by_date)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Hippocampus D2 Tests: {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
