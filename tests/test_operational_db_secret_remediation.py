from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MEMORY = ROOT / "memory"
sys.path.insert(0, str(MEMORY))
from operational_db_credentials import (  # noqa: E402
    OperationalCredentialError,
    service_pg_dsn,
)


SURFACES = {
    "messages/seal_durable_cron.py": "SEAL_DURABLE_CRON_PG_DSN",
    "messages/seal_lifecycle_controller.py": "SEAL_LIFECYCLE_PG_DSN",
    "messages/peer_health_check.sh": "SEAL_PEER_HEALTH_PG_DSN",
    "messages/continuity_snapshot.py": "SEAL_CONTINUITY_PG_DSN",
    "sandbox-agent/seal_memory_anomaly_monitor.py": "SEAL_MEMORY_MONITOR_PG_DSN",
    "messages/seal_harness_watcher.py": "SEAL_HARNESS_WATCHER_PG_DSN",
    "messages/laptop_activity_api.py": "SEAL_LAPTOP_ACTIVITY_PG_DSN",
    "messages/seal_tools_catalog.py": "SEAL_TOOLS_CATALOG_PG_DSN",
    "fable/soul_standalone/spectre_openai_shim.py": "SPECTRE_STANDALONE_PG_DSN",
}
CREDENTIAL_LITERAL = re.compile(r"postgres(?:ql)?://[^\s:'\"]+:[^\s@'\"]+@")


def test_loader_fails_closed_without_configuration(monkeypatch):
    name = "TEST_OPERATIONAL_PG_DSN"
    monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(f"{name}_FILE", raising=False)
    with pytest.raises(OperationalCredentialError, match="missing required"):
        service_pg_dsn(name, expected_role="svc_test")


def test_loader_accepts_owner_only_file_and_enforces_role(tmp_path, monkeypatch):
    name = "TEST_OPERATIONAL_PG_DSN"
    secret = tmp_path / "service.dsn"
    secret.write_text("postgresql://svc_test:placeholder@localhost:5433/db\n")
    secret.chmod(0o600)
    monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(f"{name}_FILE", str(secret))
    assert service_pg_dsn(name, expected_role="svc_test").startswith(
        "postgresql://svc_test:"
    )
    with pytest.raises(OperationalCredentialError, match="dedicated role"):
        service_pg_dsn(name, expected_role="svc_other")


def test_loader_rejects_readable_or_ambiguous_secret(tmp_path, monkeypatch):
    name = "TEST_OPERATIONAL_PG_DSN"
    secret = tmp_path / "service.dsn"
    secret.write_text("postgresql://svc_test:placeholder@localhost/db\n")
    secret.chmod(0o640)
    monkeypatch.setenv(f"{name}_FILE", str(secret))
    with pytest.raises(OperationalCredentialError, match="0600"):
        service_pg_dsn(name, expected_role="svc_test")


def test_private_transition_is_explicit_and_requires_0600(tmp_path, monkeypatch):
    name = "TEST_OPERATIONAL_PG_DSN"
    store = tmp_path / "credentials.env"
    store.write_text("SEAL_DB_DSN=postgresql://seal:placeholder@localhost/db\n")
    store.chmod(0o600)
    monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(f"{name}_FILE", raising=False)
    monkeypatch.setenv("SEAL_OPERATIONAL_TRANSITION_FILE", str(store))
    assert service_pg_dsn(
        name, expected_role="svc_test", allow_private_transition=True
    ).startswith("postgresql://seal:")
    monkeypatch.setenv("SEAL_REQUIRE_DEDICATED_OPERATIONAL_DSN", "1")
    with pytest.raises(OperationalCredentialError, match="missing required"):
        service_pg_dsn(name, expected_role="svc_test", allow_private_transition=True)
    monkeypatch.delenv("SEAL_REQUIRE_DEDICATED_OPERATIONAL_DSN")
    store.chmod(0o644)
    with pytest.raises(OperationalCredentialError, match="0600"):
        service_pg_dsn(name, expected_role="svc_test", allow_private_transition=True)
    monkeypatch.setenv(name, "postgresql://svc_test:placeholder@localhost/db")
    monkeypatch.setenv(f"{name}_FILE", str(store))
    with pytest.raises(OperationalCredentialError, match="only one"):
        service_pg_dsn(name, expected_role="svc_test")


def test_operational_surfaces_have_no_embedded_credential_and_use_own_env():
    assert len(SURFACES) == 9
    for relative, env_name in SURFACES.items():
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert not CREDENTIAL_LITERAL.search(source), relative
        assert env_name in source, relative
        assert "service_pg_dsn" in source, relative
        assert source.count("allow_private_transition=True") == 1, relative


def test_compose_uses_external_password_file_only():
    source = (ROOT / "docker-compose.seal-memory.yml").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD_FILE: /run/secrets/seal_memory_postgres_password" in source
    assert not re.search(r"^\s*POSTGRES_PASSWORD\s*:", source, re.MULTILINE)
    assert re.search(
        r"seal_memory_postgres_password:\s*\n\s+external:\s+true", source
    )
    assert not CREDENTIAL_LITERAL.search(source)


def test_provisioner_plan_has_nine_unique_non_superuser_roles(capsys):
    path = ROOT / "scripts/provision_operational_db_roles.py"
    spec = importlib.util.spec_from_file_location("operational_roles", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    roles = [item.role for item in module.ALL_ROLES]
    assert len(roles) == len(set(roles)) == 9
    assert all(role.startswith("svc_") for role in roles)
    assert "seal" not in roles
    module._print_plan()
    output = capsys.readouterr().out
    assert "roles=9" in output
    assert "secrets_emitted=false" in output
    assert not CREDENTIAL_LITERAL.search(output)


def test_all_systemd_dropins_reference_private_dsn_files():
    directory = ROOT / "ops/postgres-cutover/systemd"
    dropins = sorted(directory.glob("*.conf"))
    assert len(dropins) == 9
    for path in dropins:
        source = path.read_text(encoding="utf-8")
        assert "SEAL_REQUIRE_DEDICATED_OPERATIONAL_DSN=1" in source
        assert "_PG_DSN_FILE=%h/.config/seal/postgres-services/" in source
        assert not CREDENTIAL_LITERAL.search(source)


def test_provisioner_default_is_plan_only():
    completed = subprocess.run(
        [sys.executable, "scripts/provision_operational_db_roles.py"],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    assert "plan_only=true roles=9" in completed.stdout
    assert "applied=true" not in completed.stdout
    assert not CREDENTIAL_LITERAL.search(completed.stdout + completed.stderr)
