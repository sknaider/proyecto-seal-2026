from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_server_v4 import (
    _agent_task_description_append,
    _agent_task_update_note,
    _append_agent_task_description,
)


class _Conn:
    def __init__(self, row=None):
        self.row = row
        self.query = ""
        self.args = ()

    async def fetchrow(self, query, *args):
        self.query = query
        self.args = args
        return self.row


def test_description_append_normalizes_and_rejects_invalid_payloads() -> None:
    assert _agent_task_description_append("  VERIFICAR_CON: exit 0  ") == "VERIFICAR_CON: exit 0"
    with pytest.raises(ValueError, match="vacío"):
        _agent_task_description_append("   ")
    with pytest.raises(ValueError, match="8000"):
        _agent_task_description_append("x" * 8001)


@pytest.mark.asyncio
async def test_update_is_append_only_and_scoped_to_calling_agent() -> None:
    row = {"id": 1543, "agent": "ALICE", "description": "viejo\n\nnuevo"}
    conn = _Conn(row)

    result = await _append_agent_task_description(
        conn, 1543, "ALICE", "  VERIFICAR_CON: exit 0  "
    )

    assert result == row
    assert "description ||" in conn.query
    assert "WHERE id = $1 AND agent = $2" in conn.query
    assert conn.args == (1543, "ALICE", "VERIFICAR_CON: exit 0")


@pytest.mark.asyncio
async def test_update_cannot_find_another_agents_task() -> None:
    conn = _Conn(None)
    result = await _append_agent_task_description(conn, 1543, "ADA", "nota")
    assert result is None
    assert conn.args[:2] == (1543, "ADA")


def test_server_keeps_legacy_description_field_as_update_fallback() -> None:
    assert _agent_task_update_note("nuevo", "viejo") == "nuevo"
    assert _agent_task_update_note("", "cliente-viejo") == "cliente-viejo"
