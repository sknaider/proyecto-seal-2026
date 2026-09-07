"""Regression suite for chat_db.validate_session's sliding-renewal + absolute cap.

Born from a live back-and-forth with ADA (2026-07-17): a plain 72h TTL let active
webchat sessions die mid-use (William: "estas sesiones no deben vencer"); the first
sliding-renewal fix had no ceiling, so a replayed token could live forever (ADA); the
first ceiling only blocked renewal, so a >30d-old session still authenticated one last
time (ADA again). These six tests pin the edge cases ADA required for sign-off
(>18h, <=18h, expired, 30d-ε, 30d+ε, revoked) against the real Postgres predicate —
a mock WHERE clause would hide exactly the kind of off-by-one ADA caught twice.
"""

from __future__ import annotations

import hashlib
import inspect
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio

from messages.chat_db import ChatDB, MAX_SESSION_AGE, TOKEN_EXPIRY_HOURS

RUNTIME_DSN = os.environ.get("SEAL_PG_DSN", "").strip()
ADMIN_DSN = os.environ.get("SEAL_PG_TEST_ADMIN_DSN", "").strip()
TEST_USERNAME = "NEXUS"


def test_runtime_renewal_uses_bounded_database_function() -> None:
    """The least-privilege login role cannot perform a direct session UPDATE."""
    source = inspect.getsource(ChatDB.validate_session)
    assert "session_renew_by_token" in source
    assert "UPDATE chat_sessions SET expires_at" not in source


async def _make_session(conn, uid: int, token: str, created_at: datetime, expires_at: datetime) -> str:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    await conn.execute("DELETE FROM chat_sessions WHERE token_hash=$1", token_hash)
    await conn.execute(
        "INSERT INTO chat_sessions (user_id, token_hash, expires_at, ip_address, user_agent, created_at) "
        "VALUES ($1,$2,$3,'127.0.0.1','pytest',$4)",
        uid, token_hash, expires_at, created_at,
    )
    return token_hash


@pytest_asyncio.fixture
async def pg_conn():
    if not ADMIN_DSN:
        pytest.skip("SEAL_PG_TEST_ADMIN_DSN is required for write-scoped integration setup")
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def chat_db():
    if not RUNTIME_DSN:
        pytest.skip("SEAL_PG_DSN is required for runtime session validation")
    db = ChatDB(RUNTIME_DSN)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


@pytest_asyncio.fixture
async def nexus_uid(pg_conn):
    return await pg_conn.fetchval("SELECT id FROM chat_users WHERE username=$1", TEST_USERNAME)


@pytest.mark.asyncio
async def test_session_far_from_expiry_does_not_renew(pg_conn, chat_db, nexus_uid):
    """>18h left (outside the quarter-window threshold): expires_at must not move."""
    created = datetime.now(timezone.utc) - timedelta(days=1)
    original_expiry = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS)  # full window left
    th = await _make_session(pg_conn, nexus_uid, "pytest-far-from-expiry", created, original_expiry)
    try:
        result = await chat_db.validate_session(th)
        assert result is not None

        row = await pg_conn.fetchrow("SELECT expires_at FROM chat_sessions WHERE token_hash=$1", th)
        assert row["expires_at"] == original_expiry
    finally:
        await pg_conn.execute("DELETE FROM chat_sessions WHERE token_hash=$1", th)


@pytest.mark.asyncio
async def test_session_near_expiry_renews_under_cap(pg_conn, chat_db, nexus_uid):
    """<=18h left, well under the 30d cap: expires_at slides forward ~TOKEN_EXPIRY_HOURS."""
    created = datetime.now(timezone.utc) - timedelta(days=1)
    near_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    th = await _make_session(pg_conn, nexus_uid, "pytest-near-expiry-renews", created, near_expiry)
    try:
        result = await chat_db.validate_session(th)
        assert result is not None

        row = await pg_conn.fetchrow("SELECT expires_at FROM chat_sessions WHERE token_hash=$1", th)
        expected_floor = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS - 1)
        assert row["expires_at"] > near_expiry
        assert row["expires_at"] >= expected_floor
    finally:
        await pg_conn.execute("DELETE FROM chat_sessions WHERE token_hash=$1", th)


@pytest.mark.asyncio
async def test_session_past_absolute_cap_is_rejected_outright(pg_conn, chat_db, nexus_uid):
    """30d+ε old: must fail validation immediately, even if expires_at is still in the future
    from an earlier renewal. This is the exact bug ADA caught in the first cap attempt —
    the fix belongs in the WHERE clause, not as a post-hoc renewal skip."""
    created = datetime.now(timezone.utc) - (MAX_SESSION_AGE + timedelta(hours=1))
    still_future_expiry = datetime.now(timezone.utc) + timedelta(hours=48)
    th = await _make_session(pg_conn, nexus_uid, "pytest-past-absolute-cap", created, still_future_expiry)
    try:
        result = await chat_db.validate_session(th)
        assert result is None

        row = await pg_conn.fetchrow("SELECT expires_at FROM chat_sessions WHERE token_hash=$1", th)
        assert row["expires_at"] == still_future_expiry  # untouched: rejected, not renewed
    finally:
        await pg_conn.execute("DELETE FROM chat_sessions WHERE token_hash=$1", th)


@pytest.mark.asyncio
async def test_session_just_under_absolute_cap_still_validates(pg_conn, chat_db, nexus_uid):
    """30d-ε old: must still validate — the boundary must not be off-by-one in the strict direction."""
    created = datetime.now(timezone.utc) - (MAX_SESSION_AGE - timedelta(hours=1))
    near_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    th = await _make_session(pg_conn, nexus_uid, "pytest-just-under-cap", created, near_expiry)
    try:
        result = await chat_db.validate_session(th)
        assert result is not None

        row = await pg_conn.fetchrow("SELECT expires_at FROM chat_sessions WHERE token_hash=$1", th)
        cap = created + MAX_SESSION_AGE
        # microsecond tolerance: the test computes `cap` independently of the DB round-trip
        # that produces row["expires_at"]; a few µs of float/interval noise is not the bug
        # this test guards against (a 30d+ off-by-one), so don't let it flake on that.
        assert row["expires_at"] <= cap + timedelta(milliseconds=50)
    finally:
        await pg_conn.execute("DELETE FROM chat_sessions WHERE token_hash=$1", th)


@pytest.mark.asyncio
async def test_expired_session_is_rejected(pg_conn, chat_db, nexus_uid):
    """expires_at already in the past: must fail regardless of age — the pre-existing behavior
    the sliding-renewal fix must not have weakened."""
    created = datetime.now(timezone.utc) - timedelta(days=1)
    past_expiry = datetime.now(timezone.utc) - timedelta(minutes=5)
    th = await _make_session(pg_conn, nexus_uid, "pytest-already-expired", created, past_expiry)
    try:
        result = await chat_db.validate_session(th)
        assert result is None
    finally:
        await pg_conn.execute("DELETE FROM chat_sessions WHERE token_hash=$1", th)


@pytest.mark.asyncio
async def test_revoked_session_is_rejected_immediately(pg_conn, chat_db, nexus_uid):
    """delete_session() must still kill a session on the spot, independent of sliding renewal —
    a live, unexpired, under-cap session that gets explicitly revoked must not validate."""
    created = datetime.now(timezone.utc) - timedelta(days=1)
    healthy_expiry = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS)
    th = await _make_session(pg_conn, nexus_uid, "pytest-revoked", created, healthy_expiry)
    try:
        assert await chat_db.validate_session(th) is not None  # alive before revocation

        await chat_db.delete_session(th)

        assert await chat_db.validate_session(th) is None  # dead immediately after
    finally:
        await pg_conn.execute("DELETE FROM chat_sessions WHERE token_hash=$1", th)
