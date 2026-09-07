from __future__ import annotations

from pathlib import Path


MIGRATION = Path(__file__).with_name("migrations") / "20260829_g1_closure_gate_security_definer.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_g1_trigger_uses_constrained_security_definer() -> None:
    sql = _sql()
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, soul_v3" in sql
    assert "OWNER TO seal" in sql
    assert "REVOKE ALL ON FUNCTION" in sql


def test_g1_trigger_preserves_warn_block_and_grandfather_semantics() -> None:
    sql = _sql()
    assert "NEW.priority,0) >= 7" in sql
    assert "NEW.created_at < v_cutover" in sql
    assert "v_mode='BLOCK'" in sql
    assert "g1_closure_warn_log" in sql


def test_migration_is_transactional_and_does_not_grant_table_access() -> None:
    sql = _sql()
    assert sql.lstrip().startswith("-- G1")
    assert "BEGIN;" in sql and sql.rstrip().endswith("COMMIT;")
    assert "GRANT SELECT" not in sql
    assert "GRANT INSERT" not in sql
