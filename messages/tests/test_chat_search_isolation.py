"""Regression tests for authenticated/RLS-scoped full-text chat search."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from messages.chat_db import ChatDB


RUNTIME_DSN = os.environ.get("SEAL_PG_DSN", "").strip()
ADMIN_DSN = os.environ.get("SEAL_PG_TEST_ADMIN_DSN", "").strip()


@pytest_asyncio.fixture
async def chat_db():
    if not ADMIN_DSN:
        pytest.skip("SEAL_PG_TEST_ADMIN_DSN is required for write-scoped integration setup")
    db = ChatDB(ADMIN_DSN)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


@asynccontextmanager
async def rls_reader(db: ChatDB, user_id: int, identity: str):
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE chat_msg_ro")
            await conn.execute("SELECT set_config('app.current_user_id', $1, true)", str(user_id))
            await conn.execute("SELECT set_config('app.current_identity', $1, true)", identity)
            yield conn


@pytest.mark.asyncio
async def test_search_fails_closed_without_rls_connection(chat_db):
    assert await chat_db.search_messages("secreto", allowed_agents=["ADA"]) == []
    assert await chat_db.search_messages("secreto", conn=object()) == []


@pytest.mark.asyncio
async def test_search_isolates_other_users_dm_and_unassigned_agents(chat_db):
    marker = f"searchisolation{uuid4().hex}"
    inserted_ids: list[int] = []
    async with chat_db.pool.acquire() as writer:
        for sender, channel in (
            ("ADA", "dm:search-user-a:ada"),
            ("ADA", "dm:search-user-b:ada"),
            ("ADA", "web_chat"),
            ("NEXUS", "web_chat"),
        ):
            row_id = await writer.fetchval(
                """INSERT INTO chat_messages
                   (sender_type, sender_name, channel, message_type, content)
                   VALUES ('agent', $1, $2, 'text', $3) RETURNING id""",
                sender, channel, marker,
            )
            inserted_ids.append(row_id)

    try:
        async with rls_reader(chat_db, 910001, "search-user-a") as conn:
            rows = await chat_db.search_messages(
                marker,
                conn=conn,
                allowed_agents=["ADA"],
            )

        channels_and_senders = {(row["channel"], row["sender_name"]) for row in rows}
        assert ("dm:search-user-a:ada", "ADA") in channels_and_senders
        assert ("web_chat", "ADA") in channels_and_senders
        assert ("dm:search-user-b:ada", "ADA") not in channels_and_senders
        assert ("web_chat", "NEXUS") not in channels_and_senders
    finally:
        async with chat_db.pool.acquire() as writer:
            await writer.execute("DELETE FROM chat_messages WHERE id = ANY($1::bigint[])", inserted_ids)


@pytest.mark.asyncio
async def test_channel_filter_cannot_bypass_dm_rls(chat_db):
    marker = f"searchchannel{uuid4().hex}"
    async with chat_db.pool.acquire() as writer:
        row_id = await writer.fetchval(
            """INSERT INTO chat_messages
               (sender_type, sender_name, channel, message_type, content)
               VALUES ('agent', 'ADA', 'dm:search-user-b:ada', 'text', $1)
               RETURNING id""",
            marker,
        )
    try:
        async with rls_reader(chat_db, 910001, "search-user-a") as conn:
            rows = await chat_db.search_messages(
                marker,
                channel="dm:search-user-b:ada",
                conn=conn,
                allowed_agents=["ADA"],
            )
        assert rows == []
    finally:
        async with chat_db.pool.acquire() as writer:
            await writer.execute("DELETE FROM chat_messages WHERE id = $1", row_id)
