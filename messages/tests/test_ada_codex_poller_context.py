from __future__ import annotations

from datetime import datetime, timezone

import pytest

from messages import ada_codex_poller as poller


class FakeConn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self.rows


@pytest.mark.asyncio
async def test_fetch_recent_context_uses_default_eight_message_limit():
    conn = FakeConn()

    await poller.fetch_recent_context(conn, "web_chat", 75287)

    sql, args = conn.calls[0]
    assert args == ("web_chat", 75287, 8)
    assert "id < $2" in sql
    assert "ORDER BY id DESC" in sql
    assert "content NOT ILIKE '[STATUS]%'" in sql
    assert "content NOT ILIKE '[HEARTBEAT]%'" in sql
    assert "content !~ '^\\[SILENT\\]$'" in sql


def test_format_context_block_is_single_line_and_truncates_long_messages():
    rows = [
        {
            "created_at": datetime(2026, 5, 29, 17, 50, tzinfo=timezone.utc),
            "sender_name": "William",
            "content": "linea uno\n" + ("x" * 260),
        },
        {
            "created_at": datetime(2026, 5, 29, 17, 51, tzinfo=timezone.utc),
            "sender_name": "ALICE",
            "content": "fix listo",
        },
    ]

    block = poller.format_context_block(rows)

    assert block.startswith("[CTX2]")
    assert "\n" not in block
    assert "William:" in block
    assert "ALICE: fix listo" in block
    assert "..." in block
    assert len(block) < 360


def test_context_limit_is_enabled_for_terminal_poller():
    assert poller.CONTEXT_RECENT_LIMIT == 8
