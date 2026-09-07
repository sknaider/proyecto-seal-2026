"""Pytest quality arms for the portable SOUL F1 bundle."""

from __future__ import annotations

import re
import sys
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MEMORY = ROOT / "memory"
sys.path.insert(0, str(MEMORY))

from soul_event_interface import (  # noqa: E402
    HookRegistration,
    SoulEventEnvelope,
    dispatch,
    to_soul_event,
)
from test_soul_event_interface import main as event_suite  # noqa: E402
from test_soul_hooks_seed import main as seed_suite  # noqa: E402
from test_soul_runtime_adapter import main as adapter_suite  # noqa: E402


def _assert_matcher_routes_one() -> None:
    registry = [
        HookRegistration("on_file_change", "watch.py", matcher="ada.jsonl"),
        HookRegistration("on_file_change", "watch.py", matcher="nexus.jsonl"),
    ]
    envelope = SoulEventEnvelope(
        "on_file_change", "ADA", "session", "2026-08-05T22:00:00-05:00",
        "claude_code", {"path": "/tmp/ada.jsonl"},
    )
    calls: list[str] = []
    result = dispatch(envelope, registry, lambda script, _event: calls.append(script) or True)
    assert result["selected"] == ["watch.py"]
    assert calls == ["watch.py"]
    assert result["all_ran"] is True


def _assert_rls_sql_contract(sql: str) -> None:
    compact = re.sub(r"\s+", " ", sql).upper()
    assert "UNIQUE NULLS NOT DISTINCT (SOUL_EVENT, SCRIPT_PATH, AGENT, MATCHER)" in compact
    assert "AS RESTRICTIVE FOR SELECT" in compact
    assert "SOUL_V3.MCP_SESSION_AGENT()" in compact
    assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER" in compact
    assert "GRANT SELECT ON SOUL_V3.RUNTIME_HOOKS" in compact
    assert not re.search(r"GRANT\s+[^;]*(INSERT|UPDATE|DELETE|TRUNCATE)[^;]*RUNTIME_HOOKS", compact)


def _assert_five_file_matchers(rows: list) -> None:
    file_rows = [row for row in rows if row.soul_event == "on_file_change"]
    assert len(file_rows) == 5
    assert len({row.matcher for row in file_rows}) == 5
    assert all(row.matcher for row in file_rows)


def test_unit_script_suites() -> None:
    assert event_suite() == 0
    assert seed_suite() == 0
    assert adapter_suite() == 0


def test_matcher_positive_routes_one_hook() -> None:
    _assert_matcher_routes_one()


def test_unknown_event_negative_fails_closed() -> None:
    try:
        to_soul_event("runtime_event_without_mapping")
    except ValueError as exc:
        assert "no mapeado" in str(exc)
    else:
        raise AssertionError("unknown runtime event was silently accepted")


def test_rls_control_contract_denies_runtime_writes() -> None:
    sql = (MEMORY / "soul_runtime_hooks_migration.sql").read_text(encoding="utf-8")
    _assert_rls_sql_contract(sql)


def test_delivery_effect_preserves_five_file_matchers() -> None:
    from soul_hooks_seed import derive_seed

    rows, unmapped = derive_seed()
    assert unmapped == []
    _assert_five_file_matchers(rows)


def test_mutation_matcher_widening_is_killed(monkeypatch) -> None:
    import soul_event_interface

    monkeypatch.setattr(soul_event_interface, "_matcher_matches", lambda _pattern, _values: True)
    with pytest.raises(AssertionError):
        _assert_matcher_routes_one()


def test_mutation_write_grant_is_killed() -> None:
    sql = (MEMORY / "soul_runtime_hooks_migration.sql").read_text(encoding="utf-8")
    mutant = sql.replace("GRANT SELECT ON soul_v3.runtime_hooks", "GRANT SELECT, UPDATE ON soul_v3.runtime_hooks")
    assert mutant != sql
    with pytest.raises(AssertionError):
        _assert_rls_sql_contract(mutant)


def test_mutation_matcher_erasure_is_killed() -> None:
    from soul_hooks_seed import derive_seed

    rows, _unmapped = derive_seed()
    mutant = [replace(row, matcher=None) for row in rows]
    with pytest.raises(AssertionError):
        _assert_five_file_matchers(mutant)
