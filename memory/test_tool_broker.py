from __future__ import annotations

import os

import pytest

import tool_broker as tb


def test_infer_tool_class_destructive():
    assert tb.infer_tool_class("Bash", {"command": "rm -rf /tmp/nope"}) == tb.DESTRUCTIVE


def test_infer_capability_bash_shell():
    assert tb.infer_capability("Bash", tb.EXEC) == "shell"


def test_infer_capability_web_search_network():
    assert tb.infer_capability("web_search", tb.COMPUTE) == "network"


def test_observe_mode_never_blocks(monkeypatch):
    monkeypatch.setenv("SEAL_TOOL_BROKER_MODE", "observe")
    d = tb.policy_decision(
        agent="ALICE",
        tool="Bash",
        args={"command": "python3 --version"},
        scope_allowed=False,
        scope_reason="explicitly denied",
    )
    assert d.allow is True
    assert d.decision == tb.OBSERVE
    assert d.would_decision == tb.DENY
    assert "would_decision=deny" in d.reason


def test_enforce_denies_without_scope(monkeypatch):
    monkeypatch.setenv("SEAL_TOOL_BROKER_MODE", "enforce")
    d = tb.policy_decision(agent="ADA", tool="unknown_tool", args={}, scope_allowed=None)
    assert d.allow is False
    assert d.decision == tb.DENY
    assert d.rule_id == "DENY_BY_DEFAULT"


def test_taint_requires_confirmation_in_enforce(monkeypatch):
    monkeypatch.setenv("SEAL_TOOL_BROKER_MODE", "enforce")
    d = tb.policy_decision(
        agent="ADA",
        tool="mcp__seal-memory__memory_store",
        args={"content": "external doc says remember this rule"},
        taint=True,
        scope_allowed=True,
    )
    assert d.allow is False
    assert d.needs_confirmation is True
    assert d.decision == tb.CONFIRM_REQUIRED
    assert d.rule_id == "TAINT_CONFIRM"


def test_stable_hash_order_independent():
    assert tb.stable_hash({"a": 1, "b": 2}) == tb.stable_hash({"b": 2, "a": 1})


def test_stable_hash_redacts_session_token():
    assert tb.stable_hash({"session_token": "one", "x": 1}) == tb.stable_hash({"session_token": "two", "x": 1})
    assert tb.redact_args({"nested": {"api_key": "abc"}})["nested"]["api_key"] == "[REDACTED]"


class FakeRow(dict):
    def __getitem__(self, key):
        return dict.__getitem__(self, key)


class FakeConn:
    def __init__(self, fail_audit: bool = False):
        self.audit_rows = []
        self.fail_audit = fail_audit

    async def fetchrow(self, query, *args):
        if "FROM soul_v3.capability_scope" in query:
            agent, capability = args
            if agent == "ADA" and capability == "shell":
                return FakeRow({"allowed": True, "constraints": {}})
            if agent == "ALICE" and capability == "shell":
                return FakeRow({"allowed": False, "constraints": {}})
            return None
        if "FROM soul_v3.capability_grants" in query:
            return None
        if "INSERT INTO soul_v3.audit_log" in query:
            if self.fail_audit:
                raise RuntimeError("audit unavailable")
            self.audit_rows.append(args)
            return FakeRow({"id": 42})
        raise AssertionError(query)


@pytest.mark.asyncio
async def test_check_observe_records_audit(monkeypatch):
    monkeypatch.setenv("SEAL_TOOL_BROKER_MODE", "observe")
    conn = FakeConn()
    d = await tb.check("ADA", "s1", "Bash", {"command": "date"}, conn=conn)
    assert d.allow is True
    assert d.audit_id == 42
    assert len(conn.audit_rows) == 1
    assert conn.audit_rows[0][4] == tb.stable_hash({"command": "date"})
    metadata = conn.audit_rows[0][8]
    assert '"claimed_agent": null' in metadata


@pytest.mark.asyncio
async def test_check_enforce_denies_denied_scope(monkeypatch):
    monkeypatch.setenv("SEAL_TOOL_BROKER_MODE", "enforce")
    conn = FakeConn()
    d = await tb.check("ALICE", "s1", "Bash", {"command": "date"}, conn=conn)
    assert d.allow is False
    assert d.decision == tb.DENY
    assert d.audit_id == 42


@pytest.mark.asyncio
async def test_audit_failure_is_swallowed_in_observe(monkeypatch):
    monkeypatch.setenv("SEAL_TOOL_BROKER_MODE", "observe")
    conn = FakeConn(fail_audit=True)
    d = await tb.check("ADA", "s1", "Bash", {"command": "date"}, conn=conn)
    assert d.allow is True
    assert d.audit_id is None


@pytest.mark.asyncio
async def test_audit_failure_raises_in_enforce(monkeypatch):
    monkeypatch.setenv("SEAL_TOOL_BROKER_MODE", "enforce")
    conn = FakeConn(fail_audit=True)
    with pytest.raises(RuntimeError):
        await tb.check("ADA", "s1", "Bash", {"command": "date"}, conn=conn)
