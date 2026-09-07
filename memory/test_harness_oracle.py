from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import harness_oracle as ho


class FakeRow(dict):
    def __getitem__(self, key):
        return dict.__getitem__(self, key)


class FakeConn:
    def __init__(self):
        self.registry = {
            "ada_bridge_cursor": FakeRow({
                "state_key": "ada_bridge_cursor",
                "canonical_query": "SELECT cursor",
                "owner_agent": "ADA",
                "liveness_max_lag": timedelta(minutes=10),
            }),
            "fresh_cursor": FakeRow({
                "state_key": "fresh_cursor",
                "canonical_query": "SELECT fresh_cursor",
                "owner_agent": "ADA",
                "liveness_max_lag": timedelta(minutes=10),
            }),
        }
        self.query_values = {
            "SELECT cursor": 92100,
            "SELECT fresh_cursor": datetime.now(timezone.utc),
        }
        self.rows = []
        self.alerts = []
        self.next_id = 1
        self.recent_escalation = False

    async def fetchrow(self, query, *args):
        if "FROM soul_v3.harness_truth_registry" in query:
            return self.registry.get(args[0])
        raise AssertionError(query)

    async def fetchval(self, query, *args):
        if query in self.query_values:
            return self.query_values[query]
        if "SELECT EXISTS" in query:
            return self.recent_escalation
        if "INSERT INTO soul_v3.harness_oracle" in query:
            row_id = self.next_id
            self.next_id += 1
            self.rows.append({
                "id": row_id,
                "agent": args[0],
                "state_key": args[1],
                "belief_hash": args[2],
                "true_hash": args[3],
                "belief_snippet": args[4],
                "diverged": args[5],
                "divergence_kind": args[6],
                "escalated_to": args[7],
                "escalation_delivered": args[8],
            })
            return row_id
        raise AssertionError(query)

    async def fetch(self, query, *args):
        if "FROM soul_v3.harness_truth_registry" in query:
            return list(self.registry.values())
        raise AssertionError(query)


def test_redact_masks_secrets_and_truncates():
    text = ho._redact("token=abc123 password:xyz " + ("a" * 200))
    assert "abc123" not in text
    assert "xyz" not in text
    assert "[REDACTED]" in text
    assert len(text) <= ho._SNIPPET_MAX + 1


@pytest.mark.asyncio
async def test_check_at_use_detects_stale_read_and_returns_true_value(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(ho, "_send_live_alert", lambda owner, text: conn.alerts.append((owner, text)) or True)

    result = await ho.check_at_use("ADA", "ada_bridge_cursor", 90900, conn=conn)

    assert result["diverged"] is True
    assert result["true_value"] == 92100
    assert result["escalated_to"] == "ADA"
    assert result["escalation_delivered"] is True
    assert conn.rows[-1]["divergence_kind"] == "stale_read"
    assert conn.alerts and conn.alerts[-1][0] == "ADA"


@pytest.mark.asyncio
async def test_check_at_use_is_silent_when_belief_matches(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(ho, "_send_live_alert", lambda owner, text: conn.alerts.append((owner, text)) or True)

    result = await ho.check_at_use("ADA", "ada_bridge_cursor", 92100, conn=conn)

    assert result["diverged"] is False
    assert result["escalation_delivered"] is False
    assert conn.rows[-1]["diverged"] is False
    assert conn.alerts == []


@pytest.mark.asyncio
async def test_unregistered_state_key_fails_loud():
    conn = FakeConn()
    with pytest.raises(KeyError):
        await ho.check_at_use("ADA", "missing_state", 1, conn=conn)


@pytest.mark.asyncio
async def test_cooldown_suppresses_repeated_live_alert(monkeypatch):
    conn = FakeConn()
    conn.recent_escalation = True
    monkeypatch.setattr(ho, "_send_live_alert", lambda owner, text: conn.alerts.append((owner, text)) or True)

    result = await ho.check_at_use("ADA", "ada_bridge_cursor", 90900, conn=conn)

    assert result["diverged"] is True
    assert result["escalation_delivered"] is False
    assert conn.rows[-1]["escalation_delivered"] is False
    assert conn.alerts == []


@pytest.mark.asyncio
async def test_liveness_watcher_detects_frozen_timestamp(monkeypatch):
    conn = FakeConn()
    conn.query_values["SELECT cursor"] = datetime.now(timezone.utc) - timedelta(hours=1)
    monkeypatch.setattr(ho, "_send_live_alert", lambda owner, text: conn.alerts.append((owner, text)) or True)

    results = await ho.check_liveness_once(agent="ADA", conn=conn)
    frozen = [row for row in results if row["state_key"] == "ada_bridge_cursor"][0]
    fresh = [row for row in results if row["state_key"] == "fresh_cursor"][0]

    assert frozen["diverged"] is True
    assert frozen["divergence_kind"] == "frozen_liveness"
    assert fresh["diverged"] is False
    assert conn.rows[-1]["divergence_kind"] == "frozen_liveness"


def test_non_datetime_liveness_value_is_not_marked_frozen():
    assert ho._is_frozen(92100, timedelta(minutes=10), datetime.now(timezone.utc)) is False
