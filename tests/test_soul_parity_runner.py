"""Static security contracts for the F3 sandbox runner and migration."""
from pathlib import Path
import os
import json
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "memory"))

import soul_parity_runner as runner  # noqa: E402
from soul_runtime_orchestrator import RuntimeHookStore, RuntimeIdentityError  # noqa: E402


def test_sandbox_dsn_changes_only_database() -> None:
    raw = "postgresql://mcp_runtime_ada:secret@localhost:5433/seal_memory"
    original = runner.read_private_dsn
    original_path = runner.default_dsn_path
    try:
        runner.read_private_dsn = lambda _path: raw
        runner.default_dsn_path = lambda _agent: Path("unused")
        value = runner.sandbox_dsn("ADA")
    finally:
        runner.read_private_dsn = original
        runner.default_dsn_path = original_path
    assert value == "postgresql://mcp_runtime_ada:secret@localhost:5433/soul_v3_sandbox"


def test_runtime_store_rejects_ambiguous_or_non_postgres_injected_dsn() -> None:
    with pytest.raises(RuntimeIdentityError, match="not both"):
        RuntimeHookStore("ADA", Path("unused"), "postgresql://restricted/db")
    with pytest.raises(RuntimeIdentityError, match="not PostgreSQL"):
        RuntimeHookStore("ADA", restricted_dsn="file:///tmp/not-a-dsn")


def test_claude_environment_replaces_all_database_aliases(monkeypatch) -> None:
    monkeypatch.setenv("SEAL_DB_DSN", "postgresql://seal:admin@localhost/db")
    monkeypatch.setenv("PGPASSWORD", "admin")
    fire = runner.ClaudeFire("ADA", "postgresql://mcp_runtime_ada:x@localhost/soul_v3_sandbox")
    from soul_parity_test import EvidenceKey
    key = EvidenceKey("run", "claude_code", "on_boot", str(runner.HOOK), "ADA", "a" * 32, "b" * 64)
    env = fire._environment(key)
    assert env["SEAL_DB_DSN"] == env["SEAL_DB_URL"] == env["SEAL_PG_DSN"]
    assert "PGPASSWORD" not in env
    assert "SEAL_AGENT" not in env
    assert env["SOUL_PARITY_AGENT"] == "ADA"
    assert env["SOUL_PARITY_NONCE"] == "a" * 32


def test_effect_hook_uses_probe_identity_with_database_role_as_authority() -> None:
    source = (ROOT / "memory" / "soul_parity_effect_hook.py").read_text()
    assert 'os.environ.get("SOUL_PARITY_AGENT")' in source
    assert 'expected_role = f"mcp_runtime_{agent.lower()}"' in source
    assert 'identity["current_user"] != expected_role' in source


def test_migration_forces_rls_and_withholds_mutation_grants() -> None:
    sql = (ROOT / "memory" / "soul_parity_sandbox_migration.sql").read_text()
    assert "ALTER TABLE soul_f3.parity_evidence_v2 FORCE ROW LEVEL SECURITY" in sql
    assert "GRANT SELECT, INSERT ON soul_f3.parity_evidence_v2" in sql
    assert "GRANT UPDATE" not in sql
    assert "GRANT DELETE" not in sql
    assert "GRANT TRUNCATE" not in sql
    assert "recorded_by = session_user" in sql


def test_claude_and_local_entrypoints_are_distinct() -> None:
    source = (ROOT / "memory" / "soul_parity_runner.py").read_text()
    assert "claude-code:" in source
    assert "soul_parity_local_process" in source
    assert "CLAUDE_EXEC" in source
    assert "process_pid" in (ROOT / "memory" / "soul_parity_test.py").read_text()


def test_compact_probe_has_explicit_prompt_and_oversized_stdin() -> None:
    source = (ROOT / "memory" / "soul_parity_runner.py").read_text()
    compact = source.split("if compact_probe:", 1)[1].split("else:", 1)[0]
    assert "argv.append(prompt)" in compact
    assert 'stdin = ("cobalt " * 115000)' in compact
    assert "stdin=subprocess.PIPE if stdin is not None else None" in source


def test_live_registry_canonicalizes_relative_script_paths() -> None:
    source = (ROOT / "memory" / "soul_parity_runner.py").read_text()
    assert "replace(hook, script_path=str(canonical.resolve()))" in source


def test_claude_environment_does_not_inherit_unlisted_secrets(monkeypatch) -> None:
    for name in ("AWS_SECRET_ACCESS_KEY", "DATABASE_URL", "GITHUB_TOKEN", "PGPASSFILE"):
        monkeypatch.setenv(name, "must-not-pass")
    fire = runner.ClaudeFire("ADA", "postgresql://mcp_runtime_ada:x@localhost/soul_v3_sandbox")
    from soul_parity_test import EvidenceKey
    key = EvidenceKey("run", "claude_code", "on_boot", str(runner.HOOK), "ADA", "a" * 32, "b" * 64)
    env = fire._environment(key)
    assert not ({"AWS_SECRET_ACCESS_KEY", "DATABASE_URL", "GITHUB_TOKEN", "PGPASSFILE"} & env.keys())


def test_effect_schema_binds_native_event_pid_and_executed_bytes() -> None:
    hook = (ROOT / "memory" / "soul_parity_effect_hook.py").read_text()
    sql = (ROOT / "memory" / "soul_parity_sandbox_migration.sql").read_text()
    runner_source = (ROOT / "memory" / "soul_parity_runner.py").read_text()
    assert "native Claude event does not match claimed SOUL event" in hook
    assert "SOUL_EXECUTED_SCRIPT_SHA256" in hook
    assert "SOUL_PARITY_RUNTIME_PID" in hook
    assert "native_event text NOT NULL" in sql
    assert "script_sha256 text NOT NULL" in sql
    assert "runtime_pid integer NOT NULL" in sql
    assert "key.native_event, key.script_sha256, key.runtime_pid" in runner_source
    assert "start_new_session=True" in runner_source
    assert "os.killpg(proc.pid, signal.SIGKILL)" in runner_source
    assert "await proc.communicate()" in runner_source


def test_claude_native_event_cannot_claim_precompact() -> None:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "SOUL_PARITY_AGENT": "ADA",
        "SOUL_PARITY_RUNTIME_ID": "claude_code",
        "SOUL_PARITY_SOUL_EVENT": "on_compact",
        "SOUL_PARITY_SUITE_RUN_ID": "test-run",
        "SOUL_PARITY_NONCE": "a" * 32,
        "SOUL_PARITY_TOKEN": "b" * 64,
        "SOUL_EXECUTED_SCRIPT_SHA256": "c" * 64,
        "SOUL_PARITY_RUNTIME_PID": str(os.getpid()),
        "SEAL_PG_DSN": "postgresql://unused/unused",
    }
    completed = subprocess.run(
        [str(runner.PYTHON), str(runner.HOOK)],
        input=json.dumps({"hook_event_name": "SessionStart"}),
        text=True, capture_output=True, env=env, check=False,
    )
    assert completed.returncode == 1
    assert "native Claude event does not match claimed SOUL event" in completed.stderr
