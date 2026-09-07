from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools/provision_nerves_runtime_role.py"
SPEC = importlib.util.spec_from_file_location("provision_nerves_runtime_role", PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_each_agent_has_a_unique_login_and_unit():
    assert set(module.AGENT_ROLES) == {"ADA", "ALICE", "DUM", "JARVIS", "NEXUS"}
    assert len(set(module.AGENT_ROLES.values())) == 5
    assert len(set(module.AGENT_UNITS.values())) == 5
    assert all(role.startswith("svc_soul_nerves_") for role in module.AGENT_ROLES.values())


def test_trusted_identity_uses_session_user_not_client_guc():
    sql = module._ident_sql()
    assert "session_user" in sql
    assert "app.agent" not in sql
    for agent, role in module.AGENT_ROLES.items():
        assert f"WHEN '{role}' THEN '{agent}'" in sql


def test_shared_login_requires_explicit_retirement_flag():
    source = PATH.read_text(encoding="utf-8")
    assert "--retire-shared" in source
    assert f"ALTER ROLE {{SHARED_ROLE}} NOLOGIN" in source


def test_dropins_use_agent_specific_credentials():
    source = PATH.read_text(encoding="utf-8")
    assert "soul_nerves_{agent.lower()}_db.env" in source
    assert '"[Service]\\nEnvironmentFile=\\n"' in source
    assert "EnvironmentFile=%h/.config/seal/{env_path.name}" in source


def test_hard_policy_is_restrictive_and_session_bound():
    source = PATH.read_text(encoding="utf-8")
    assert "AS RESTRICTIVE FOR ALL" in source
    assert "agent::text = soul_v3.nerves_session_agent()" in source
    assert "NOBYPASSRLS NOINHERIT" in source
