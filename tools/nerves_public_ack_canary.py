#!/usr/bin/env python3
"""Record one content-free public William→ADA ACK latency canary."""

from __future__ import annotations

import argparse
import asyncio
from datetime import timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

import asyncpg


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.nerves_a2_canary_record import record_principal_ack  # noqa: E402


class AckCanaryError(RuntimeError):
    """The selected database rows do not prove a public bound ACK."""


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise AckCanaryError("metadata_not_object")
    return value


async def collect(dsn: str, request_id: int) -> tuple[dict[str, object], int]:
    conn = await asyncpg.connect(dsn)
    try:
        request = await conn.fetchrow(
            """
            SELECT id, sender_name, channel, created_at, metadata
              FROM soul_v3.chat_messages
             WHERE id = $1
            """,
            request_id,
        )
        if (
            request is None
            or request["sender_name"] != "William"
            or request["channel"] != "web_chat"
        ):
            raise AckCanaryError("public_william_request_not_found")
        request_meta = _metadata(request["metadata"])
        request_legacy = request_meta.get("legacy_id")
        if (
            request_meta.get("session_user") != "William"
            or not isinstance(request_legacy, str)
            or not request_legacy.startswith("api_william_")
        ):
            raise AckCanaryError("request_identity_invalid")
        ack = await conn.fetchrow(
            """
            SELECT id, sender_name, channel, created_at, metadata
              FROM soul_v3.chat_messages
             WHERE channel = 'web_chat'
               AND UPPER(sender_name) = 'ADA'
               AND metadata->>'in_reply_to' = $1
             ORDER BY created_at, id
             LIMIT 1
            """,
            request_legacy,
        )
        if ack is None:
            raise AckCanaryError("bound_ada_ack_missing")
        ack_meta = _metadata(ack["metadata"])
        ack_legacy = ack_meta.get("legacy_id")
        if (
            ack["sender_name"].upper() != "ADA"
            or ack_meta.get("session_user") != "ADA"
            or ack_meta.get("in_reply_to") != request_legacy
            or not isinstance(ack_legacy, str)
            or not ack_legacy.startswith("api_ada_")
        ):
            raise AckCanaryError("ack_identity_or_binding_invalid")
        latency_ms = int(
            round(
                (ack["created_at"] - request["created_at"]).total_seconds()
                * 1000
            )
        )
        if latency_ms < 0:
            raise AckCanaryError("ack_precedes_request")
        evidence: dict[str, object] = {
            "schema": "seal.nerves.principal-ack-evidence.v1",
            "channel": "web_chat",
            "request_id": int(request["id"]),
            "request_legacy_id": request_legacy,
            "request_created_at": request["created_at"]
            .astimezone(timezone.utc)
            .isoformat(),
            "ack_id": int(ack["id"]),
            "ack_legacy_id": ack_legacy,
            "ack_created_at": ack["created_at"]
            .astimezone(timezone.utc)
            .isoformat(),
            "in_reply_to": ack_meta["in_reply_to"],
        }
        return evidence, latency_ms
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", type=int, required=True)
    args = parser.parse_args()
    dsn = os.environ.get("SEAL_DB_DSN", "")
    if not dsn:
        print(json.dumps({"ok": False, "error": "SEAL_DB_DSN_required"}))
        return 2
    try:
        evidence, latency_ms = asyncio.run(collect(dsn, args.request_id))
        if latency_ms > 2_000:
            raise AckCanaryError("principal_ack_sla_exceeded")
        path = record_principal_ack(
            evidence=evidence,
            principal_ack_latency_ms=latency_ms,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)}, sort_keys=True
            )
        )
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "request_id": args.request_id,
                "ack_id": evidence["ack_id"],
                "latency_ms": latency_ms,
                "channel": "web_chat",
                "content_read": False,
                "private_channels_read": 0,
                "mutations": 0,
                "soak_record": str(path),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
