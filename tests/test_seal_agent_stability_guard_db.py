from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import seal_agent_stability_guard as guard


def test_read_private_dsn_accepts_expected_principal(tmp_path) -> None:
    secret = tmp_path / "guard.dsn"
    secret.write_text(
        "postgresql://svc_soul_stability_guard:opaque@localhost/db\n",
        encoding="utf-8",
    )
    secret.chmod(0o600)

    assert "svc_soul_stability_guard" in guard.read_private_dsn(
        secret, "svc_soul_stability_guard"
    )


def test_read_private_dsn_reads_single_env_entry(tmp_path) -> None:
    secret = tmp_path / "sdk.env"
    secret.write_text(
        "SEAL_DB_URL='postgresql://svc_soul_memory_sdk:opaque@localhost/db'\n",
        encoding="utf-8",
    )
    secret.chmod(0o600)

    assert guard.read_private_dsn(
        secret,
        "svc_soul_memory_sdk",
        env_key="SEAL_DB_URL",
    ).startswith("postgresql://svc_soul_memory_sdk:")


@pytest.mark.parametrize("mode", [0o644, 0o600])
def test_read_private_dsn_fails_closed_on_mode_or_principal(
    tmp_path, mode: int
) -> None:
    secret = tmp_path / "guard.dsn"
    secret.write_text(
        "postgresql://seal:opaque@localhost/db\n",
        encoding="utf-8",
    )
    secret.chmod(mode)

    with pytest.raises(RuntimeError):
        guard.read_private_dsn(secret, "svc_soul_stability_guard")


def test_stable_hash_tracks_sdk_boundary_without_breaking_legacy_reports() -> None:
    report = {
        "status": "GREEN",
        "issues": [],
        "fixes": [],
        "ws": [],
        "auth_guard": {"status": "healthy", "issues": []},
        "autonomy_contract": {"status": "healthy", "issues": []},
        "mcp_postgres_boundary": {"status": "healthy", "issues": []},
    }
    legacy = guard.stable_hash(report)
    report["sdk_db_boundary"] = {"status": "drift", "issues": ["escape"]}
    assert guard.stable_hash(report) != legacy
