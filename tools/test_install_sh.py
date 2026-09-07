"""Tests for install.sh — bash syntax validity + behavior of each step in dry-run mode."""

import os
import shutil
import subprocess
import sys
import tempfile
import pathlib

INSTALL_SH = pathlib.Path(__file__).resolve().parents[1] / "install.sh"


def test_install_sh_syntax_valid():
    """bash -n must accept install.sh without parse errors."""
    result = subprocess.run(
        ["bash", "-n", str(INSTALL_SH)], capture_output=True, text=True
    )
    assert result.returncode == 0, f"syntax error: {result.stderr}"


def test_install_sh_is_executable():
    """install.sh must have the executable bit set."""
    assert os.access(INSTALL_SH, os.X_OK), "install.sh is not executable"


def test_install_sh_has_shebang():
    text = INSTALL_SH.read_text()
    assert text.startswith("#!/usr/bin/env bash"), "missing bash shebang"


def test_install_sh_uses_strict_mode():
    text = INSTALL_SH.read_text()
    assert "set -euo pipefail" in text, "must enable strict mode"


def test_install_sh_writes_env_template():
    """When run with a target dir that has no .env, the template must be created."""
    tmp = tempfile.mkdtemp(prefix="seal-install-test-")
    try:
        # Pre-clone a fake repo so step_clone_or_update finds a .git
        subprocess.run(["git", "init", "-q", tmp], check=True)
        subprocess.run(
            ["git", "-C", tmp, "commit", "--allow-empty", "-m", "init", "-q"],
            check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
        )

        env = os.environ.copy()
        env["SEAL_TARGET_DIR"] = tmp
        env["SEAL_REPO_URL"] = tmp  # local "repo" so clone is offline
        # Skip venv + deps because we just want to exercise env template writing.
        # We achieve this by replacing python step functions via a wrapper script.
        wrapper = f"""
            source {INSTALL_SH}
            step_write_env_template
        """
        result = subprocess.run(
            ["bash", "-c", wrapper], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0, f"wrapper failed: {result.stderr}"
        env_file = pathlib.Path(tmp) / ".env"
        assert env_file.exists(), ".env was not created"
        contents = env_file.read_text()
        assert "ANTHROPIC_API_KEY=" in contents
        assert "DISCORD_BOT_TOKEN=" in contents
        assert "SOUL_DB_PORT=5433" in contents
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_install_sh_skips_existing_env():
    """If .env already exists, the installer must NOT overwrite it."""
    tmp = tempfile.mkdtemp(prefix="seal-install-test2-")
    try:
        env_file = pathlib.Path(tmp) / ".env"
        env_file.write_text("PRESERVED=true\n")
        env = os.environ.copy()
        env["SEAL_TARGET_DIR"] = tmp
        wrapper = f"""
            source {INSTALL_SH}
            step_write_env_template
        """
        result = subprocess.run(
            ["bash", "-c", wrapper], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0
        assert env_file.read_text() == "PRESERVED=true\n", "env file was overwritten"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    tests = [
        test_install_sh_syntax_valid,
        test_install_sh_is_executable,
        test_install_sh_has_shebang,
        test_install_sh_uses_strict_mode,
        test_install_sh_writes_env_template,
        test_install_sh_skips_existing_env,
    ]
    passed = 0
    for t in tests:
        t()
        print(f"[OK] {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
