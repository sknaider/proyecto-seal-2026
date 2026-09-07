from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


MODULE = Path(__file__).with_name("nerves_public_ack_canary.py")
SPEC = importlib.util.spec_from_file_location("public_ack_canary", MODULE)
canary = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(canary)


class FakeConn:
    def __init__(self, *, channel: str = "web_chat", bound: bool = True):
        self.channel = channel
        self.bound = bound

    async def fetchrow(self, query, value):
        if "WHERE id = $1" in query:
            return {
                "id": value,
                "sender_name": "William",
                "channel": self.channel,
                "created_at": datetime(
                    2026, 7, 24, 19, 30, 44, 454127, tzinfo=timezone.utc
                ),
                "metadata": json.dumps(
                    {
                        "legacy_id": "api_william_request",
                        "session_user": "William",
                    }
                ),
            }
        return {
            "id": 117575,
            "sender_name": "ADA",
            "channel": "web_chat",
            "created_at": datetime(
                2026, 7, 24, 19, 30, 44, 927763, tzinfo=timezone.utc
            ),
            "metadata": json.dumps(
                {
                    "legacy_id": "api_ada_ack",
                    "in_reply_to": (
                        value if self.bound else "api_william_wrong"
                    ),
                    "session_user": "ADA",
                }
            ),
        }

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_collects_bound_public_ack_without_content(monkeypatch):
    async def connect(_dsn):
        return FakeConn()

    monkeypatch.setattr(canary.asyncpg, "connect", connect)
    evidence, latency_ms = await canary.collect("dsn", 117574)
    assert latency_ms == 474
    assert evidence["channel"] == "web_chat"
    assert evidence["request_id"] == 117574
    assert evidence["ack_id"] == 117575
    assert "content" not in evidence


@pytest.mark.asyncio
async def test_rejects_private_request(monkeypatch):
    async def connect(_dsn):
        return FakeConn(channel="dm:ada:william")

    monkeypatch.setattr(canary.asyncpg, "connect", connect)
    with pytest.raises(
        canary.AckCanaryError, match="public_william_request_not_found"
    ):
        await canary.collect("dsn", 117574)


@pytest.mark.asyncio
async def test_rejects_unbound_ack(monkeypatch):
    async def connect(_dsn):
        return FakeConn(bound=False)

    monkeypatch.setattr(canary.asyncpg, "connect", connect)
    with pytest.raises(
        canary.AckCanaryError, match="ack_identity_or_binding_invalid"
    ):
        await canary.collect("dsn", 117574)
