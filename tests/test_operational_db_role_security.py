from __future__ import annotations

import importlib.util
import asyncio
import os
import stat
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _role(module, service: str):
    return next(spec for spec in module.ALL_ROLES if spec.service == service)


def test_durable_cron_resolves_secret_inside_child_not_argv(monkeypatch):
    module = _load("messages/seal_durable_cron.py", "durable_cron_security_test")
    captured = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setenv(
        "SEAL_DURABLE_CRON_PG_DSN",
        "postgresql://svc_seal_durable_cron:SENTINEL_SECRET@localhost:5433/seal_memory",
    )
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module._run_db("register", {
        "agent": "ADA", "name": "private-loop", "prompt": "SENSITIVE_PROMPT",
        "interval": 60,
    })

    argv = "\n".join(captured["args"])
    assert "SENTINEL_SECRET" not in argv
    assert "SENSITIVE_PROMPT" not in argv
    assert "_db-child" in argv
    assert "SENSITIVE_PROMPT" in captured["kwargs"]["input"]
    assert captured["kwargs"]["text"] is True
    assert "_db_url()!r" not in (ROOT / "messages/seal_durable_cron.py").read_text()


def test_manifest_requires_real_session_loop_relation_and_scoped_rls():
    module = _load("scripts/provision_operational_db_roles.py", "ops_roles_session_test")
    durable = _role(module, "seal-durable-cron")
    assert durable.setup == "session_loops"
    assert "session_loops" not in durable.optional_relations
    assert durable.grants == ()
    grants = {(grant.privilege, grant.columns) for grant in durable.column_grants}
    assert grants == {
        ("SELECT", ("agent", "loop_name", "loop_prompt", "interval_seconds", "active", "last_fired")),
        ("INSERT", ("agent", "loop_name", "loop_prompt", "interval_seconds", "active")),
        ("UPDATE", ("loop_prompt", "interval_seconds", "active", "last_fired")),
    }
    assert {policy.command for policy in durable.policies} == {"SELECT", "INSERT", "UPDATE"}

    source = (ROOT / "scripts/provision_operational_db_roles.py").read_text()
    assert "CREATE TABLE IF NOT EXISTS soul_v3.session_loops" in source
    assert "UNIQUE (agent, loop_name)" in source
    assert "FORCE ROW LEVEL SECURITY" in source


def test_public_permissive_tables_have_role_restrictive_guards():
    module = _load("scripts/provision_operational_db_roles.py", "ops_roles_rls_test")
    peer = _role(module, "seal-peer-health")
    continuity = _role(module, "seal-continuity")

    peer_guard = [p for p in peer.policies if p.restrictive]
    assert len(peer_guard) == 1
    assert peer_guard[0].table == "event_log"
    assert peer_guard[0].expression == "event_type = 'heartbeat'"

    continuity_guards = {(p.table, p.command) for p in continuity.policies if p.restrictive}
    assert continuity_guards == {("inner_monologue", "SELECT"), ("working_state", "SELECT")}
    assert all("NEXUS" not in p.expression for p in continuity.policies if p.restrictive)
    assert len(continuity.boundary_checks) == 2
    assert any(("app.agent", "NEXUS") in check.settings for check in continuity.boundary_checks)
    names = [
        module._policy_name(spec, policy)
        for spec in module.ALL_ROLES
        for policy in spec.policies
    ]
    assert len(names) == len(set(names))
    assert all(len(name) <= 63 for name in names)


def test_restrictive_policy_sql_is_role_scoped():
    module = _load("scripts/provision_operational_db_roles.py", "ops_roles_policy_sql_test")
    peer = _role(module, "seal-peer-health")
    policy = next(item for item in peer.policies if item.restrictive)

    class FakeConnection:
        def __init__(self):
            self.executed = []

        async def fetchval(self, query, *args):
            if "quote_ident" in query:
                return args[0]
            return True

        async def fetchrow(self, query, *args):
            return None

        async def execute(self, query, *args):
            self.executed.append(query)

    connection = FakeConnection()
    asyncio.run(module._ensure_policy(connection, peer, policy))
    statement = connection.executed[-1]
    assert "AS RESTRICTIVE" in statement
    assert "TO svc_seal_peer_health" in statement
    assert "event_type = 'heartbeat'" in statement


def test_policy_command_mismatch_is_preflighted_and_recreated():
    module = _load("scripts/provision_operational_db_roles.py", "ops_policy_recreate_test")
    peer = _role(module, "seal-peer-health")
    policy = next(item for item in peer.policies if item.restrictive)
    name = module._policy_name(peer, policy)

    class FakeConnection:
        def __init__(self):
            self.executed = []

        async def fetchval(self, query, *args):
            if "quote_ident" in query:
                return args[0]
            return True

        async def fetchrow(self, query, *args):
            if "SELECT permissive, roles, cmd" in query:
                return {"permissive": "RESTRICTIVE", "roles": [peer.role], "cmd": "UPDATE"}
            return None

        async def execute(self, query, *args):
            self.executed.append(query)

    connection = FakeConnection()
    asyncio.run(module._ensure_policy(connection, peer, policy, {name}))
    assert any(statement.startswith("DROP POLICY") for statement in connection.executed)
    assert any("CREATE POLICY" in statement and "FOR SELECT" in statement
               for statement in connection.executed)


def test_policy_preflight_detects_immutable_shape_before_mutation():
    module = _load("scripts/provision_operational_db_roles.py", "ops_policy_preflight_test")
    peer = _role(module, "seal-peer-health")
    target = next(item for item in peer.policies if item.restrictive)

    class FakeConnection:
        async def fetchrow(self, _query, table, name):
            if table == target.table and name == module._policy_name(peer, target):
                return {"permissive": "PERMISSIVE", "cmd": "UPDATE"}
            return None

    recreate = asyncio.run(module._preflight_policy_recreates(FakeConnection(), (peer,)))
    assert recreate == {module._policy_name(peer, target)}


def test_tools_catalog_uses_only_required_column_grants():
    module = _load("scripts/provision_operational_db_roles.py", "ops_roles_tools_test")
    tools = _role(module, "seal-tools-catalog")
    assert tools.grants == ()
    grants = {(g.privilege, g.columns) for g in tools.column_grants}
    assert (
        "SELECT",
        ("id", "tool_key", "port", "status", "metadata", "last_seen_at"),
    ) in grants
    assert (
        "UPDATE",
        ("status", "metadata", "last_seen_at", "last_checked_at"),
    ) in grants


def test_spectre_has_no_direct_base_table_or_sequence_grants():
    module = _load("scripts/provision_operational_db_roles.py", "ops_roles_spectre_test")
    spectre = _role(module, "spectre-openai-shim")
    assert {table for table, _ in spectre.grants} == {
        "spectre_identity_boundary",
        "spectre_memories_boundary",
    }
    assert spectre.function_grants == ("ensure_spectre_prereqs()",)
    assert spectre.setup == "spectre_boundary"

    shim = (ROOT / "fable/soul_standalone/spectre_openai_shim.py").read_text()
    runner = (ROOT / "fable/soul_standalone/soul_runner.py").read_text()
    assert 'os.environ["SOUL_SPECTRE_RESTRICTED_DB"] = "1"' in shim
    assert "spectre_identity_boundary" in runner
    assert "spectre_memories_boundary" in runner
    assert "postgresql://seal:" not in runner

    shim_source = (ROOT / "fable/soul_standalone/spectre_openai_shim.py").read_text()
    resolved = shim_source.index("DSN = service_pg_dsn(")
    scrubbed = shim_source.index("os.environ.pop(_db_env_name, None)")
    tools_import = shim_source.index("from tool_executor import run_tool_loop")
    assert resolved < scrubbed < tools_import
    assert 'os.environ["SOUL_STANDALONE_DSN"]' not in shim_source


def test_memory_monitor_uses_minimal_boundary_views():
    module = _load("scripts/provision_operational_db_roles.py", "ops_monitor_views_test")
    monitor = _role(module, "seal-memory-monitor")
    assert monitor.setup == "memory_monitor_boundary"
    assert {table for table, _ in monitor.grants} == {
        "memory_monitor_memories_boundary",
        "memory_monitor_audit_boundary",
        "memory_monitor_working_state_boundary",
        "memory_monitor_capabilities_boundary",
    }
    source = (ROOT / "sandbox-agent/seal_memory_anomaly_monitor.py").read_text()
    assert "FROM soul_v3.memory_monitor_memories_boundary" in source
    assert "FROM soul_v3.memory_monitor_audit_boundary" in source
    assert "FROM soul_v3.memory_monitor_working_state_boundary" in source
    assert "FROM soul_v3.memory_monitor_capabilities_boundary" in source


def test_only_sensitive_public_functions_are_revoked_and_canaried():
    module = _load("scripts/provision_operational_db_roles.py", "ops_roles_functions_test")
    assert module.SENSITIVE_PUBLIC_FUNCTIONS == (
        "memory_dual_usage_stats()",
        "memory_structure_stats()",
    )
    assert module.FUNCTION_CANARY_ROLES == ("fable_ltd", "soul_admin")
    source = (ROOT / "scripts/provision_operational_db_roles.py").read_text()
    assert "ALL FUNCTIONS IN SCHEMA soul_v3 FROM PUBLIC" not in source
    assert "aclexplode" in source
    assert "function canary lost EXECUTE" in source
    assert module.REQUIRED_FUNCTION_ROLES == {"soul_standalone": ("soul_admin",)}


def test_standalone_preserves_soul_admin_before_public_revoke_and_denies_others():
    module = _load("scripts/provision_operational_db_roles.py", "ops_function_contract_test")

    class FakeConnection:
        def __init__(self):
            self.public = True
            self.explicit = set()
            self.executed = []

        async def fetchval(self, query, *args):
            if "quote_ident" in query:
                return args[0]
            if "to_regprocedure" in query and "IS NOT NULL" in query:
                return True
            if "SELECT EXISTS (SELECT 1 FROM pg_roles" in query:
                return args[0] in {"soul_admin", "fable_ltd"}
            if "has_function_privilege" in query:
                role = args[0]
                return role == "soul_admin" or role in self.explicit
            if "acldefault" in query:
                return self.public
            if "aclexplode" in query:
                return args[1] in self.explicit
            if query.startswith("SELECT count(*)"):
                return 1
            return False

        async def execute(self, query, *args):
            self.executed.append(query)
            if query.startswith("GRANT EXECUTE"):
                self.explicit.add(query.rsplit(" TO ", 1)[1])
            elif query.startswith("REVOKE EXECUTE"):
                self.public = False

    connection = FakeConnection()
    asyncio.run(module._harden_sensitive_functions(connection, "soul_standalone"))
    for signature in module.SENSITIVE_PUBLIC_FUNCTIONS:
        grant = next(i for i, sql in enumerate(connection.executed)
                     if sql == f"GRANT EXECUTE ON FUNCTION soul_v3.{signature} TO soul_admin")
        revoke = next(i for i, sql in enumerate(connection.executed)
                      if sql == f"REVOKE EXECUTE ON FUNCTION soul_v3.{signature} FROM PUBLIC")
        assert grant < revoke
    assert connection.public is False
    assert "soul_admin" in connection.explicit


def test_sensitive_function_verifier_rejects_public_or_operational_access():
    module = _load("scripts/provision_operational_db_roles.py", "ops_function_negative_test")

    class FakeConnection:
        async def fetchval(self, query, *args):
            if "to_regprocedure" in query:
                return True
            if "acldefault" in query:
                return True
            return False

    try:
        asyncio.run(module._verify_sensitive_function_contract(
            FakeConnection(), "soul_standalone"
        ))
    except RuntimeError as exc:
        assert "PUBLIC still executes" in str(exc)
    else:
        raise AssertionError("PUBLIC function access was accepted")

    target_role = _role(module, "spectre-openai-shim").role

    class OtherRoleConnection:
        async def fetchval(self, query, *args):
            if "acldefault" in query:
                return False
            if "to_regprocedure" in query:
                return True
            if "SELECT EXISTS (SELECT 1 FROM pg_roles" in query:
                return args[0] == target_role
            if "has_function_privilege" in query:
                return args[0] in {"soul_admin", target_role}
            return False

    try:
        asyncio.run(module._verify_sensitive_function_contract(
            OtherRoleConnection(), "soul_standalone"
        ))
    except RuntimeError as exc:
        assert f"operational role executes sensitive function: {target_role}" in str(exc)
    else:
        raise AssertionError("operational function access was accepted")


def test_verify_one_handles_view_contracts_without_policy_variable():
    module = _load("scripts/provision_operational_db_roles.py", "ops_view_only_verify_test")
    import asyncpg

    spec = module.ServiceRole(
        service="view-only-test",
        database="seal_memory",
        role="svc_view_only_test",
        env_name="VIEW_ONLY_DSN",
        view_contracts=(module.ViewContract(
            "demo_boundary", ("id",), ("from soul_v3.source_table",),
        ),),
        policies=(),
    )

    class Tx:
        async def start(self):
            return None

        async def rollback(self):
            return None

    class FakeConnection:
        async def fetchrow(self, query, *args):
            if "SELECT current_user" in query:
                return {
                    "current_user": spec.role, "session_user": spec.role,
                    "current_database": spec.database,
                    "application_name": spec.service.replace("-", "_"),
                }
            if "SELECT rolsuper" in query:
                return {
                    "rolsuper": False, "rolcreatedb": False, "rolcreaterole": False,
                    "rolreplication": False, "rolbypassrls": False, "rolinherit": False,
                }
            if "pg_get_viewdef" in query:
                return {
                    "definition": "SELECT id FROM soul_v3.source_table;",
                    "options": ["security_barrier=true"], "check_option": "NONE",
                }
            raise AssertionError(f"unexpected fetchrow: {query}")

        async def fetch(self, query, *args):
            if "pg_auth_members" in query:
                return []
            if "has_table_privilege" in query:
                return []
            if "has_column_privilege" in query:
                return []
            if "has_sequence_privilege" in query:
                return []
            if "p.prosecdef" in query:
                return []
            if "information_schema.columns" in query:
                return [{"column_name": "id"}]
            raise AssertionError(f"unexpected fetch: {query}")

        async def fetchval(self, query, *args):
            if "pg_has_role" in query:
                return False
            if "to_regprocedure" in query:
                return False
            if "to_regclass" in query:
                return True
            raise AssertionError(f"unexpected fetchval: {query}")

        def transaction(self):
            return Tx()

        async def close(self):
            return None

    connection = FakeConnection()
    calls = 0

    async def connector(_dsn, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return connection
        raise asyncpg.InsufficientPrivilegeError("cross-db denied")

    asyncio.run(module._verify_one(spec, "postgresql://svc:x@localhost/seal_memory",
                                   connector=connector))
    assert calls == 2


def test_verifier_checks_exact_acl_membership_public_policy_and_effective_rls():
    source = (ROOT / "scripts/provision_operational_db_roles.py").read_text()
    required_assertions = (
        "pg_auth_members",
        "pg_has_role(current_user, 'seal', 'MEMBER')",
        "has_table_privilege",
        "has_column_privilege",
        "has_sequence_privilege",
        "PUBLIC permissive policy lacks role-scoped restrictive guard",
        "effective RLS boundary failed",
        "SECURITY DEFINER ACL mismatch",
        "qual, with_check",
        "pg_get_viewdef",
        "security_barrier=true",
        "cross-database CONNECT unexpectedly succeeded",
        "_run_synthetic_canaries",
    )
    for assertion in required_assertions:
        assert assertion in source


def test_secret_io_is_owner_only_nofollow_and_rotates_invalid(tmp_path):
    module = _load("scripts/provision_operational_db_roles.py", "ops_secret_io_test")
    secret = tmp_path / "private" / "service.dsn"
    module._write_private(secret, "postgresql://role:new@localhost/db")
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    assert module._read_private(secret).endswith("/db")

    os.chmod(secret, 0o644)
    assert module._existing_password(secret, "role") is None
    module._write_private(secret, "postgresql://role:rotated@localhost/db")
    assert module._existing_password(secret, "role") == "rotated"

    target = tmp_path / "target"
    target.mkdir()
    symlink_parent = tmp_path / "linked"
    symlink_parent.symlink_to(target, target_is_directory=True)
    try:
        module._write_private(symlink_parent / "bad.dsn", "secret")
    except RuntimeError:
        pass
    else:
        raise AssertionError("symlink secret parent was accepted")


def test_activation_failure_rolls_role_back_to_nologin(monkeypatch):
    module = _load("scripts/provision_operational_db_roles.py", "ops_activation_rollback_test")
    spec = _role(module, "seal-peer-health")
    transitions = []
    invariant_checks = []
    events = []

    async def fake_set(_dsn, role, enabled):
        transitions.append((role, enabled))
        events.append(f"login={enabled}")

    async def fake_verify(_spec, _dsn):
        raise RuntimeError("synthetic verifier failure")

    async def fake_admin_invariants(_dsn, database):
        invariant_checks.append(database)
        events.append("admin_invariants")

    monkeypatch.setattr(module, "_set_role_login", fake_set)
    monkeypatch.setattr(module, "_verify_one", fake_verify)
    monkeypatch.setattr(module, "_verify_admin_invariants", fake_admin_invariants)
    active, failed = asyncio.run(module._activate_group("admin", [(spec, "service")]))
    assert active == []
    assert failed == [spec.service]
    assert transitions == [(spec.role, True), (spec.role, False)]
    assert invariant_checks == [spec.database]
    assert events == ["login=True", "login=False", "admin_invariants"]


def test_apply_orders_nologin_secret_login_verify_and_reports_per_database():
    source = (ROOT / "scripts/provision_operational_db_roles.py").read_text()
    assert "ROLE {role} NOLOGIN PASSWORD" in source
    assert "_write_private(_secret_path(spec), dsn)" in source
    assert "await _set_role_login(admin_dsn, spec.role, True)" in source
    assert "await _verify_one(spec, dsn)" in source
    assert "await _set_role_login(admin_dsn, spec.role, False)" in source
    assert "database={database} status=" in source
