"""Tests for SEAL App Sprint 1 backend port (JARVIS — Workstream B).

Covers:
- BYOK vault endpoints (status, save, delete) + audit trail
- Dreams list endpoint
- LLM routing (GET + PATCH upsert)
- Capabilities (GET + PATCH)
- Audit log (read-only proxy)
- Notifications (CRUD)
"""
from __future__ import annotations

import os
import tempfile
import pytest
from httpx import AsyncClient, ASGITransport
from companion_core.main import app


@pytest.fixture
def isolated_vault(monkeypatch):
    """Sandbox the BYOK vault to a tmpdir so tests don't touch real ~/.config."""
    tmpdir = tempfile.mkdtemp(prefix="byok_test_")
    monkeypatch.setenv("SOUL_VAULT_DIR", tmpdir)
    # force re-import of paths
    import importlib
    from companion_core import byok_vault as bv
    importlib.reload(bv)
    yield tmpdir
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_byok_status_initial_empty(isolated_vault):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/byok/status")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["providers_configured"] == []
        assert "allowed_providers" in d


@pytest.mark.asyncio
async def test_byok_save_and_status_lists_provider(isolated_vault):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        save = await client.post("/api/byok/key", json={"provider": "openai", "api_key": "sk-test-1234567890"})
        assert save.status_code == 200
        assert save.json()["ok"] is True
        status = await client.get("/api/byok/status")
        assert "openai" in status.json()["providers_configured"]


@pytest.mark.asyncio
async def test_byok_save_rejects_unknown_provider(isolated_vault):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/byok/key", json={"provider": "evil", "api_key": "sk-bad-1234567890"})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_byok_save_rejects_short_key(isolated_vault):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/byok/key", json={"provider": "openai", "api_key": "x"})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_byok_save_creates_audit_entry(isolated_vault):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/byok/key", json={"provider": "anthropic", "api_key": "sk-ant-test-12345"})
        audit = await client.get("/api/audit-log?limit=5")
        entries = audit.json()["entries"]
        assert any(e["action"] == "byok_save" and e["target_id"] == "anthropic" for e in entries)


@pytest.mark.asyncio
async def test_byok_delete(isolated_vault):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/byok/key", json={"provider": "openai", "api_key": "sk-test-1234567890"})
        r = await client.delete("/api/byok/key/openai")
        assert r.status_code == 200
        assert r.json()["removed"] is True
        status = await client.get("/api/byok/status")
        assert "openai" not in status.json()["providers_configured"]


@pytest.mark.asyncio
async def test_dreams_empty_initial():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/dreams")
        assert r.status_code == 200
        d = r.json()
        assert d["count"] == 0
        assert d["dreams"] == []


@pytest.mark.asyncio
async def test_dreams_rejects_unknown_agent():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/dreams?agent=HACKER")
        assert "error" in r.json()


@pytest.mark.asyncio
async def test_llm_routing_default_seeded():
    """4 default rows must be auto-seeded on DB init."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/llm-routing?agent=DEFAULT")
        assert r.status_code == 200
        d = r.json()
        roles = {row["role"] for row in d["rows"]}
        assert roles == {"reasoning", "agentic", "coding", "summary"}
        for row in d["rows"]:
            assert row["provider"] == "ollama"


@pytest.mark.asyncio
async def test_llm_routing_patch_upsert():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        patch = await client.patch("/api/llm-routing", json={
            "agent": "DEFAULT",
            "role": "reasoning",
            "provider": "anthropic",
            "model": "claude-opus-4-7",
            "enabled": True,
        })
        assert patch.status_code == 200
        get = await client.get("/api/llm-routing?agent=DEFAULT")
        reasoning = next(r for r in get.json()["rows"] if r["role"] == "reasoning")
        assert reasoning["provider"] == "anthropic"
        assert reasoning["model"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_llm_routing_patch_rejects_unknown_role():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.patch("/api/llm-routing", json={
            "agent": "DEFAULT", "role": "invalid", "provider": "ollama", "model": "x"})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_capabilities_soul_defaults_safe():
    """SOUL agent must arrive with shell/git/write/screen/camera/browser OFF."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/capabilities?agent=SOUL")
        caps = r.json()["capabilities"]
        for risky in ["cap_shell_commands", "cap_git", "cap_write_files",
                      "cap_screen_capture", "cap_camera", "cap_browser_control",
                      "cap_cron_jobs", "cap_channel_read"]:
            assert caps[risky] is False, f"{risky} must default OFF (William rule)"


@pytest.mark.asyncio
async def test_capabilities_patch_toggles():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch("/api/capabilities", json={
            "agent": "SOUL", "capability": "cap_shell_commands", "enabled": True})
        r = await client.get("/api/capabilities?agent=SOUL")
        assert r.json()["capabilities"]["cap_shell_commands"] is True


@pytest.mark.asyncio
async def test_capabilities_patch_rejects_unknown():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.patch("/api/capabilities", json={
            "agent": "SOUL", "capability": "cap_nuclear", "enabled": True})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_audit_log_empty_initial():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/audit-log")
        d = r.json()
        assert d["ok"] is True
        assert d["count"] == 0
        assert d["stats"] == {"local": 0, "egress": 0}


@pytest.mark.asyncio
async def test_notifications_crud():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create
        cr = await client.post("/api/notifications", json={
            "agent": "SOUL", "type": "system_alert", "title": "Test alert", "body": "test body"})
        assert cr.status_code == 200
        nid = cr.json()["id"]
        # List
        lr = await client.get("/api/notifications")
        assert lr.json()["count"] == 1
        # Mark read
        await client.post(f"/api/notifications/{nid}/read")
        # Unread filter shows 0
        u = await client.get("/api/notifications?unread_only=true")
        assert u.json()["count"] == 0
        # Dismiss
        await client.delete(f"/api/notifications/{nid}")
        all_after = await client.get("/api/notifications")
        assert all_after.json()["count"] == 0


@pytest.mark.asyncio
async def test_notifications_rejects_unknown_type():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/notifications", json={
            "agent": "SOUL", "type": "evil_type", "title": "x"})
        assert r.status_code == 400
