from pathlib import Path


MIGRATION = Path(__file__).with_name("migrations") / "20260828_soul_feedback_signal_runtime_grants_v1.sql"


def test_feedback_runtime_grants_are_owner_scoped_and_minimal():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "GRANT INSERT (agent, signal, action_ref, context, instinct_id, ema_delta)" in sql
    assert "GRANT SELECT (id)" in sql
    assert "GRANT UPDATE" not in sql
    assert "GRANT DELETE" not in sql
    assert "current_setting('app.agent', true)" in sql
    assert "mcp_feedback_insert_own_agent" in sql
    assert "mcp_feedback_select_own_agent" in sql
