from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tools import soul_postgres_mcp as native


def test_limit_is_bounded() -> None:
    assert native._limit(-20) == 1
    assert native._limit(50) == 50
    assert native._limit(9999) == native.MAX_ROWS


def test_json_values_are_stable() -> None:
    stamp = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    assert native._json_value(stamp) == "2026-07-21T12:00:00+00:00"
    assert native._json_value(Decimal("12.50")) == 12.5


def test_expected_role_cannot_be_weakened_by_environment(monkeypatch) -> None:
    monkeypatch.setenv("SOUL_POSTGRES_MCP_ROLE", "seal")
    assert native.EXPECTED_ROLE == "mcp_observer"


def test_view_contract_lists_only_explicit_columns() -> None:
    assert set(native.EXPECTED_VIEWS) == {
        "agent_work_status_v",
        "tool_latency_health",
        "v_agent_tools_boot",
        "v_tool_broker_observe_rollup",
    }
    assert native.EXPECTED_VIEWS["agent_work_status_v"]["columns"] == (
        "agent",
        "status",
        "item_count",
        "last_updated_at",
    )


@pytest.mark.asyncio
async def test_work_status_uses_fixed_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_fetch(sql: str, *args: object):
        captured["sql"] = sql
        captured["args"] = args
        return [{"agent": "ADA", "status": "in_progress"}]

    monkeypatch.setattr(native, "_fetch", fake_fetch)
    result = await native.soul_agent_work_status("ADA", "in_progress", 999)
    assert result["ok"] is True
    assert "soul_v3.agent_work_status_v" in str(captured["sql"])
    assert captured["args"] == ("ADA", "in_progress", native.MAX_ROWS)


@pytest.mark.asyncio
async def test_inventory_does_not_accept_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_fetch(sql: str, *args: object):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(native, "_fetch", fake_fetch)
    payload = "up'; DROP TABLE soul_v3.memories; --"
    result = await native.soul_tools_inventory(payload, "", "", 10)
    assert result == {"ok": True, "count": 0, "rows": []}
    assert payload not in str(captured["sql"])
    assert captured["args"][0] == payload
