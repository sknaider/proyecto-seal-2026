from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "memory/migrations/20260722_ada_bridge_hard_identity_ADA.sql"
DOWN = ROOT / "memory/migrations/20260722_ada_bridge_hard_identity_ADA.rollback.sql"


def test_bridge_identity_migration_is_session_user_derived_and_restrictive():
    sql = UP.read_text(encoding="utf-8")
    assert "CASE session_user" in sql
    assert "current_setting('app.agent'" not in sql
    assert sql.count("AS RESTRICTIVE") == 3
    assert "TO pr_ada_bridge" in sql
    for table in ("working_state", "emotional_diary", "identity"):
        assert f"ON soul_v3.{table}" in sql


def test_bridge_identity_migration_has_complete_rollback():
    sql = DOWN.read_text(encoding="utf-8")
    for table in ("working_state", "emotional_diary", "identity"):
        assert (
            f"DROP POLICY IF EXISTS ada_bridge_hard_session_identity ON soul_v3.{table};"
            in sql
        )
    assert "DROP FUNCTION IF EXISTS soul_v3.ada_bridge_session_agent();" in sql


def test_runtime_sources_have_no_superuser_dsn_literal():
    for name in ("ada_codex_poller.py", "ada_codex_remote_bridge.py"):
        source = (ROOT / "messages" / name).read_text(encoding="utf-8")
        assert "postgresql://seal:" not in source
        assert 'expected_role="login_ada_bridge"' in source


def test_terminal_ack_requires_publication_receipt():
    poller = (ROOT / "messages/ada_codex_poller.py").read_text(encoding="utf-8")
    relay = (ROOT / "messages/ada_codex_stream_relay.py").read_text(encoding="utf-8")
    assert "wait_for_terminal_completion(task_id)" in poller
    assert "_mark_task_delivered(" in relay
    assert '"status": "delivered"' in relay
    assert '"db_id": db_ids[0]' in relay
    assert "os.fsync" in relay
