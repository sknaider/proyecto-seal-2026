from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_unix_installer_is_parseable_and_initializes_machine_soul():
    installer = ROOT / "installer" / "soul-install.sh"
    subprocess.run(["bash", "-n", str(installer)], check=True)
    text = installer.read_text()
    assert "soul-machine\" init" in text
    assert "rm -rf" not in text
    assert "curl" not in text or "| bash" not in text


def test_windows_installer_is_user_space_and_initializes_machine_soul():
    text = (ROOT / "installer" / "Install-Soul.ps1").read_text()
    assert "soul-machine.exe" in text and '@("init", "--kind"' in text
    assert "LOCALAPPDATA" in text
    assert "Remove-Item" not in text
    assert "ExecutionPolicy" not in text
    assert "Start-Process -Verb RunAs" not in text
    assert 'Get-ChildItem -LiteralPath $PSScriptRoot -Filter "soul_platform-*.whl"' in text
    assert 'if ($actual -ne $expected)' in text
    assert 'return "soul-platform"' in text
    assert '"--force-reinstall", "--no-deps"' in text
    assert "if (-not $RequireBundledWheel)" in text
    assert "if ($RequireBundledWheel)" in text
    assert "$env:SOUL_PACKAGE_SOURCE" in text


def test_windows_click_installer_is_local_and_non_elevating():
    text = (ROOT / "installer" / "Instalar-SOUL-Windows.bat").read_text()
    assert "Install-Soul.ps1" in text
    assert "-RequireBundledWheel" in text
    assert "-ExecutionPolicy Bypass" in text
    assert "RunAs" not in text
    assert "curl" not in text
