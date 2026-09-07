from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server_v4 import _agent_task_miss_result


class _Conn:
    def __init__(self, state: str | None):
        self.state = state
        self.query = ""
        self.args = ()

    async def fetchval(self, query, *args):
        self.query = query
        self.args = args
        return self.state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected_code"),
    [
        ("out_of_scope", "out_of_scope"),
        ("not_found", "not_found"),
        ("owned", "visibility_inconsistent"),
        (None, "visibility_indeterminate"),
        ("unexpected", "visibility_indeterminate"),
    ],
)
async def test_agent_task_miss_returns_discriminating_code(
    state: str | None, expected_code: str
) -> None:
    conn = _Conn(state)

    result = await _agent_task_miss_result(conn, 934)

    assert result["code"] == expected_code
    assert result["task_id"] == 934
    assert conn.query == "SELECT soul_v3.agent_task_visibility($1)"
    assert conn.args == (934,)


def test_visibility_migration_exposes_only_classification() -> None:
    sql = (
        Path(__file__).parent
        / "migrations"
        / "20260829_agent_task_visibility_probe.sql"
    ).read_text()

    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, soul_v3" in sql
    assert "REVOKE ALL ON FUNCTION" in sql
    assert "FROM PUBLIC" in sql
    assert "mcp_session_agent()" in sql
    assert "'not_found'" in sql
    assert "'owned'" in sql
    assert "'out_of_scope'" in sql
    assert "SELECT agent" in sql
    function_body = sql.split("AS $function$", 1)[1].split("$function$;", 1)[0]
    for forbidden in ("title", "description", "evidence", "completed_at"):
        assert forbidden not in function_body
