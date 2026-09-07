from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL_IMAGE = (
    "mcr.microsoft.com/powershell@"
    "sha256:e69d1ba31146ce79f1b84893f2f89adae1e4a4308a96e821aa6cc886de991710"
)


def test_windows_post_activate_recovery_by_effect():
    """Execute the production recovery module with a faulted new runtime."""
    if os.environ.get("SEAL_REQUIRE_POWERSHELL_FAULT_INJECTION") != "1":
        pytest.skip("release-only PowerShell container gate")
    docker = shutil.which("docker")
    assert docker, "Docker is required for the release-only PowerShell gate"
    result = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "-v",
            f"{ROOT}:/work:ro",
            POWERSHELL_IMAGE,
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            "/work/tests/windows_recovery_fault_injection.ps1",
            "-ModulePath",
            "/work/installer/Soul-Installer-Recovery.psm1",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "WINDOWS_RECOVERY_FAULT_INJECTION_OK rollback_events=3 fresh_events=1"
        in result.stdout
    )


def test_windows_client_config_rollback_cas_by_effect():
    """Execute the production CAS functions against A/B/D byte transitions."""
    if os.environ.get("SEAL_REQUIRE_POWERSHELL_FAULT_INJECTION") != "1":
        pytest.skip("release-only PowerShell container gate")
    docker = shutil.which("docker")
    assert docker, "Docker is required for the release-only PowerShell gate"
    result = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "-v",
            f"{ROOT}:/work:ro",
            POWERSHELL_IMAGE,
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            "/work/tests/windows_client_config_cas.ps1",
            "-InstallerPath",
            "/work/installer/Install-Soul.ps1",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "WINDOWS_CLIENT_CONFIG_CAS_OK exact=1 concurrent_preserved=1 missing_restored=1"
        in result.stdout
    )
