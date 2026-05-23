"""Tests for Skills API."""
import pytest
from httpx import AsyncClient, ASGITransport
from companion_core.main import app


@pytest.mark.asyncio
async def test_create_skill():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/skills", json={
            "name": "Summarize", "prompt_template": "Summarize this: {text}", "category": "writing"
        })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert isinstance(r.json()["id"], int)


@pytest.mark.asyncio
async def test_create_skill_empty_name_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/skills", json={"name": "", "prompt_template": "do something"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_skill_empty_template_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/skills", json={"name": "BadSkill", "prompt_template": "  "})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_skill_duplicate_returns_409():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"name": "UniqueSkill409", "prompt_template": "do it"}
        await client.post("/api/skills", json=payload)
        r = await client.post("/api/skills", json=payload)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_import_skill_md_from_content():
    content = """---
name: react-expert
description: Use when building React components.
---

# React Expert

Build accessible React views.
"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/skills/import-skill-md", json={"content": content})
        assert r.status_code == 200
        sid = r.json()["id"]
        run = await client.post(f"/api/skills/{sid}/run", json={"task": "Create AvatarView"})
    assert run.json()["skill"] == "react-expert"
    assert "Build accessible React views." in run.json()["prompt"]
    assert "Create AvatarView" in run.json()["prompt"]


@pytest.mark.asyncio
async def test_import_skill_md_from_path(tmp_path):
    skill_dir = tmp_path / "systematic-debugging"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("# Systematic Debugging\n\nFind root cause before fixes.\n", encoding="utf-8")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/skills/import-skill-md", json={"path": str(skill_file)})
        assert r.status_code == 200
        listed = await client.get("/api/skills")
    skill = next(s for s in listed.json()["skills"] if s["name"] == "Systematic Debugging")
    assert skill["category"] == "skill-md"
    assert skill["trigger_phrase"] == "systematic-debugging"


@pytest.mark.asyncio
async def test_import_skill_md_overwrite_existing():
    first = "# Duplicate Skill\n\nFirst version."
    second = "# Duplicate Skill\n\nSecond version."
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/skills/import-skill-md", json={"content": first})
        assert r1.status_code == 200
        conflict = await client.post("/api/skills/import-skill-md", json={"content": second})
        assert conflict.status_code == 409
        r2 = await client.post("/api/skills/import-skill-md", json={"content": second, "overwrite": True})
        assert r2.status_code == 200
        run = await client.post(f"/api/skills/{r2.json()['id']}/run", json={"task": "x"})
    assert "Second version." in run.json()["prompt"]


@pytest.mark.asyncio
async def test_list_skills_empty():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/skills")
    assert r.status_code == 200
    data = r.json()
    assert "skills" in data
    assert "total" in data
    assert isinstance(data["skills"], list)


@pytest.mark.asyncio
async def test_list_skills_after_create():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/skills", json={"name": "ListSkill", "prompt_template": "list prompt"})
        r = await client.get("/api/skills")
    skills = r.json()["skills"]
    assert any(s["name"] == "ListSkill" for s in skills)


@pytest.mark.asyncio
async def test_list_skills_enabled_only_filters_disabled():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/skills", json={
            "name": "EnabledSkill", "prompt_template": "active", "enabled": True
        })
        r1 = await client.post("/api/skills", json={
            "name": "DisabledSkill", "prompt_template": "inactive", "enabled": False
        })
        sid = r1.json()["id"]
        r = await client.get("/api/skills?enabled_only=true")
    skills = r.json()["skills"]
    ids = [s["id"] for s in skills]
    assert sid not in ids
    assert all(s["enabled"] for s in skills)


@pytest.mark.asyncio
async def test_run_skill_no_variables():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/skills", json={
            "name": "SimpleRun", "prompt_template": "Just do it"
        })
        sid = r1.json()["id"]
        r = await client.post(f"/api/skills/{sid}/run")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["prompt"] == "Just do it"
    assert r.json()["skill"] == "SimpleRun"


@pytest.mark.asyncio
async def test_run_skill_with_variables():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/skills", json={
            "name": "TemplateRun", "prompt_template": "Translate {text} to {lang}"
        })
        sid = r1.json()["id"]
        r = await client.post(f"/api/skills/{sid}/run", json={"text": "hello", "lang": "Spanish"})
    assert r.json()["prompt"] == "Translate hello to Spanish"


@pytest.mark.asyncio
async def test_run_skill_increments_use_count():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/skills", json={
            "name": "CountSkill", "prompt_template": "count me"
        })
        sid = r1.json()["id"]
        await client.post(f"/api/skills/{sid}/run")
        await client.post(f"/api/skills/{sid}/run")
        r = await client.get("/api/skills")
    skill = next(s for s in r.json()["skills"] if s["id"] == sid)
    assert skill["use_count"] >= 2


@pytest.mark.asyncio
async def test_run_skill_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/skills/999999/run")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_run_skill_disabled_returns_409():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/skills", json={
            "name": "DisabledRun", "prompt_template": "won't run", "enabled": False
        })
        sid = r1.json()["id"]
        r = await client.post(f"/api/skills/{sid}/run")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_update_skill():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/skills", json={
            "name": "UpdateMe", "prompt_template": "old template"
        })
        sid = r1.json()["id"]
        r2 = await client.patch(f"/api/skills/{sid}", json={"prompt_template": "new template"})
        assert r2.json()["ok"] is True
        r3 = await client.get("/api/skills")
    skill = next(s for s in r3.json()["skills"] if s["id"] == sid)
    assert skill["prompt_template"] == "new template"


@pytest.mark.asyncio
async def test_update_skill_disable():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/skills", json={
            "name": "DisableMe", "prompt_template": "active skill"
        })
        sid = r1.json()["id"]
        await client.patch(f"/api/skills/{sid}", json={"enabled": False})
        r = await client.get("/api/skills?enabled_only=true")
    ids = [s["id"] for s in r.json()["skills"]]
    assert sid not in ids


@pytest.mark.asyncio
async def test_update_skill_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.patch("/api/skills/999999", json={"name": "ghost"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_skill_no_fields_returns_ok():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/skills", json={
            "name": "NoOpUpdate", "prompt_template": "stays same"
        })
        sid = r1.json()["id"]
        r = await client.patch(f"/api/skills/{sid}", json={})
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_delete_skill():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/skills", json={
            "name": "DeleteMe", "prompt_template": "gone"
        })
        sid = r1.json()["id"]
        r2 = await client.delete(f"/api/skills/{sid}")
        assert r2.json()["ok"] is True
        r3 = await client.get("/api/skills")
    ids = [s["id"] for s in r3.json()["skills"]]
    assert sid not in ids


@pytest.mark.asyncio
async def test_delete_skill_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/skills/999999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_skills_sorted_by_use_count():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/skills", json={"name": "LowUse", "prompt_template": "low"})
        r2 = await client.post("/api/skills", json={"name": "HighUse", "prompt_template": "high"})
        sid_low = r1.json()["id"]
        sid_high = r2.json()["id"]
        # Run HighUse 3 times
        for _ in range(3):
            await client.post(f"/api/skills/{sid_high}/run")
        r = await client.get("/api/skills")
    skills = r.json()["skills"]
    # HighUse should appear before LowUse
    ids = [s["id"] for s in skills]
    assert ids.index(sid_high) < ids.index(sid_low)
