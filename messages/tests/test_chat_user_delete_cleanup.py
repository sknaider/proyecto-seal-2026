"""Deletion contract: removing a user must use the bounded DB operation."""

from pathlib import Path

import pytest

from messages.chat_db import ChatDB, LastSuperuserError


class _FakePool:
    def __init__(self, deleted):
        self.deleted = deleted
        self.calls = []

    async def fetchval(self, query, *args):
        self.calls.append((query, *args))
        return self.deleted


class _RolePool:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def fetchval(self, query, *args):
        self.calls.append((query, *args))
        return self.result


@pytest.mark.asyncio
async def test_delete_user_uses_atomic_session_cleanup_function():
    pool = _FakePool(deleted=1)
    db = ChatDB()
    db.pool = pool
    db.admin_pool = pool

    assert await db.delete_user(
        "temporary-basic", actor_user_id=1, session_token_hash="session-hash"
    ) is True
    assert pool.calls == [
        ("SELECT soul_v3.user_delete_with_sessions($1,$2,$3)",
         "temporary-basic", 1, "session-hash")
    ]


@pytest.mark.asyncio
async def test_delete_user_missing_principal_is_noop():
    pool = _FakePool(deleted=0)
    db = ChatDB()
    db.pool = pool
    db.admin_pool = pool

    assert await db.delete_user(
        "missing", actor_user_id=1, session_token_hash="session-hash"
    ) is False


@pytest.mark.asyncio
async def test_delete_user_prefers_admin_pool():
    runtime_pool = _FakePool(deleted=0)
    admin_pool = _FakePool(deleted=1)
    db = ChatDB()
    db.pool = runtime_pool
    db.admin_pool = admin_pool

    assert await db.delete_user(
        "temporary-basic", actor_user_id=1, session_token_hash="session-hash"
    ) is True
    assert runtime_pool.calls == []
    assert admin_pool.calls == [
        ("SELECT soul_v3.user_delete_with_sessions($1,$2,$3)",
         "temporary-basic", 1, "session-hash")
    ]


@pytest.mark.asyncio
async def test_delete_last_superuser_is_rejected_inside_locked_transaction():
    pool = _FakePool(deleted=-1)
    db = ChatDB()
    db.pool = pool
    db.admin_pool = pool

    with pytest.raises(LastSuperuserError):
        await db.delete_user(
            "only-owner", actor_user_id=1, session_token_hash="session-hash"
        )

    assert pool.calls == [(
        "SELECT soul_v3.user_delete_with_sessions($1,$2,$3)",
        "only-owner", 1, "session-hash",
    )]


@pytest.mark.asyncio
async def test_role_update_uses_guarded_admin_function():
    pool = _RolePool({
        "ok": True,
        "user": {"id": 7, "username": "person", "display_name": "Person", "role": "admin"},
    })
    db = ChatDB()
    db.pool = object()
    db.admin_pool = pool

    updated = await db.update_user_role(
        7, "admin", "basic", "dadito", "approved",
        actor_user_id=1, session_token_hash="session-hash",
    )

    assert updated["role"] == "admin"
    assert pool.calls == [(
        "SELECT soul_v3.user_role_update_guarded($1,$2,$3,$4,$5)",
        7, "admin", 1, "session-hash", "approved",
    )]


@pytest.mark.asyncio
async def test_role_update_rejects_last_superuser_from_guarded_function():
    pool = _RolePool({"ok": False, "error": "last_superuser"})
    db = ChatDB()
    db.admin_pool = pool

    with pytest.raises(LastSuperuserError):
        await db.update_user_role(
            1, "admin", "superuser", "dadito",
            actor_user_id=1, session_token_hash="session-hash",
        )


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
    assert "REVOKE DELETE ON soul_v3.chat_users, soul_v3.chat_sessions" in migration
    assert "FROM login_bus, pr_bus, login_bus_admin, pr_bus_admin" in migration
    assert "pg_advisory_xact_lock(6000273874612473938)" in migration
    assert "target.role='superuser'" in migration
    assert "RETURN -1" in migration
    assert "user_role_update_guarded" in migration
    assert "REVOKE UPDATE ON soul_v3.chat_users" in migration
    assert "FROM login_bus, pr_bus, login_bus_admin, pr_bus_admin" in migration
    assert "GRANT UPDATE (last_seen) ON soul_v3.chat_users TO login_bus, pr_bus" in migration
    assert "ALTER ROLE user_mgr_owner NOLOGIN NOINHERIT NOSUPERUSER" in migration
    assert "user_mgr_owner must have no members" in migration
    assert "FROM pg_auth_members" in migration
    assert "GRANT USAGE, SELECT ON SEQUENCE soul_v3.soul_audit_log_id_seq" in migration
    assert "p_actor_user_id IS DISTINCT FROM 1" in migration
    assert "durable owner cannot be deleted" in migration
    assert "durable owner cannot be demoted" in migration
    assert "target_username" in migration
    assert "'DELETE','chat_users'" in migration


def test_live_verifier_covers_complete_user_authority_boundary():
    verifier = (
        Path(__file__).parents[2]
        / "scripts"
        / "verify_coordinator_cutover_live.py"
    ).read_text(encoding="utf-8")

    assert "user_owner_has_no_members" in verifier
    assert "no_direct_user_or_session_delete" in verifier
    assert "user_function_boundaries_exact" in verifier
    assert "coordination_function_boundary_exact" in verifier
    assert "_verify_function_bodies" in verifier
    assert "function_bodies_match_migrations" in verifier
    assert "_probe_durable_owner_guards" in verifier
    assert "durable_owner_effect_probes_rejected" in verifier
