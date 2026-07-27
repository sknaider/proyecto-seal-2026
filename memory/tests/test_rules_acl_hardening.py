from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UP = ROOT / "memory/migrations/20260727_rules_acl_hardening_ADA.sql"
DOWN = ROOT / "memory/migrations/20260727_rules_acl_hardening_ADA_down.sql"
PROVISIONER = ROOT / "tools/provision_stability_guard_db_role.py"


def test_up_migration_removes_public_all_policy_and_sdk_dml() -> None:
    sql = UP.read_text()
    assert "DROP POLICY mcp_legacy_compat_all ON soul_v3.rules" in sql
    assert "CREATE POLICY rules_explicit_readers" in sql
    assert "CREATE POLICY rules_soul_admin_all" in sql
    assert "REVOKE INSERT, UPDATE, DELETE" in sql
    assert "ON soul_v3.rules" in sql
    assert "FROM soul_sdk_runtime" in sql


def test_future_default_acl_keeps_select_but_loses_sdk_dml() -> None:
    sql = UP.read_text()
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE seal IN SCHEMA soul_v3" in sql
    assert "REVOKE INSERT, UPDATE, DELETE ON TABLES FROM soul_sdk_runtime" in sql
    assert "REVOKE SELECT" not in sql


def test_stability_view_is_read_only_and_sdk_acl_is_removed() -> None:
    sql = UP.read_text()
    assert "SELECT DISTINCT id, content, active" in sql
    assert "REVOKE ALL" in sql
    assert "FROM PUBLIC, soul_sdk_runtime" in sql
    assert "TO svc_soul_stability_guard" in sql


def test_rollback_is_explicit_and_restores_the_previous_contract() -> None:
    sql = DOWN.read_text()
    assert "CREATE POLICY mcp_legacy_compat_all" in sql
    assert "TO PUBLIC" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in sql
    assert "TO soul_sdk_runtime" in sql
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE seal" in sql


def test_stability_provisioner_cannot_reintroduce_sdk_view_dml() -> None:
    source = PROVISIONER.read_text()
    assert "SELECT DISTINCT id, content, active" in source
    assert "REVOKE ALL ON " in source
    assert "FROM PUBLIC, soul_sdk_runtime" in source
    assert "sdk_view_dml_denied" in source
