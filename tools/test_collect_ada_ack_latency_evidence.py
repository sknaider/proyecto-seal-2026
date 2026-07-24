from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest


MODULE = Path(__file__).with_name("collect_ada_ack_latency_evidence.py")
SPEC = importlib.util.spec_from_file_location("collect_ack", MODULE)
collector = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(collector)


class FakeConn:
    def __init__(self, *, bad_channel: bool = False):
        self.bad_channel = bad_channel

    async def fetch(self, query, value):
        if "id = ANY" in query:
            return [
                {
                    "id": request_id,
                    "sender_name": "William",
                    "channel": "dm:ada:william" if self.bad_channel else "web_chat",
                    "created_at": datetime.fromisoformat(
                        f"2026-07-{day:02d}T10:00:00+00:00"
                    ),
                    "metadata": json.dumps(
                        {
                            "legacy_id": f"api_william_{request_id}",
                            "session_user": "William",
                        }
                    ),
                }
                for request_id, day in zip(collector.REQUEST_IDS, (15, 21, 23))
            ]
        source_id = value
        request_id = int(source_id.rsplit("_", 1)[1])
        return [
            {
                "id": request_id + 1,
                "sender_name": "ADA",
                "channel": "web_chat",
                "created_at": datetime.fromisoformat(
                    "2026-07-23T10:01:00+00:00"
                ),
                "metadata": json.dumps(
                    {
                        "legacy_id": f"api_ada_{request_id + 1}",
                        "in_reply_to": source_id,
                        "session_user": "ADA",
                    }
                ),
            }
        ]

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_collector_reads_only_public_rows(monkeypatch):
    async def connect(_dsn):
        return FakeConn()

    monkeypatch.setattr(collector.asyncpg, "connect", connect)
    result = await collector.collect("dsn")
    assert len(result["observations"]) == 3
    assert {item["channel"] for item in result["observations"]} == {"web_chat"}


@pytest.mark.asyncio
async def test_collector_rejects_private_channel(monkeypatch):
    async def connect(_dsn):
        return FakeConn(bad_channel=True)

    monkeypatch.setattr(collector.asyncpg, "connect", connect)
    with pytest.raises(RuntimeError, match="request_scope_invalid"):
        await collector.collect("dsn")
