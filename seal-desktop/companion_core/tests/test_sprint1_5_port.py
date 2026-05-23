"""Tests for SEAL App Sprint 1.5 backend endpoints (JARVIS — P1/P2/P3/P5).

Covers:
- Memory Tree (P1)
- Connections / Add Account (P2)
- Screen Awareness status (P3)
- TokenJuice rules + compact (P5)
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport
from companion_core.main import app


# ─── P1 — Memory Tree ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_memory_tree_empty_initial():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/memory-tree?agent=SOUL&level=day")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["agent"] == "SOUL"
        assert d["level"] == "day"
        assert d["count"] == 0


@pytest.mark.asyncio
async def test_memory_tree_rejects_unknown_level():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/memory-tree?agent=SOUL&level=decade")
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_memory_tree_rejects_unknown_agent():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/memory-tree?agent=HACKER&level=day")
        assert r.status_code == 400


# ─── P2 — Connections ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_connections_list_includes_known_connectors():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/connections")
        assert r.status_code == 200
        d = r.json()
        connector_ids = {c["id"] for c in d["connectors"]}
        for required in ["gmail", "gcal", "gdrive", "github", "notion"]:
            assert required in connector_ids, f"missing {required}"


@pytest.mark.asyncio
async def test_connections_initial_state_all_disconnected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/connections")
        d = r.json()
        for c in d["connectors"]:
            assert c["connected"] is False, f"{c['id']} should be disconnected initially"
            assert c["status"] == "available"


@pytest.mark.asyncio
async def test_connections_add_and_remove():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        add = await client.post("/api/connections/add", json={"connector_id": "gmail"})
        assert add.status_code == 200
        assert add.json()["connected"] is True

        listed = await client.get("/api/connections")
        gmail = next(c for c in listed.json()["connectors"] if c["id"] == "gmail")
        assert gmail["connected"] is True
        assert gmail["status"] == "connected"

        rm = await client.delete("/api/connections/gmail")
        assert rm.status_code == 200
        assert rm.json()["connected"] is False


@pytest.mark.asyncio
async def test_connections_add_rejects_unknown_connector():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/connections/add", json={"connector_id": "evil_corp"})
        assert r.status_code == 400


# ─── P3 — Screen Awareness ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_screen_status_returns_capabilities():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/screen/status")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert "capture_available" in d
        assert "analyzer_available" in d
        assert "permission_note" in d  # user-friendly explanation present


# ─── P5 — TokenJuice ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tokenjuice_rules_listed():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/tokenjuice/rules")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["count"] > 0
        for rule in d["rules"]:
            assert "label" in rule  # user-friendly label present


@pytest.mark.asyncio
async def test_tokenjuice_compact_removes_ansi_codes():
    """ANSI color codes should be stripped."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"text": "\x1b[31merror\x1b[0m: something went wrong\x1b[0m"}
        r = await client.post("/api/tokenjuice/compact", json=payload)
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert "\x1b" not in d["output"]
        assert d["output_chars"] < d["input_chars"]


@pytest.mark.asyncio
async def test_tokenjuice_compact_empty_input():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/tokenjuice/compact", json={"text": ""})
        assert r.status_code == 200
        d = r.json()
        assert d["input_chars"] == 0
        assert d["output_chars"] == 0
