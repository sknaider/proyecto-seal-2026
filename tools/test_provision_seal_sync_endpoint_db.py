from tools import provision_seal_sync_endpoint_db as provision
import pytest


def test_agent_domain_matches_roles_exactly() -> None:
    assert tuple(provision.ROLES) == provision.AGENTS
    assert provision._agent_domain_sql() == (
        "'ADA', 'ALICE', 'DUM', 'FABLE', 'JARVIS', 'NEXUS'"
    )


def test_each_session_identity_has_one_domain_owner() -> None:
    identity_case = provision._identity_case()
    for agent, role in provision.ROLES.items():
        assert identity_case.count(f"WHEN '{role}' THEN '{agent}'") == 1


def test_legacy_provisioner_fails_before_rotation_after_os_cutover(
    monkeypatch, tmp_path
) -> None:
    marker = tmp_path / "db.env"
    marker.write_text("dedicated\n", encoding="utf-8")
    monkeypatch.setattr(provision, "DEDICATED_ENV_FILE", marker)

    with pytest.raises(RuntimeError, match="provisioner legacy bloqueado"):
        provision._assert_legacy_runtime_target()
