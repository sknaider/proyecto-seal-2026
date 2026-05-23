"""Tests for SEAL App Sprint 1.5 backend endpoints (JARVIS — P1/P2/P3/P5).

Covers:
- Memory Tree (P1)
- Connections / Add Account (P2)
- Screen Awareness status (P3)
- TokenJuice rules + compact (P5)
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace

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


@pytest.mark.asyncio
async def test_memory_tree_search_filters_summary_and_level():
    from companion_core.db import get_db

    db = get_db()
    await db.execute(
        """INSERT INTO memory_tree (agent, level, bucket_start, bucket_end, summary, child_ids)
           VALUES ('SOUL', 'day', '2026-05-23T00:00:00-05:00', '2026-05-23T23:59:59-05:00',
                   'William reviso OpenHuman y pidio memoria visual.', '[]')"""
    )
    await db.execute(
        """INSERT INTO memory_tree (agent, level, bucket_start, bucket_end, summary, child_ids)
           VALUES ('SOUL', 'hour', '2026-05-23T09:00:00-05:00', '2026-05-23T09:59:59-05:00',
                   'Otro bloque sin la palabra clave.', '[]')"""
    )
    await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/memory-tree/search?agent=SOUL&level=day&q=openhuman")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["query"] == "openhuman"
        assert d["count"] == 1
        assert d["buckets"][0]["level"] == "day"
        assert d["buckets"][0]["bucket_key"] == "2026-05-23T00:00:00-05:00"


@pytest.mark.asyncio
async def test_memory_tree_detail_returns_children_parent_and_memories():
    from companion_core.db import get_db

    db = get_db()
    cur_mem = await db.execute(
        """INSERT INTO memories (agent, category, content, importance)
           VALUES ('SOUL', 'milestone', 'Screen Intelligence capturo la pantalla.', 8)"""
    )
    mem_id = cur_mem.lastrowid
    cur_hour = await db.execute(
        """INSERT INTO memory_tree (agent, level, bucket_start, bucket_end, summary, child_ids)
           VALUES ('SOUL', 'hour', '2026-05-23T09:00:00-05:00', '2026-05-23T09:59:59-05:00',
                   'Hora con captura de pantalla.', ?)""",
        (json.dumps([mem_id]),),
    )
    hour_id = cur_hour.lastrowid
    await db.execute(
        """INSERT INTO memory_tree (agent, level, bucket_start, bucket_end, summary, child_ids)
           VALUES ('SOUL', 'day', '2026-05-23T00:00:00-05:00', '2026-05-23T23:59:59-05:00',
                   'Dia con progreso OpenHuman.', ?)""",
        (json.dumps([hour_id]),),
    )
    await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hour = await client.get(f"/api/memory-tree/buckets/{hour_id}")
        assert hour.status_code == 200
        hour_d = hour.json()
        assert hour_d["ok"] is True
        assert hour_d["bucket"]["id"] == hour_id
        assert hour_d["parent"]["level"] == "day"
        assert hour_d["memories"][0]["id"] == mem_id

        day_id = hour_d["parent"]["id"]
        day = await client.get(f"/api/memory-tree/buckets/{day_id}")
        assert day.status_code == 200
        assert day.json()["children"][0]["id"] == hour_id


@pytest.mark.asyncio
async def test_memory_tree_rebuild_calls_builder(monkeypatch):
    import companion_core.memory_tree_builder as builder

    async def fake_build_all(agent: str, dry_run: bool = False):
        return [{"level": "hour", "agent": agent, "dry_run": dry_run}]

    monkeypatch.setattr(builder, "build_all", fake_build_all)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/memory-tree/rebuild", json={"agent": "SOUL", "level": "all", "dry_run": True})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["agent"] == "SOUL"
        assert d["dry_run"] is True
        assert d["result"][0]["level"] == "hour"


@pytest.mark.asyncio
async def test_memory_tree_rebuild_backfills_existing_user_memories(monkeypatch):
    from companion_core.db import get_db
    import companion_core.memory_tree_builder as builder

    async def fake_summary(items: list[str], context_label: str) -> str:
        return f"{context_label}: {len(items)} items"

    monkeypatch.setattr(builder, "_summarize_text", fake_summary)

    db = get_db()
    await db.execute(
        """INSERT INTO memories (agent, category, content, importance, created_at)
           VALUES ('USER', 'preference', 'Le gusta guardar recuerdos locales.', 6, '2026-05-15 15:15:11')"""
    )
    await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/memory-tree/rebuild", json={"agent": "USER", "level": "all"})
        assert r.status_code == 200
        d = r.json()
        assert [x["level"] for x in d["result"]] == ["hour", "day", "month", "year"]
        assert all(x["rows_in_bucket"] == 1 for x in d["result"])

        day = await client.get("/api/memory-tree?agent=USER&level=day")
        assert day.status_code == 200
        day_d = day.json()
        assert day_d["count"] == 1
        assert day_d["buckets"][0]["bucket_start"] == "2026-05-15T00:00:00-05:00"


@pytest.mark.asyncio
async def test_memory_tree_rebuild_rejects_unknown_level():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/memory-tree/rebuild", json={"agent": "SOUL", "level": "decade"})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_unknown_api_endpoint_returns_user_friendly_404():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/inexistente")
        assert r.status_code == 404
        d = r.json()
        assert d["error"] == "endpoint no encontrado"
        assert "/api/health" in d["available"]
        assert d["path"] == "/api/inexistente"


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


@pytest.mark.asyncio
async def test_screen_history_empty_and_local_only(tmp_path, monkeypatch):
    import companion_core.main as main_mod

    monkeypatch.setattr(main_mod, "_SCREEN_DIR", tmp_path / "screen")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/screen/history")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["local_only"] is True
        assert d["captures"] == []


@pytest.mark.asyncio
async def test_screen_capture_writes_thumbnail_with_fake_capture(tmp_path, monkeypatch):
    import companion_core.main as main_mod

    class FakeShot:
        size = (2, 1)
        rgb = bytes([255, 0, 0, 0, 255, 0])

    class FakeMss:
        monitors = [None, {"left": 0, "top": 0, "width": 2, "height": 1}]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def grab(self, monitor):
            return FakeShot()

    monkeypatch.setattr(main_mod, "_SCREEN_DIR", tmp_path / "screen")
    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=lambda: FakeMss()))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/screen/capture")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["local_only"] is True
        assert d["width"] == 2
        assert d["height"] == 1
        assert Path(d["path"]).exists()
        assert (tmp_path / "screen" / f"{d['image_id']}.thumb.png").exists()

        history = await client.get("/api/screen/history")
        assert history.json()["count"] == 1


@pytest.mark.asyncio
async def test_screen_analyze_uses_local_ollama_payload(tmp_path, monkeypatch):
    import companion_core.main as main_mod
    from PIL import Image

    image_id = "test_capture"
    screen_dir = tmp_path / "screen"
    screen_dir.mkdir()
    Image.new("RGB", (1, 1), color=(20, 40, 60)).save(screen_dir / f"{image_id}.png")

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"response": "Una ventana local de prueba."}).encode()

    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode())
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(main_mod, "_SCREEN_DIR", screen_dir)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/screen/analyze",
            json={"image_id": image_id, "prompt": "Describe", "model": "gemma3:4b"},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["local_only"] is True
        assert d["description"] == "Una ventana local de prueba."
        assert captured["url"] == "http://localhost:11434/api/generate"
        assert captured["payload"]["stream"] is False
        assert len(captured["payload"]["images"]) == 1


@pytest.mark.asyncio
async def test_screen_rejects_invalid_image_id(tmp_path, monkeypatch):
    import companion_core.main as main_mod

    monkeypatch.setattr(main_mod, "_SCREEN_DIR", tmp_path / "screen")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        analyze = await client.post("/api/screen/analyze", json={"image_id": "../secret"})
        assert analyze.status_code == 400

        thumb = await client.get("/api/screen/thumbnail/../secret")
        assert thumb.status_code in {400, 404}


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


@pytest.mark.asyncio
async def test_tokenjuice_custom_rule_crud_and_stats():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create = await client.post(
            "/api/tokenjuice/rules",
            json={
                "id": "remove_noise_line",
                "label": "Quitar linea NOISE",
                "pattern": r"^NOISE:.*$",
                "category": "custom",
            },
        )
        assert create.status_code == 200
        assert create.json()["rule"]["builtin"] is False

        listed = await client.get("/api/tokenjuice/rules?include_disabled=true")
        rule_ids = {r["id"] for r in listed.json()["rules"]}
        assert "remove_noise_line" in rule_ids

        compact = await client.post("/api/tokenjuice/compact", json={"text": "ok\nNOISE: drop me\nfin"})
        compact_d = compact.json()
        assert compact_d["ok"] is True
        assert "NOISE" not in compact_d["output"]
        assert any(r["rule"] == "remove_noise_line" for r in compact_d["rules_applied"])

        stats = await client.get("/api/tokenjuice/stats")
        stat = next(s for s in stats.json()["stats"] if s["rule_id"] == "remove_noise_line")
        assert stat["match_count"] >= 1
        assert stat["chars_saved"] > 0

        patch = await client.patch("/api/tokenjuice/rules/remove_noise_line", json={"enabled": False})
        assert patch.status_code == 200
        assert patch.json()["rule"]["enabled"] is False

        delete = await client.delete("/api/tokenjuice/rules/remove_noise_line")
        assert delete.status_code == 200
        assert delete.json()["deleted"] is True


@pytest.mark.asyncio
async def test_tokenjuice_rejects_bad_regex_and_builtin_delete():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad = await client.post(
            "/api/tokenjuice/rules",
            json={"id": "bad_regex", "label": "Bad", "pattern": "(", "category": "custom"},
        )
        assert bad.status_code == 400

        builtin = await client.delete("/api/tokenjuice/rules/ansi_color_codes")
        assert builtin.status_code == 400


@pytest.mark.asyncio
async def test_tokenjuice_export_import_custom_rules():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        imp = await client.post(
            "/api/tokenjuice/import",
            json={
                "custom_rules": [
                    {
                        "id": "strip_debug",
                        "label": "Quitar DEBUG",
                        "pattern": r"^DEBUG:.*$",
                        "category": "logs",
                        "enabled": True,
                    }
                ]
            },
        )
        assert imp.status_code == 200
        assert imp.json()["imported"] == ["strip_debug"]

        exported = await client.get("/api/tokenjuice/export")
        assert exported.status_code == 200
        rules = exported.json()["custom_rules"]
        assert any(r["id"] == "strip_debug" and r["category"] == "logs" for r in rules)
