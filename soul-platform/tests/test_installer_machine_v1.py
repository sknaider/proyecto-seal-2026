from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_unix_installer_is_parseable_and_initializes_machine_soul():
    installer = ROOT / "installer" / "soul-install.sh"
    subprocess.run(["bash", "-n", str(installer)], check=True)
    text = installer.read_text()
    assert 'soul-machine" init' in text
    assert "rm -rf" not in text
    assert "curl" not in text or "| bash" not in text


def test_windows_installer_is_user_space_and_initializes_machine_soul():
    text = (ROOT / "installer" / "Install-Soul.ps1").read_text()
    assert "soul-machine.exe" in text and '@("init", "--kind"' in text
    assert "LOCALAPPDATA" in text
    assert "Remove-Item" not in text
    assert "ExecutionPolicy" not in text
    assert "Start-Process -Verb RunAs" not in text
    assert (
        'Get-ChildItem -LiteralPath $PSScriptRoot -Filter "soul_platform-*.whl"' in text
    )
    assert "if ($actual -ne $expected)" in text
    assert 'return "soul-platform"' in text
    assert '"--force-reinstall", "--no-deps"' in text
    assert "if (-not $RequireBundledWheel)" in text
    assert "if ($RequireBundledWheel)" in text
    assert "$env:SOUL_PACKAGE_SOURCE" in text
    assert '"pystray==0.19.5", "pillow==12.3.0"' in text
    assert "Scripts\\soul-tray.exe" in text
    assert "Scripts\\soul-tray-cli.exe" in text
    assert 'Invoke-Checked $trayCli @("--check-desktop")' in text
    assert 'Invoke-Checked $trayCli @("--install-autostart")' in text
    assert "& $trayCli --remove-autostart" in text
    assert "$trayAutostartInstalled = $true" in text
    assert "if (-not $Check -and $NoTray)" in text
    assert 'Invoke-Checked $trayCli @("--remove-autostart")' in text
    assert 'Invoke-Checked $trayCli @("--check")' in text
    assert "Start-Process -FilePath $tray" in text
    assert "-PassThru" in text and "$trayProcess.HasExited" in text
    assert "Start-Process -Verb RunAs" not in text
    assert '[version]"0.4.0"' in text
    assert "soul-framework 0.4.2 exacto" in text
    assert '$installedCoreVersion = & $venvPython -c' in text
    assert '$installedCoreVersion = & $python -c' not in text
    assert 'soul-machine-embedding-cutover.exe' in text
    assert '@("disable-autostart", "--config", $soulConfig)' in text
    assert '@("migrate", $soulDb, "--candidate", $candidate, "--checkpoint", $checkpoint)' in text
    assert '@("verify", $checkpoint)' in text
    assert '@("activate", $soulConfig, $checkpoint)' in text
    assert 'migracion parcial ambigua' in text
    assert 'Verificando BGE-M3 antes de detener el alma legacy' in text
    assert '$probe.embeddings[0].Count -ne 1024' in text
    assert text.index('Verificando BGE-M3 antes') < text.index('@("disable-autostart"')
    assert 'reactivando el runtime legacy preservado' in text
    assert '@("init", "--root", $soulRoot' in text
    assert '$isLegacyProfile' in text and '$isBgeProfile' in text
    assert 'provider\\s*=\\s*"simple"' in text
    assert 'vector_index\\s*=\\s*"auto"' in text
    assert 'perfil embedding no soportado' in text


def test_windows_click_installer_is_local_and_non_elevating():
    text = (ROOT / "installer" / "Instalar-SOUL-Windows.bat").read_text()
    assert "Install-Soul.ps1" in text
    assert "-RequireBundledWheel" in text
    assert "-ExecutionPolicy Bypass" in text
    assert "RunAs" not in text
    assert "curl" not in text


def test_windows_novice_guide_matches_tray_release():
    text = (ROOT / "installer" / "LEEME-WINDOWS.txt").read_text()
    assert "SOUL PLATFORM 0.4.0" in text
    assert "icono violeta SOUL" in text
    assert "Copiar token local" in text
