#!/usr/bin/env python3
"""Read-only William approval verifier for SOUL consolidation batches."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seal_secrets import pg_dsn


DB_URL = os.environ.get("SEAL_PG_DSN", pg_dsn(required=True))
WILLIAM_USERNAMES = {"william", "dadito"}
APPROVAL_RE = re.compile(
    r"^\s*OK\s+ADA\s+aplica\s+lote\s+"
    r"(?P<agent>[A-Z][A-Z0-9_]*)\s+"
    r"(?P<action>[a-z0-9_]+)\s+"
    r"count=(?P<count>[0-9]+)"
    r"(?:\s+manifest=(?P<manifest>[a-f0-9]{64}))?\s*$",
    re.IGNORECASE,
)
MATRIX_PREFIX_RE = re.compile(r"^\s*\[Matrix\]\s*", re.IGNORECASE)


@dataclass(frozen=True)
class ApprovalRequest:
    agent: str
    action: str
    count: int
    manifest_sha256: str | None = None


@dataclass(frozen=True)
class ApprovalVerification:
    ok: bool
    message_id: int | None
    reason: str
    request: ApprovalRequest | None = None
    sender: dict[str, Any] | None = None


def parse_approval_text(text: str) -> ApprovalRequest | None:
    normalized = MATRIX_PREFIX_RE.sub("", text or "", count=1)
    match = APPROVAL_RE.match(normalized)
    if not match:
        return None
    return ApprovalRequest(
        agent=match.group("agent").upper(),
        action=match.group("action").lower(),
        count=int(match.group("count")),
        manifest_sha256=(match.group("manifest") or "").lower() or None,
    )


def _hash_session_key(session_key: str) -> str:
    return hashlib.sha256(session_key.encode()).hexdigest()


def _is_william_user(username: str | None, display_name: str | None) -> bool:
    values = {(username or "").strip().lower(), (display_name or "").strip().lower()}
    return bool(values & WILLIAM_USERNAMES)


async def _session_key_belongs_to_william(conn: asyncpg.Connection, session_key: str) -> bool:
    return await _session_token_hash_belongs_to_william(conn, _hash_session_key(session_key))


async def _session_token_hash_belongs_to_william(conn: asyncpg.Connection, token_hash: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT u.username, u.display_name
        FROM soul_v3.chat_sessions s
        JOIN soul_v3.chat_users u ON u.id = s.user_id
        WHERE s.token_hash=$1 AND s.expires_at > now()
        """,
        token_hash,
    )
    return bool(row and _is_william_user(row["username"], row["display_name"]))


async def verify_approval_message(
    message_id: int,
    expected_agent: str | None = None,
    expected_action: str | None = None,
    expected_count: int | None = None,
    expected_manifest_sha256: str | None = None,
) -> ApprovalVerification:
    conn = await asyncpg.connect(DB_URL)
    try:
        row = await conn.fetchrow(
            """
            SELECT m.id, m.sender_type, m.sender_id, m.sender_name, m.content, m.metadata,
                   u.username, u.display_name
            FROM soul_v3.chat_messages m
            LEFT JOIN soul_v3.chat_users u ON u.id = m.sender_id
            WHERE m.id=$1
            """,
            message_id,
        )
        if not row:
            return ApprovalVerification(False, message_id, "message_not_found")

        request = parse_approval_text(row["content"] or "")
        sender = {
            "sender_type": row["sender_type"],
            "sender_id": row["sender_id"],
            "sender_name": row["sender_name"],
            "username": row["username"],
            "display_name": row["display_name"],
        }
        if not request:
            return ApprovalVerification(False, message_id, "approval_phrase_invalid", sender=sender)

        if expected_agent and request.agent != expected_agent.upper():
            return ApprovalVerification(False, message_id, "agent_mismatch", request, sender)
        if expected_action and request.action != expected_action.lower():
            return ApprovalVerification(False, message_id, "action_mismatch", request, sender)
        if expected_count is not None and request.count != expected_count:
            return ApprovalVerification(False, message_id, "count_mismatch", request, sender)
        if expected_manifest_sha256:
            if not request.manifest_sha256:
                return ApprovalVerification(False, message_id, "manifest_required", request, sender)
            if request.manifest_sha256 != expected_manifest_sha256.lower():
                return ApprovalVerification(False, message_id, "manifest_mismatch", request, sender)

        if row["sender_type"] == "user" and _is_william_user(row["username"], row["display_name"]):
            return ApprovalVerification(True, message_id, "authenticated_user_message", request, sender)

        metadata = row["metadata"] or {}
        session_token_hash = metadata.get("session_token_hash") if isinstance(metadata, dict) else None
        if session_token_hash and await _session_token_hash_belongs_to_william(conn, str(session_token_hash)):
            return ApprovalVerification(True, message_id, "authenticated_session_token_hash", request, sender)

        session_key = metadata.get("session_key") if isinstance(metadata, dict) else None
        if session_key and await _session_key_belongs_to_william(conn, str(session_key)):
            return ApprovalVerification(True, message_id, "authenticated_session_key", request, sender)

        return ApprovalVerification(False, message_id, "not_authenticated_william", request, sender)
    finally:
        await conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify an authenticated William consolidation approval.")
    parser.add_argument("--message-id", type=int, required=True)
    parser.add_argument("--agent")
    parser.add_argument("--action")
    parser.add_argument("--count", type=int)
    parser.add_argument("--manifest-sha256")
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = await verify_approval_message(
        args.message_id,
        args.agent,
        args.action,
        args.count,
        args.manifest_sha256,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.ok else 2


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
