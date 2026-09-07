from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260724_high_importance_delete_shadow_ADA.sql"
)


def _sql() -> str:
    return MIGRATION.read_text()


def test_guard_is_shadow_only_without_enforcement_switch() -> None:
    sql = _sql()
    assert "SHADOW ONLY" in sql
    assert "BEFORE DELETE ON soul_v3.memories" in sql
    assert "RETURN NULL" not in sql
    assert "RAISE EXCEPTION" not in sql
    assert "memory_guard_config" not in sql


def test_live_twin_is_exact_and_owner_scoped() -> None:
    sql = _sql()
    assert "twin.tenant_id = OLD.tenant_id" in sql
    assert "twin.agent = OLD.agent" in sql
    assert "twin.invalid_at IS NULL" in sql
    assert "twin.content_hash_sha256 = OLD.content_hash_sha256" in sql
    assert "twin.content = OLD.content" in sql


def test_archive_escape_is_exact_row_and_owner_scoped() -> None:
    sql = _sql()
    assert "archived.id = OLD.id" in sql
    assert "archived.tenant_id = OLD.tenant_id" in sql
    assert "archived.agent = OLD.agent" in sql


def test_audit_uses_row_tenant_and_never_copies_plaintext() -> None:
    sql = _sql()
    assert "OLD.tenant_id" in sql
    assert "content_hash_sha256" in sql
    assert "content_head" not in sql
    assert "left(COALESCE(OLD.content" not in sql


def test_shadow_telemetry_failure_cannot_block_delete() -> None:
    sql = _sql()
    assert "EXCEPTION" in sql
    assert "WHEN OTHERS" in sql
    assert "Shadow telemetry must never become accidental enforcement" in sql
    assert sql.count("RETURN OLD;") >= 3


def test_audit_surface_is_not_public() -> None:
    sql = _sql()
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON TABLE soul_v3.memory_delete_shadow_log FROM PUBLIC" in sql
    assert "GRANT SELECT ON TABLE soul_v3.memory_delete_shadow_log TO soul_admin" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, soul_v3" in sql
