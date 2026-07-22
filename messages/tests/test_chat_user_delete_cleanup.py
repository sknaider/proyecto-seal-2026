"""Deletion contract: removing a user must use the bounded DB operation."""

from pathlib import Path

import pytest

from messages.chat_db import ChatDB


class _FakePool:
    def __init__(self, deleted):
        self.deleted = deleted
        self.calls = []

    async def fetchval(self, query, username):
        self.calls.append((query, username))
        return self.deleted


@pytest.mark.asyncio
async def test_delete_user_uses_atomic_session_cleanup_function():
    pool = _FakePool(deleted=1)
    db = ChatDB()
    db.pool = pool

    assert await db.delete_user("temporary-basic") is True
    assert pool.calls == [
        ("SELECT soul_v3.user_delete_with_sessions($1)", "temporary-basic")
    ]


@pytest.mark.asyncio
async def test_delete_user_missing_principal_is_noop():
    pool = _FakePool(deleted=0)
    db = ChatDB()
    db.pool = pool

    assert await db.delete_user("missing") is False


@pytest.mark.asyncio
async def test_delete_user_prefers_admin_pool():
    runtime_pool = _FakePool(deleted=0)
    admin_pool = _FakePool(deleted=1)
    db = ChatDB()
    db.pool = runtime_pool
    db.admin_pool = admin_pool

    assert await db.delete_user("temporary-basic") is True
    assert runtime_pool.calls == []
    assert admin_pool.calls == [
        ("SELECT soul_v3.user_delete_with_sessions($1)", "temporary-basic")
    ]


def test_session_fk_migration_enforces_database_cascade():
    migration = (
        Path(__file__).parents[1]
        / "migrations"
        / "20260718_chat_sessions_user_fk.sql"
    ).read_text(encoding="utf-8")

    assert "FOREIGN KEY (user_id)" in migration
    assert "REFERENCES soul_v3.chat_users(id)" in migration
    assert "ON DELETE CASCADE" in migration
    assert "NOT EXISTS" in migration  # bounded cleanup of historical orphans


def test_delete_function_migration_has_hardened_definer_boundary():
    migration = (
        Path(__file__).parents[1]
        / "migrations"
        / "20260718_user_delete_with_sessions.sql"
    ).read_text(encoding="utf-8")

    assert "SECURITY DEFINER" in migration
    assert "SET search_path = pg_catalog, soul_v3" in migration
    assert "OWNER TO user_mgr_owner" in migration
    assert "REVOKE ALL ON FUNCTION" in migration
    assert "TO login_bus_admin, pr_bus_admin" in migration
    assert "REVOKE SELECT (username)" in migration
