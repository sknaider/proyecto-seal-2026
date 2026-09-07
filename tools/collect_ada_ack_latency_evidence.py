#!/usr/bin/env python3
"""Collect three public William→ADA late-ACK incidents without message content."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

import asyncpg


ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPT = (
    ROOT
    / "skills/seal-ada-ack-latency-triage/scripts/ack_latency_triage.py"
)
sys.path.insert(0, str(SKILL_SCRIPT.parent))

from ack_latency_triage import PATTERN_ID, audit_bundle, canonical_bytes  # noqa: E402


REQUEST_IDS = (110560, 115414, 117182)


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise RuntimeError("metadata_not_object")
    return value


def _row(row: asyncpg.Record, *, sender: str, in_reply_to: str | None) -> dict:
    metadata = _metadata(row["metadata"])
    if metadata.get("session_user") != sender:
        raise RuntimeError("session_user_mismatch")
    legacy_id = metadata.get("legacy_id")
    if not isinstance(legacy_id, str) or not legacy_id.startswith("api_"):
        raise RuntimeError("legacy_id_invalid")
    actual_reply = metadata.get("in_reply_to")
    if actual_reply != in_reply_to:
        raise RuntimeError("reply_binding_mismatch")
    return {
        "id": int(row["id"]),
        "legacy_id": legacy_id,
        "sender": sender,
        "created_at": row["created_at"].isoformat(),
        "in_reply_to": actual_reply,
    }


async def collect(dsn: str) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn)
    try:
        requests = await conn.fetch(
            """
            SELECT id, sender_name, channel, created_at, metadata
              FROM soul_v3.chat_messages
             WHERE id = ANY($1::bigint[])
             ORDER BY id
            """,
            list(REQUEST_IDS),
        )
        if [int(row["id"]) for row in requests] != list(REQUEST_IDS):
            raise RuntimeError("required_public_requests_missing")
        observations: list[dict[str, Any]] = []
        for request in requests:
            if (
                request["channel"] != "web_chat"
                or request["sender_name"] != "William"
            ):
                raise RuntimeError("request_scope_invalid")
            request_metadata = _metadata(request["metadata"])
            request_row = _row(request, sender="William", in_reply_to=None)
            replies = await conn.fetch(
                """
                SELECT id, sender_name, channel, created_at, metadata
                  FROM soul_v3.chat_messages
                 WHERE channel = 'web_chat'
                   AND UPPER(sender_name) = 'ADA'
                   AND metadata->>'in_reply_to' = $1
                 ORDER BY created_at, id
                 LIMIT 2
                """,
                request_metadata["legacy_id"],
            )
            if not replies:
                raise RuntimeError("durable_ack_missing")
            ack = _row(
                replies[0],
                sender="ADA",
                in_reply_to=request_metadata["legacy_id"],
            )
            recovery = (
                _row(
                    replies[1],
                    sender="ADA",
                    in_reply_to=request_metadata["legacy_id"],
                )
                if len(replies) > 1
                else None
            )
            observations.append(
                {
                    "observation_id": (
                        f"ada-public-ack-{request['id']}-{replies[0]['id']}"
                    ),
                    "pattern_id": PATTERN_ID,
                    "source_kind": "public_web_chat",
                    "channel": "web_chat",
                    "request": request_row,
                    "ack": ack,
                    "recovery": recovery,
                }
            )
        bundle = {
            "schema": "seal.ada.ack_latency_bundle.v1",
            "pattern_id": PATTERN_ID,
            "sla_seconds": 20,
            "observations": observations,
        }
        audit_bundle(bundle)
        return bundle
    finally:
        await conn.close()


def _write_private(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical_bytes(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(raw, 0o600)
        os.replace(raw, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(raw):
            os.unlink(raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    dsn = os.environ.get("SEAL_DB_DSN", "")
    if not dsn:
        print(json.dumps({"ok": False, "error": "SEAL_DB_DSN_required"}))
        return 2
    try:
        bundle = asyncio.run(collect(dsn))
        _write_private(args.output, bundle)
        result = audit_bundle(bundle)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "observations": result["observation_count"],
                "time_windows": result["time_windows"],
                "late_ack_count": result["late_ack_count"],
                "result_sha256": result["result_sha256"],
                "private_channels_read": 0,
                "mutations": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
