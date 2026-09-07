"""Tests for capability_gate.py"""
import asyncio
import pytest
import asyncpg
import json

_DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"


@pytest.mark.asyncio
async def test_allowed_capability():
    from capability_gate import check_capability
    result = await check_capability("ADA", "shell")
    assert result["allowed"] is True
    assert "reason" in result


@pytest.mark.asyncio
async def test_denied_capability():
    from capability_gate import check_capability
    result = await check_capability("ALICE", "shell")
    assert result["allowed"] is False
    assert "denied" in result["reason"].lower() or "deny" in result["reason"].lower()


@pytest.mark.asyncio
async def test_no_entry_denies():
    from capability_gate import check_capability
    result = await check_capability("DUM", "camera")
    assert result["allowed"] is False
    assert "deny by default" in result["reason"]


@pytest.mark.asyncio
async def test_jarvis_denied_ops():
    from capability_gate import check_capability
    # JARVIS shell allowed but code_edit denied
    result = await check_capability("JARVIS", "shell", operation="code_edit")
    assert result["allowed"] is False
    assert "denied_ops" in result["reason"]


@pytest.mark.asyncio
async def test_jarvis_allowed_ops():
    from capability_gate import check_capability
    result = await check_capability("JARVIS", "shell", operation="query")
    assert result["allowed"] is True


@pytest.mark.asyncio
async def test_high_risk_requires_confirmation():
    from capability_gate import check_capability
    result = await check_capability("ADA", "shell", operation="network")
    assert result["allowed"] is True
    assert result["requires_confirmation"] is True


@pytest.mark.asyncio
async def test_audit_recorded():
    from capability_gate import gate
    conn = await asyncpg.connect(_DB_URL)
    count_before = await conn.fetchval("SELECT COUNT(*) FROM soul_v3.capability_audit")
    await gate("NEXUS", "file_read", operation="read", context="test audit", authorized_by="NEXUS_test")
    count_after = await conn.fetchval("SELECT COUNT(*) FROM soul_v3.capability_audit")
    await conn.close()
    assert count_after > count_before


@pytest.mark.asyncio
async def test_gate_deny_returns_false():
    from capability_gate import gate
    conn = await asyncpg.connect(_DB_URL)
    count_before = await conn.fetchval("SELECT COUNT(*) FROM soul_v3.denial_tracker")
    await conn.close()
    result = await gate("ALICE", "shell", operation="query", context="test deny")
    assert result["allowed"] is False
    conn = await asyncpg.connect(_DB_URL)
    count_after = await conn.fetchval("SELECT COUNT(*) FROM soul_v3.denial_tracker")
    await conn.execute(
        """
        DELETE FROM soul_v3.denial_tracker
        WHERE source='capability_gate'
          AND context->>'context'='test deny'
          AND agent='ALICE'
        """
    )
    await conn.close()
    assert count_after > count_before
