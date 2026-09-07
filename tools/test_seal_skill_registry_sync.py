from pathlib import Path

import asyncpg
import pytest

from memory.seal_secrets import pg_dsn
from tools.seal_skill_registry_sync import describe_skill, discover_skill_files, reconcile


def test_describe_skill_uses_frontmatter_and_hash(tmp_path):
    path = tmp_path / "demo" / "SKILL.md"
    path.parent.mkdir()
    path.write_text("---\nname: demo-skill\ndescription: Demo segura\n---\n# Demo\n")
    item = describe_skill(path)
    assert item["name"] == "demo-skill"
    assert item["description"] == "Demo segura"
    assert len(item["content_sha256"]) == 64


def test_discovery_deduplicates_resolved_paths(tmp_path):
    path = tmp_path / "skills" / "demo" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Demo\n")
    assert discover_skill_files((tmp_path / "skills", tmp_path / "skills")) == [path.resolve()]


@pytest.mark.asyncio
async def test_reconcile_is_pending_review_and_idempotent(tmp_path):
    path = tmp_path / "skills" / "telemetry-sync-test" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: telemetry-sync-test\ndescription: Canary\n---\n")
    conn = await asyncpg.connect(pg_dsn(required=True))
    tr = conn.transaction()
    await tr.start()
    try:
        preview = await reconcile(conn, (tmp_path / "skills",), apply=False)
        first = await reconcile(conn, (tmp_path / "skills",), apply=True)
        second = await reconcile(conn, (tmp_path / "skills",), apply=True)
        row = await conn.fetchrow(
            "SELECT agent,pending_review,boot_load,execution_environment FROM soul_v3.skills WHERE name='telemetry-sync-test'"
        )
        assert preview["candidate_count"] == 1
        assert first["inserted_count"] == 1
        assert second["inserted_count"] == 0
        assert dict(row) == {
            "agent": "TEAM",
            "pending_review": True,
            "boot_load": False,
            "execution_environment": "restricted",
        }
    finally:
        await tr.rollback()
        await conn.close()
