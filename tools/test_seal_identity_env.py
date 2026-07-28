from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "seal_identity_env.sh"
SYNTHETIC_TOKEN = "synthetic-test-token-not-a-live-secret"


def _source(tmp_path: Path, *, legacy_flag: str | None = None) -> subprocess.CompletedProcess[str]:
    config = tmp_path / "config"
    runtime = tmp_path / "runtime"
    legacy = tmp_path / "legacy"
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(config),
        "XDG_RUNTIME_DIR": str(runtime),
        "SEAL_TOKENS_DIR": str(runtime / "seal"),
        "SEAL_LEGACY_TOKENS_DIR": str(legacy),
        "SEAL_AGENT": "NEXUS",
    }
    env.pop("SEAL_DISABLE_LEGACY_TMP_TOKENS", None)
    if legacy_flag is not None:
        env["SEAL_DISABLE_LEGACY_TMP_TOKENS"] = legacy_flag
    return subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail; source "$1"; '
            'test -n "$SEAL_SESSION_TOKEN"; '
            'printf "%s\\n" "$SEAL_TOKENS_DIR"',
            "bash",
            str(BOOTSTRAP),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_default_writes_runtime_only(tmp_path: Path) -> None:
    result = _source(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "runtime/seal/NEXUS.token").is_file()
    assert not (tmp_path / "legacy").exists()


def test_default_does_not_restore_legacy_token(tmp_path: Path) -> None:
    legacy_token = tmp_path / "legacy/NEXUS.token"
    legacy_token.parent.mkdir(parents=True)
    legacy_token.write_text(SYNTHETIC_TOKEN + "\n")

    result = _source(tmp_path)

    assert result.returncode == 0, result.stderr
    runtime_token = (tmp_path / "runtime/seal/NEXUS.token").read_text().strip()
    assert runtime_token != SYNTHETIC_TOKEN
    assert legacy_token.read_text().strip() == SYNTHETIC_TOKEN


def test_explicit_legacy_mode_copies_synthetic_token(tmp_path: Path) -> None:
    runtime_token = tmp_path / "runtime/seal/NEXUS.token"
    runtime_token.parent.mkdir(parents=True)
    runtime_token.write_text(SYNTHETIC_TOKEN + "\n")

    result = _source(tmp_path, legacy_flag="0")

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "legacy/NEXUS.token").read_text().strip() == SYNTHETIC_TOKEN


def test_explicit_legacy_mode_restores_synthetic_token(tmp_path: Path) -> None:
    legacy_token = tmp_path / "legacy/NEXUS.token"
    legacy_token.parent.mkdir(parents=True)
    legacy_token.write_text(SYNTHETIC_TOKEN + "\n")

    result = _source(tmp_path, legacy_flag="0")

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "runtime/seal/NEXUS.token").read_text().strip() == SYNTHETIC_TOKEN


def test_explicit_disable_matches_secure_default(tmp_path: Path) -> None:
    result = _source(tmp_path, legacy_flag="1")
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "legacy").exists()
