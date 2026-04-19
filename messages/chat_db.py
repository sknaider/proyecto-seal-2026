"""
chat_db.py — SEAL Chat Pro async database layer.

Uses asyncpg for high-performance PostgreSQL access.
Provides CRUD operations for users, sessions, messages, and channels.

Usage:
    from chat_db import ChatDB

    db = ChatDB()
    await db.init()  # creates connection pool
    messages = await db.get_messages("general", limit=50)
    await db.close()
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from typing import Any, Optional
from uuid import UUID

import asyncpg

# ── Config ──────────────────────────────────────────────────────────────────

PG_DSN = os.environ.get(
    "SEAL_PG_DSN",
    "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory",
)
POOL_MIN = 2
POOL_MAX = 10
TOKEN_EXPIRY_HOURS = 72  # JWT session duration


# ── Password hashing (PBKDF2-SHA256, no external deps) ─────────────────────

def hash_password(password: str) -> str:
    """Hash password with PBKDF2-SHA256 + random salt."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"pbkdf2:sha256:260000${salt}${dk.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against stored PBKDF2 hash."""
    try:
        parts = password_hash.split("$")
        if len(parts) != 3:
            return False
        header, salt, stored_hash = parts
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
        return secrets.compare_digest(dk.hex(), stored_hash)
    except Exception:
        return False


# ── Database class ──────────────────────────────────────────────────────────

class ChatDB:
    """Async PostgreSQL interface for SEAL Chat Pro."""

    def __init__(self, dsn: str = PG_DSN):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def init(self) -> None:
        """Initialize connection pool."""
        self.pool = await asyncpg.create_pool(
            self.dsn, min_size=POOL_MIN, max_size=POOL_MAX,
        )

    async def close(self) -> None:
        """Close connection pool."""
        if self.pool:
            await self.pool.close()

    # ── Users ───────────────────────────────────────────────────────────────

    async def create_user(
        self,
        username: str,
        display_name: str,
        password: str,
        role: str = "user",
        avatar_url: str | None = None,
    ) -> dict:
        """Create a new chat user. Returns user dict."""
        pw_hash = hash_password(password)
        row = await self.pool.fetchrow(
            """INSERT INTO chat_users (username, display_name, password_hash, role, avatar_url)
               VALUES ($1, $2, $3, $4, $5)
               RETURNING id, username, display_name, role, avatar_url, created_at""",
            username, display_name, pw_hash, role, avatar_url,
        )
        return dict(row)

    async def get_user_by_username(self, username: str) -> dict | None:
        """Fetch user by username (case-insensitive)."""
        row = await self.pool.fetchrow(
            "SELECT * FROM chat_users WHERE LOWER(username) = LOWER($1)", username,
        )
        return dict(row) if row else None

    async def get_user_by_id(self, user_id: int) -> dict | None:
        """Fetch user by ID."""
        row = await self.pool.fetchrow(
            "SELECT id, username, display_name, role, avatar_url, created_at, last_seen "
            "FROM chat_users WHERE id = $1", user_id,
        )
        return dict(row) if row else None

    async def authenticate(self, username: str, password: str) -> dict | None:
        """Verify credentials. Returns user dict (without password_hash) or None."""
        user = await self.get_user_by_username(username)
        if not user:
            return None
        if not verify_password(password, user["password_hash"]):
            return None
        # Update last_seen
        await self.pool.execute(
            "UPDATE chat_users SET last_seen = NOW() WHERE id = $1", user["id"],
        )
        # Remove sensitive field
        user.pop("password_hash", None)
        return user

    async def update_last_seen(self, user_id: int) -> None:
        """Touch user's last_seen timestamp."""
        await self.pool.execute(
            "UPDATE chat_users SET last_seen = NOW() WHERE id = $1", user_id,
        )

    # ── Sessions ────────────────────────────────────────────────────────────

    async def create_session(
        self, user_id: int, token_hash: str,
        ip_address: str | None = None, user_agent: str | None = None,
    ) -> dict:
        """Create a new session. Returns session dict."""
        expires = datetime.now(LIMA_TZ) + timedelta(hours=TOKEN_EXPIRY_HOURS)
        row = await self.pool.fetchrow(
            """INSERT INTO chat_sessions (user_id, token_hash, expires_at, ip_address, user_agent)
               VALUES ($1, $2, $3, $4::inet, $5)
               RETURNING id, user_id, created_at, expires_at""",
            user_id, token_hash, expires, ip_address, user_agent,
        )
        return dict(row)

    async def validate_session(self, token_hash: str) -> dict | None:
        """Validate session by token hash. Returns user dict or None."""
        row = await self.pool.fetchrow(
            """SELECT s.id as session_id, s.user_id, s.expires_at,
                      u.username, u.display_name, u.role, u.avatar_url
               FROM chat_sessions s
               JOIN chat_users u ON s.user_id = u.id
               WHERE s.token_hash = $1 AND s.expires_at > NOW()""",
            token_hash,
        )
        return dict(row) if row else None

    async def delete_session(self, token_hash: str) -> None:
        """Invalidate a session."""
        await self.pool.execute(
            "DELETE FROM chat_sessions WHERE token_hash = $1", token_hash,
        )

    async def cleanup_expired_sessions(self) -> int:
        """Remove expired sessions. Returns count deleted."""
        result = await self.pool.execute(
            "DELETE FROM chat_sessions WHERE expires_at < NOW()",
        )
        return int(result.split()[-1])

    async def limit_user_sessions(self, user_id: int, max_sessions: int = 5) -> int:
        """Keep only the N most recent sessions per user. Returns count deleted."""
        result = await self.pool.execute(
            """DELETE FROM chat_sessions
               WHERE user_id = $1
               AND id NOT IN (
                   SELECT id FROM chat_sessions
                   WHERE user_id = $1
                   ORDER BY created_at DESC
                   LIMIT $2
               )""",
            user_id, max_sessions,
        )
        return int(result.split()[-1])

    # ── Messages ────────────────────────────────────────────────────────────

    async def create_message(
        self,
        sender_name: str,
        content: str,
        channel: str = "general",
        sender_type: str = "agent",
        sender_id: int | None = None,
        message_type: str = "text",
        metadata: dict | None = None,
        reply_to: int | None = None,
    ) -> dict:
        """Store a new message. Returns message dict with id and timestamp."""
        row = await self.pool.fetchrow(
            """INSERT INTO chat_messages
               (sender_type, sender_id, sender_name, channel, message_type, content, metadata, reply_to)
               VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
               RETURNING id, sender_type, sender_id, sender_name, channel,
                         message_type, content, metadata, reply_to, created_at""",
            sender_type, sender_id, sender_name, channel, message_type,
            content, json.dumps(metadata) if metadata else None, reply_to,
        )
        result = dict(row)
        # Convert metadata back from JSON string
        if result.get("metadata") and isinstance(result["metadata"], str):
            result["metadata"] = json.loads(result["metadata"])
        return result

    async def get_messages(
        self,
        channel: str,
        limit: int = 50,
        before_id: int | None = None,
        after_id: int | None = None,
    ) -> list[dict]:
        """Fetch messages for a channel with cursor-based pagination."""
        if before_id:
            rows = await self.pool.fetch(
                """SELECT * FROM chat_messages
                   WHERE channel = $1 AND id < $2
                   ORDER BY created_at DESC LIMIT $3""",
                channel, before_id, limit,
            )
        elif after_id:
            rows = await self.pool.fetch(
                """SELECT * FROM chat_messages
                   WHERE channel = $1 AND id > $2
                   ORDER BY created_at ASC LIMIT $3""",
                channel, after_id, limit,
            )
        else:
            rows = await self.pool.fetch(
                """SELECT * FROM chat_messages
                   WHERE channel = $1
                   ORDER BY created_at DESC LIMIT $2""",
                channel, limit,
            )
        return [dict(r) for r in reversed(rows)]  # oldest first

    async def delete_message(self, message_id: int, requester_name: str, is_admin: bool = False) -> bool:
        """Delete a message. Only the sender or an admin can delete. Returns True if deleted."""
        if is_admin:
            result = await self.pool.execute(
                "DELETE FROM chat_messages WHERE id = $1", message_id
            )
        else:
            result = await self.pool.execute(
                "DELETE FROM chat_messages WHERE id = $1 AND sender_name = $2",
                message_id, requester_name,
            )
        return result == "DELETE 1"

    async def get_dm_messages(
        self, user_a: str, user_b: str, limit: int = 50, before_id: int | None = None,
    ) -> list[dict]:
        """Fetch direct messages between two participants."""
        # DM channel name is always sorted alphabetically, lowercase
        parts = sorted([user_a.lower(), user_b.lower()])
        dm_channel = f"dm:{parts[0]}:{parts[1]}"
        return await self.get_messages(dm_channel, limit, before_id)

    async def search_messages(
        self, query: str, channel: str | None = None, limit: int = 20,
    ) -> list[dict]:
        """Full-text search across messages (Spanish tsquery)."""
        if channel:
            rows = await self.pool.fetch(
                """SELECT * FROM chat_messages
                   WHERE channel = $1
                     AND to_tsvector('spanish', content) @@ plainto_tsquery('spanish', $2)
                   ORDER BY created_at DESC LIMIT $3""",
                channel, query, limit,
            )
        else:
            rows = await self.pool.fetch(
                """SELECT * FROM chat_messages
                   WHERE to_tsvector('spanish', content) @@ plainto_tsquery('spanish', $1)
                   ORDER BY created_at DESC LIMIT $2""",
                query, limit,
            )
        return [dict(r) for r in rows]

    async def get_message_count(self, channel: str | None = None) -> int:
        """Count messages, optionally by channel."""
        if channel:
            row = await self.pool.fetchrow(
                "SELECT COUNT(*) as cnt FROM chat_messages WHERE channel = $1", channel,
            )
        else:
            row = await self.pool.fetchrow("SELECT COUNT(*) as cnt FROM chat_messages")
        return row["cnt"]

    # ── Channels ────────────────────────────────────────────────────────────

    async def get_channels(self, user_id: int | None = None) -> list[dict]:
        """List channels. If user_id provided, only channels user has access to."""
        if user_id:
            rows = await self.pool.fetch(
                """SELECT c.*, ca.can_read, ca.can_write
                   FROM chat_channels c
                   LEFT JOIN chat_channel_access ca ON c.id = ca.channel_id AND ca.user_id = $1
                   WHERE c.is_private = FALSE
                      OR ca.can_read = TRUE
                   ORDER BY c.name""",
                user_id,
            )
        else:
            rows = await self.pool.fetch(
                "SELECT * FROM chat_channels WHERE is_private = FALSE ORDER BY name",
            )
        return [dict(r) for r in rows]

    async def create_dm_channel(self, user_a: str, user_b: str) -> str:
        """Get or create a DM channel between two participants. Returns channel name."""
        parts = sorted([user_a.lower(), user_b.lower()])
        dm_name = f"dm:{parts[0]}:{parts[1]}"
        await self.pool.execute(
            """INSERT INTO chat_channels (name, description, is_private)
               VALUES ($1, $2, TRUE)
               ON CONFLICT (name) DO NOTHING""",
            dm_name, f"DM between {parts[0]} and {parts[1]}",
        )
        return dm_name

    async def grant_channel_access(
        self, channel_id: int, user_id: int,
        can_read: bool = True, can_write: bool = True,
    ) -> None:
        """Grant a user access to a channel."""
        await self.pool.execute(
            """INSERT INTO chat_channel_access (channel_id, user_id, can_read, can_write)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (channel_id, user_id)
               DO UPDATE SET can_read = $3, can_write = $4""",
            channel_id, user_id, can_read, can_write,
        )

    async def user_can_access_channel(self, user_id: int, channel: str) -> bool:
        """Check if user can read a channel."""
        # DM channels — check if user is a participant (case-insensitive)
        if channel.startswith("dm:"):
            user = await self.get_user_by_id(user_id)
            if user:
                ch_lower = channel.lower()
                return (user["display_name"].lower() in ch_lower
                        or user["username"].lower() in ch_lower)
            return False

        # Public channels are always readable
        ch = await self.pool.fetchrow(
            "SELECT id, is_private FROM chat_channels WHERE name = $1", channel,
        )
        if not ch:
            return False
        if not ch["is_private"]:
            return True
        # Private channel — check access table
        access = await self.pool.fetchrow(
            "SELECT can_read FROM chat_channel_access WHERE channel_id = $1 AND user_id = $2",
            ch["id"], user_id,
        )
        return bool(access and access["can_read"])

    # ── Bulk import (for JSONL migration) ───────────────────────────────────

    async def bulk_import_messages(self, messages: list[dict]) -> int:
        """Bulk import messages from JSONL migration. Returns count imported."""
        count = 0
        async with self.pool.acquire() as conn:
            # Use a transaction for atomicity
            async with conn.transaction():
                for msg in messages:
                    await conn.execute(
                        """INSERT INTO chat_messages
                           (sender_type, sender_name, channel, message_type, content, metadata, created_at)
                           VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)""",
                        msg.get("sender_type", "agent"),
                        msg.get("sender_name", msg.get("from", "unknown")),
                        msg.get("channel", "general"),
                        msg.get("message_type", msg.get("type", "text")),
                        msg.get("content", msg.get("message", "")),
                        json.dumps(msg.get("metadata")) if msg.get("metadata") else None,
                        msg.get("created_at", datetime.now(LIMA_TZ)),
                    )
                    count += 1
        return count
