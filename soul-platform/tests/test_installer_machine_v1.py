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
    assert 'PLATFORM_VERSION="0.5.0"' in text
    assert 'CORE_VERSION="0.4.2"' in text
    assert "verify_bundled_wheel" in text and "--find-links" in text
    assert '--no-deps --force-reinstall "$CORE_WHEEL"' in text
    assert 'PLATFORM_INSTALL_FLAGS=(--force-reinstall)' in text
    assert '"${PLATFORM_INSTALL_FLAGS[@]}" "${PIP_FIND_LINKS[@]}" "$SPEC"' in text
    assert 'direct_url.json' in text and '.resolve().as_uri()' in text
    assert 'archive_info' in text and 'verify_installed_wheel_provenance' in text
    assert 'ollama pull bge-m3' in text
    assert 'len(value["embeddings"][0]) == 1024' in text
    assert 'soul-machine-embedding-cutover" migrate' in text
    assert 'soul-machine-embedding-cutover" verify' in text
    assert 'soul-machine-embedding-cutover" activate' in text
    assert 'MachineSoul.bge.candidate.db' in text
    assert 'MachineSoul.bge.checkpoint.json' in text
    assert 'resume_args=(--resume)' in text
    assert 'recover_legacy_runtime' in text
    assert 'trap recover_legacy_runtime EXIT INT TERM' in text
    assert 'ProxyHandler({})' in text
    assert 'perfil embedding no soportado' in text
    assert 'config MachineSoul verificada: BGE-M3/1024/auto' in text
    activated = text.index('CUTOVER_ACTIVATED=1')
    failed_init = text.index('if ! "$VENV/bin/soul-machine" init', activated)
    rollback = text.index('embedding-cutover" rollback', failed_init)
    legacy_restart = text.index('soul-machine" init --root', rollback)
    assert activated < failed_init < rollback < legacy_restart


def test_windows_installer_is_user_space_and_initializes_machine_soul():
    text = (ROOT / "installer" / "Install-Soul.ps1").read_text()
    recovery = (ROOT / "installer" / "Soul-Installer-Recovery.psm1").read_text()
    assert 'Import-Module -Name $recoveryModule -Force' in text
    assert "Invoke-SoulPostActivateRuntime" in text
    assert '@("rollback", $SoulConfig, $Checkpoint)' in recovery
    assert '@("init", "--root", $SoulRoot' in recovery
    assert "soul-machine.exe" in text and '@("init", "--kind"' in recovery
    assert "LOCALAPPDATA" in text
    assert "Remove-Item" not in text
    assert "ExecutionPolicy" not in text
    assert "Start-Process -Verb RunAs" not in text
    assert 'Get-ChildItem -LiteralPath $PSScriptRoot -Filter "soul_platform-*.whl"' in text
    assert 'if ($actual -ne $expected)' in text
    assert 'return "soul-platform"' in text
    assert '${resolvedPackageSource}[desktop]' in text
    assert '"--force-reinstall", "--no-deps"' in text
    assert "if (-not $RequireBundledWheel)" in text
    assert "if ($RequireBundledWheel)" in text
    assert "$env:SOUL_PACKAGE_SOURCE" in text
    assert '"--no-deps", "--force-reinstall", $BundledCoreWheel' in text
    assert "m.distribution(name).read_text('direct_url.json')" in text
    assert ".resolve().as_uri()" in text and "archive_info" in text
    assert "soul-tray.exe" in text
    assert 'Invoke-Checked $tray @("--headless-check")' in text
    assert 'Invoke-Checked $tray @("--install-autostart")' in text
    assert '[version]"0.4.0"' in text
    assert "soul-framework 0.4.2 exacto" in text
    assert '$installedCoreVersion = & $venvPython -c' in text
    assert '$installedCoreVersion = & $python -c' not in text
    assert 'soul-machine-embedding-cutover.exe' in text
    assert 'soul-autowire.exe' in text
    assert 'soul-mcp-stdio.exe' in text
    assert 'soul-mcp-enroll.exe' in text
    assert '$resolvedPackageIsLocalFile' in text
    assert '$resolvedPackageIsBundled = $resolvedPackageIsLocalFile -and [bool]$BundledCoreWheel' in text
    assert 'Procedencia PEP 610 y hash del wheel local verificados' in text
    assert '@("upgrade-config", "--config", $soulConfig)' in text
    assert '@("--root", $soulRoot, "reconcile")' in text
    assert 'TrustCurrentOllama' not in text
    assert 'trust-current-ollama' not in text
    assert '@("--root", $soulRoot, "install-autostart")' in text
    assert 'Install-SoulClientMcp $soulMcp $soulMcpEnroll $soulConfig' in text
    assert 'Resolve-ClientParentBinary' in text and '--parent-executable' in text
    assert '--server-executable' in text
    assert '--rotate-existing' not in text
    assert 'Claude MCP quedo cableado y ligado a bytes; health vivo pendiente de iniciar sesion en Claude' in text
    assert 'Test-ClaudeSoulMcpShape $claudeCli $Mcp $Config' in text
    assert 'node_modules\\@openai\\codex\\node_modules' in text
    assert 'Where-Object' in text and r'codex\.exe$' in text
    assert 'Restore-ClientConfigCas' in text and 'cambio concurrentemente' in text
    assert 'Set-SoulPrivateAcl $soulRoot' in text
    assert 'function Invoke-NativeCapture' in text
    assert 'function Resolve-NativeCli' in text
    assert '[IO.Path]::ChangeExtension($source, ".cmd")' in text
    assert '$ErrorActionPreference = "Continue"' in text
    assert 'if ($probe.ExitCode -ne 0)' in text
    assert 'if ($existing.ExitCode -eq 0)' in text
    assert 'mcp", "add", "soul-local"' in text
    assert '"--scope", "user", "soul-local"' in text
    assert 'HOLD: Codex ya tiene soul-local' in text
    assert 'HOLD: Claude ya tiene soul-local' in text
    assert '$checkpoint = ""' in text
    assert '$codexAdded = $true' in text and '$claudeAdded = $true' in text
    assert 'function Undo-SoulClientMcpAdd' in text
    assert 'CAS before the destructive inverse' in text
    assert 'Invoke-NativeCapture $Cli @("mcp", "remove"' in text
    assert 'cambio despues del add; preserve sus bytes y no removi soul-local' in text
    assert '$rollbackIssues += $issue' in text
    assert 'Copy-Item -LiteralPath $claudeBackup -Destination $claudeConfig -Force' not in text
    assert 'Copy-Item -LiteralPath $codexBackup -Destination $codexConfig -Force' not in text
    assert '@("disable-autostart", "--config", $soulConfig)' in text
    assert '@("migrate", $soulDb, "--candidate", $candidate, "--checkpoint", $checkpoint)' in text
    assert '@("verify", $checkpoint)' in text
    assert '@("activate", $soulConfig, $checkpoint)' in text
    assert 'migracion parcial ambigua' in text
    assert 'Verificando BGE-M3 antes de detener el alma legacy' in text
    assert text.index('Verificando BGE-M3 antes') < text.index('@("disable-autostart"')
    assert 'reactivando el runtime legacy preservado' in text
    assert '@("init", "--root", $SoulRoot' in recovery
    assert '$isLegacyProfile' in text and '$isBgeProfile' in text
    assert 'provider\\s*=\\s*"simple"' in text
    assert 'vector_index\\s*=\\s*"auto"' in text
    assert 'perfil embedding no soportado' in text
    assert 'No existe una MachineSoul configurada' in text
    assert 'if (-not $NoTray -and -not $NoMachine)' in text
    activated = text.index('$cutoverActivated = $true')
    runtime = text.index('Invoke-SoulPostActivateRuntime', activated)
    assert activated < runtime
    guard = text.index("if (-not $NoMachine) {", text.index("$installedCoreVersion"))
    ollama = text.index('$ollamaCommand = Get-Command "ollama"')
    machine = text.index('$machine = Join-Path $Venv "Scripts\\soul-machine.exe"')
    assert guard < ollama < machine
    assert 'Good "BGE-M3 local verificado (1024 dimensiones)"\n}' in text


def test_windows_click_installer_is_local_and_non_elevating():
    text = (ROOT / "installer" / "Instalar-SOUL-Windows.bat").read_text()
    assert "Install-Soul.ps1" in text
    assert "-RequireBundledWheel" in text
    assert "-ExecutionPolicy Bypass" in text
    assert "RunAs" not in text
    assert "curl" not in text
def test_windows_novice_guide_matches_tray_release():
    text = (ROOT / "installer" / "LEEME-WINDOWS.txt").read_text()
    assert "SOUL PLATFORM 0.5.0" in text
    assert "icono violeta SOUL" in text
    assert "Copiar token local" in text
