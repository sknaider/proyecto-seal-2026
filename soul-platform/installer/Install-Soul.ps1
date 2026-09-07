[CmdletBinding()]
param(
    [string]$Model = $env:SOUL_MODEL,
    [string]$Kind = $(if ($env:SOUL_UPSTREAM_KIND) { $env:SOUL_UPSTREAM_KIND } else { "ollama" }),
    [string]$BaseUrl = $(if ($env:SOUL_UPSTREAM_URL) { $env:SOUL_UPSTREAM_URL } else { "http://127.0.0.1:11434/v1" }),
    [string]$Venv = $(if ($env:SOUL_VENV) { $env:SOUL_VENV } else { Join-Path $env:LOCALAPPDATA "SOUL\venv" }),
    [string]$PackageSource,
    [switch]$RequireBundledWheel,
    [switch]$NoMachine,
    [switch]$NoTray,
    [switch]$Check
)

$ErrorActionPreference = "Stop"
$recoveryModule = Join-Path $PSScriptRoot "Soul-Installer-Recovery.psm1"
if (-not (Test-Path -LiteralPath $recoveryModule -PathType Leaf)) {
    throw "Falta el modulo de recuperacion verificado: $recoveryModule"
}
Import-Module -Name $recoveryModule -Force

function Step([string]$Message) { Write-Host "[SOUL] $Message" -ForegroundColor Cyan }
function Good([string]$Message) { Write-Host "  OK $Message" -ForegroundColor Green }
function Invoke-NativeCapture([string]$File, [string[]]$Arguments) {
    # Windows PowerShell can promote a native program's stderr to a terminating
    # NativeCommandError while `$ErrorActionPreference = "Stop"`, even when the
    # program is merely reporting a normal negative probe. Capture the stream
    # and decide exclusively from the native exit code.
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $File @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    return [pscustomobject]@{ ExitCode = $exitCode; Output = @($output) }
}

function Resolve-NativeCli([System.Management.Automation.CommandInfo]$Command) {
    # npm installs both .ps1 and .cmd shims on Windows. A PowerShell shim can
    # consume the `--` separator before Claude Code sees it, causing MCP server
    # arguments such as `--config` to be misparsed as Claude options. Prefer the
    # byte-adjacent .cmd shim when available.
    $source = [string]$Command.Source
    if ([IO.Path]::GetExtension($source).Equals(".ps1", [StringComparison]::OrdinalIgnoreCase)) {
        $cmdShim = [IO.Path]::ChangeExtension($source, ".cmd")
        if (Test-Path -LiteralPath $cmdShim -PathType Leaf) { return $cmdShim }
    }
    return $source
}

function Invoke-Checked([string]$File, [string[]]$Arguments) {
    $result = Invoke-NativeCapture $File $Arguments
    foreach ($line in $result.Output) { Write-Host ([string]$line) }
    if ($result.ExitCode -ne 0) {
        throw "El comando fallo con codigo $($result.ExitCode): $File $($Arguments -join ' ')"
    }
}

function Backup-ClientConfig([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $backupRoot = Join-Path $env:LOCALAPPDATA "SOUL\client-config-backups"
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    $digest = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant().Substring(0, 16)
    $target = Join-Path $backupRoot ("{0}-{1}-{2}.bak" -f $Label, [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'), $digest)
    Copy-Item -LiteralPath $Path -Destination $target
    return $target
}

function Get-OptionalFileHash([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return "__MISSING__" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Restore-ClientConfigCas(
    [string]$Path, [string]$Backup, [string]$BeforeHash,
    [string]$PostAddHash, [string]$Label
) {
    $current = Get-OptionalFileHash $Path
    if ($current -eq $BeforeHash) { return }
    if ($current -ne $PostAddHash) {
        throw "HOLD: $Label cambio concurrentemente; preservo esos bytes y no ejecuto rollback destructivo"
    }
    if ($BeforeHash -eq "__MISSING__") {
        [IO.File]::Delete($Path)
    } elseif ($Backup) {
        Copy-Item -LiteralPath $Backup -Destination $Path -Force
    } else {
        throw "HOLD: falta backup verificable de $Label"
    }
    if ((Get-OptionalFileHash $Path) -ne $BeforeHash) {
        throw "Rollback CAS de $Label no reprodujo los bytes previos"
    }
}

function Resolve-ClientParentBinary([string]$ClientId, [string]$CommandPath) {
    $command = Get-Item -LiteralPath $CommandPath -ErrorAction Stop
    if ($command.Extension -ieq ".exe") { return $command.FullName }
    $base = $command.Directory.FullName
    if ($ClientId -eq "claude") {
        $candidate = Join-Path $base "node_modules\@anthropic-ai\claude-code\bin\claude.exe"
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    if ($ClientId -eq "codex") {
        $nativeRoot = Join-Path $base "node_modules\@openai\codex\node_modules"
        $candidates = @(
            Get-ChildItem -LiteralPath $nativeRoot -Filter "codex.exe" -File -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match '\\vendor\\[^\\]+\\bin\\codex\.exe$' }
        )
        if ($candidates.Count -eq 1) { return $candidates[0].FullName }
        if ($candidates.Count -gt 1) {
            throw "HOLD: encontre varios binarios nativos de Codex; no puedo ligar el grant sin ambiguedad"
        }
    }
    throw "HOLD: no pude ligar $ClientId a un binario padre unico y verificable"
}

function Set-SoulPrivateAcl([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $system = New-Object Security.Principal.SecurityIdentifier("S-1-5-18")
    $isDirectory = Test-Path -LiteralPath $Path -PathType Container
    if ($isDirectory) {
        $acl = New-Object Security.AccessControl.DirectorySecurity
        $inherit = [Security.AccessControl.InheritanceFlags]"ContainerInherit, ObjectInherit"
        $propagation = [Security.AccessControl.PropagationFlags]::None
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($identity,"FullControl",$inherit,$propagation,"Allow")))
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($system,"FullControl",$inherit,$propagation,"Allow")))
    } else {
        $acl = New-Object Security.AccessControl.FileSecurity
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($identity,"FullControl","Allow")))
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($system,"FullControl","Allow")))
    }
    $acl.SetAccessRuleProtection($true, $false)
    Set-Acl -LiteralPath $Path -AclObject $acl
    $live = Get-Acl -LiteralPath $Path
    $allowed = @($identity.Value, $system.Value)
    $unexpected = @($live.Access | Where-Object {
        $sid = $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        $_.AccessControlType -eq "Allow" -and $sid -notin $allowed
    })
    if (-not $live.AreAccessRulesProtected -or $unexpected.Count -ne 0) {
        throw "ACL privada no se verifico para $Path"
    }
}

function Assert-CodexSoulMcp([string]$Codex, [string]$Mcp, [string]$Config) {
    $probe = Invoke-NativeCapture $Codex @("mcp", "get", "soul-local", "--json")
    if ($probe.ExitCode -ne 0) { return $false }
    try { $entry = (($probe.Output -join "`n") | ConvertFrom-Json) } catch { return $false }
    return (
        $entry.enabled -eq $true -and
        $entry.transport.type -eq "stdio" -and
        [IO.Path]::GetFullPath([string]$entry.transport.command) -eq [IO.Path]::GetFullPath($Mcp) -and
        (@($entry.transport.args) -join "`0") -eq (@("--config", $Config, "--client-id", "codex") -join "`0")
    )
}

function Assert-ClaudeSoulMcp([string]$Claude, [string]$Mcp, [string]$Config) {
    $probe = Invoke-NativeCapture $Claude @("mcp", "get", "soul-local")
    if ($probe.ExitCode -ne 0) { return $false }
    $text = $probe.Output -join "`n"
    return (
        $text -notmatch 'Failed to connect|No MCP server found' -and
        $text -match [regex]::Escape("Command: $Mcp") -and
        $text -match [regex]::Escape("Args: --config $Config --client-id claude")
    )
}

function Test-ClaudeSoulMcpShape([string]$Claude, [string]$Mcp, [string]$Config) {
    $probe = Invoke-NativeCapture $Claude @("mcp", "get", "soul-local")
    $text = $probe.Output -join "`n"
    return (
        $text -notmatch 'No MCP server found' -and
        $text -match [regex]::Escape("Command: $Mcp") -and
        $text -match [regex]::Escape("Args: --config $Config --client-id claude")
    )
}

function Undo-SoulClientMcpAdd(
    [string]$ClientId, [string]$Cli, [string]$ConfigPath,
    [string]$BeforeHash, [string]$PostAddHash
) {
    # CAS before the destructive inverse: if another writer changed B to E,
    # removing by name would delete that writer's entry. Preserve E and HOLD.
    if ((Get-OptionalFileHash $ConfigPath) -ne $PostAddHash) {
        return "$ClientId cambio despues del add; preserve sus bytes y no removi soul-local"
    }
    if ($ClientId -eq "claude") {
        $removed = Invoke-NativeCapture $Cli @("mcp", "remove", "soul-local", "--scope", "user")
        $probe = Invoke-NativeCapture $Cli @("mcp", "get", "soul-local")
        if ($removed.ExitCode -ne 0 -or ($probe.Output -join "`n") -notmatch 'No MCP server found') {
            return "Claude no confirmo la remocion; preserve la configuracion actual"
        }
    } else {
        $removed = Invoke-NativeCapture $Cli @("mcp", "remove", "soul-local")
        $probe = Invoke-NativeCapture $Cli @("mcp", "get", "soul-local", "--json")
        if ($removed.ExitCode -ne 0 -or $probe.ExitCode -eq 0) {
            return "Codex no confirmo la remocion; preserve la configuracion actual"
        }
    }
    if ((Get-OptionalFileHash $ConfigPath) -ne $BeforeHash) {
        return "$ClientId cambio antes del add; preserve sus bytes ya sin soul-local"
    }
    return $null
}

function Install-SoulClientMcp([string]$Mcp, [string]$Enroller, [string]$Config) {
    if (-not (Test-Path -LiteralPath $Mcp -PathType Leaf)) {
        throw "Falta soul-mcp-stdio.exe en el paquete instalado"
    }
    if (-not (Test-Path -LiteralPath $Enroller -PathType Leaf)) {
        throw "Falta soul-mcp-enroll.exe en el paquete instalado"
    }
    $wired = @()
    $needCodex = $false
    $needClaude = $false
    $codex = Get-Command "codex" -ErrorAction SilentlyContinue
    if ($codex) {
        $codexCli = Resolve-NativeCli $codex
        if (-not (Assert-CodexSoulMcp $codexCli $Mcp $Config)) {
            $existing = Invoke-NativeCapture $codexCli @("mcp", "get", "soul-local", "--json")
            if ($existing.ExitCode -eq 0) {
                throw "HOLD: Codex ya tiene soul-local con otro comando; no lo sobrescribo"
            }
            $needCodex = $true
        }
    }
    $claude = Get-Command "claude" -ErrorAction SilentlyContinue
    if ($claude) {
        $claudeCli = Resolve-NativeCli $claude
        if (-not (Test-ClaudeSoulMcpShape $claudeCli $Mcp $Config)) {
            $existing = Invoke-NativeCapture $claudeCli @("mcp", "get", "soul-local")
            if (($existing.Output -join "`n") -notmatch 'No MCP server found') {
                throw "HOLD: Claude ya tiene soul-local distinto o no saludable; no lo sobrescribo"
            }
            $needClaude = $true
        }
    }

    # Preflight both clients before the first mutation. If either post-add gate
    # fails, remove only the entries created here and restore exact config bytes.
    $codexConfig = Join-Path $env:USERPROFILE ".codex\config.toml"
    $claudeConfig = Join-Path $env:USERPROFILE ".claude.json"
    $codexExisted = Test-Path -LiteralPath $codexConfig -PathType Leaf
    $claudeExisted = Test-Path -LiteralPath $claudeConfig -PathType Leaf
    $codexBeforeHash = Get-OptionalFileHash $codexConfig
    $claudeBeforeHash = Get-OptionalFileHash $claudeConfig
    $codexBackup = $(if ($needCodex) { Backup-ClientConfig $codexConfig "codex" } else { $null })
    $claudeBackup = $(if ($needClaude) { Backup-ClientConfig $claudeConfig "claude" } else { $null })
    $codexAdded = $false
    $claudeAdded = $false
    $codexPostAddHash = $codexBeforeHash
    $claudePostAddHash = $claudeBeforeHash
    try {
        if ($needCodex) {
            Invoke-Checked $codexCli @("mcp", "add", "soul-local", "--", $Mcp, "--config", $Config, "--client-id", "codex")
            $codexAdded = $true
            $codexPostAddHash = Get-OptionalFileHash $codexConfig
        }
        if ($needClaude) {
            Invoke-Checked $claudeCli @("mcp", "add", "--scope", "user", "soul-local", "--", $Mcp, "--config", $Config, "--client-id", "claude")
            $claudeAdded = $true
            $claudePostAddHash = Get-OptionalFileHash $claudeConfig
        }
        if ($codex) {
            $codexParent = Resolve-ClientParentBinary "codex" $codexCli
            Invoke-Checked $Enroller @("--config", $Config, "--client-id", "codex", "--parent-executable", $codexParent, "--server-executable", $Mcp)
            if (-not (Assert-CodexSoulMcp $codexCli $Mcp $Config)) {
                throw "Codex no confirmo el MCP local de SOUL"
            }
        }
        if ($claude) {
            $claudeParent = Resolve-ClientParentBinary "claude" $claudeCli
            Invoke-Checked $Enroller @("--config", $Config, "--client-id", "claude", "--parent-executable", $claudeParent, "--server-executable", $Mcp)
            if (-not (Test-ClaudeSoulMcpShape $claudeCli $Mcp $Config)) {
                throw "Claude no preservo la configuracion MCP local exacta de SOUL"
            }
            if (-not (Assert-ClaudeSoulMcp $claudeCli $Mcp $Config)) {
                # Claude Code reports every configured MCP as disconnected when
                # its provider OAuth is revoked.  That external account state
                # must not roll back a byte-bound local MCP enrollment.  The
                # exact command/args and immutable parent/server grants above
                # remain authoritative; live health becomes pending login.
                Write-Warning "Claude MCP quedo cableado y ligado a bytes; health vivo pendiente de iniciar sesion en Claude"
            }
        }
    } catch {
        $originalFailure = $_
        $rollbackIssues = @()
        if ($claudeAdded) {
            $issue = Undo-SoulClientMcpAdd "claude" $claudeCli $claudeConfig $claudeBeforeHash $claudePostAddHash
            if ($issue) { $rollbackIssues += $issue }
        }
        if ($codexAdded) {
            $issue = Undo-SoulClientMcpAdd "codex" $codexCli $codexConfig $codexBeforeHash $codexPostAddHash
            if ($issue) { $rollbackIssues += $issue }
        }
        if ($rollbackIssues.Count -gt 0) {
            throw ("HOLD: " + ($rollbackIssues -join "; "))
        }
        throw $originalFailure
    }
    if ($codex) { $wired += "codex" }
    if ($claude) { $wired += "claude" }
    return $wired
}

function Resolve-PackageSource {
    if (-not $RequireBundledWheel) {
        if ($PackageSource) { return $PackageSource }
        if ($env:SOUL_PACKAGE_SOURCE) { return $env:SOUL_PACKAGE_SOURCE }
    }

    $bundledWheels = @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter "soul_platform-*.whl" -File)
    if ($bundledWheels.Count -gt 1) {
        throw "Hay varios wheels soul-platform junto al instalador. Deja solo el que quieras instalar o usa -PackageSource."
    }
    if ($bundledWheels.Count -eq 1) {
        $wheel = $bundledWheels[0]
        $checksumFile = "$($wheel.FullName).sha256"
        if (-not (Test-Path -LiteralPath $checksumFile -PathType Leaf)) {
            throw "Falta el checksum del paquete incluido: $checksumFile"
        }
        $expected = ((Get-Content -LiteralPath $checksumFile -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
        if ($expected -notmatch '^[0-9a-f]{64}$') {
            throw "El checksum incluido no tiene formato SHA-256 valido."
        }
        $actual = (Get-FileHash -LiteralPath $wheel.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) {
            throw "El wheel incluido no coincide con su SHA-256. No lo instalo."
        }
        Good "Paquete incluido verificado: $($wheel.Name) ($actual)"
        $coreWheels = @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter "soul_framework-0.4.2-*.whl" -File)
        if ($coreWheels.Count -ne 1) {
            throw "El bundle debe incluir exactamente un wheel soul-framework 0.4.2."
        }
        $coreWheel = $coreWheels[0]
        $coreChecksumFile = "$($coreWheel.FullName).sha256"
        if (-not (Test-Path -LiteralPath $coreChecksumFile -PathType Leaf)) {
            throw "Falta el checksum del Core incluido: $coreChecksumFile"
        }
        $coreExpected = ((Get-Content -LiteralPath $coreChecksumFile -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
        $coreActual = (Get-FileHash -LiteralPath $coreWheel.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($coreExpected -notmatch '^[0-9a-f]{64}$' -or $coreActual -ne $coreExpected) {
            throw "El wheel soul-framework incluido no coincide con su SHA-256."
        }
        $script:BundledCoreWheel = $coreWheel.FullName
        $script:BundledPlatformHash = $actual
        $script:BundledCoreHash = $coreActual
        Good "SOUL Core incluido verificado: $($coreWheel.Name) ($coreActual)"
        return $wheel.FullName
    }

    if ($RequireBundledWheel) {
        throw "El instalador de doble clic exige exactamente un wheel soul-platform incluido y su checksum. Extrae todo el ZIP y reintenta."
    }

    return "soul-platform"
}

function Find-Python {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3.13") },
        @{ Exe = "py"; Args = @("-3.12") },
        @{ Exe = "py"; Args = @("-3.11") },
        @{ Exe = "python"; Args = @() }
    )
    foreach ($candidate in $candidates) {
        try {
            $version = & $candidate.Exe @($candidate.Args) -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            $parts = $version.Trim().Split('.')
            if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11)) {
                return $candidate
            }
        } catch { }
    }
    throw "Python 3.11+ no esta instalado. Instala Python desde python.org y vuelve a ejecutar este archivo."
}

$launcher = Find-Python
Good "Python detectado"
$resolvedPackageSource = Resolve-PackageSource
$resolvedPackageIsLocalFile = Test-Path -LiteralPath $resolvedPackageSource -PathType Leaf
$resolvedPackageIsBundled = $resolvedPackageIsLocalFile -and [bool]$BundledCoreWheel
$localPlatformHash = $(if ($resolvedPackageIsLocalFile) {
    (Get-FileHash -LiteralPath $resolvedPackageSource -Algorithm SHA256).Hash.ToLowerInvariant()
} else { "" })
$resolvedInstallSpec = "${resolvedPackageSource}[desktop]"
$venvPython = Join-Path $Venv "Scripts\python.exe"
$soulRoot = Join-Path $env:LOCALAPPDATA "SOUL"
$soulConfig = Join-Path $soulRoot "proxy.toml"
$soulDb = Join-Path $soulRoot "MachineSoul.db"
$legacyMigrationRequired = $false
$legacyModel = ""
$cutoverActivated = $false
$checkpoint = ""
if (-not $NoMachine -and (Test-Path -LiteralPath $soulConfig -PathType Leaf)) {
    $soulConfigText = Get-Content -LiteralPath $soulConfig -Raw
    $embeddingMatch = [regex]::Match(
        $soulConfigText,
        '(?ms)^\s*\[embedding\]\s*\r?\n(?<body>.*?)(?=^\s*\[[^\]]+\]\s*$|\z)'
    )
    if (-not $embeddingMatch.Success) {
        $legacyMigrationRequired = $true
    } else {
        $embeddingBody = $embeddingMatch.Groups['body'].Value
        $isBgeProfile = (
            $embeddingBody -match '(?m)^\s*provider\s*=\s*"bge-m3"\s*$' -and
            $embeddingBody -match '(?m)^\s*dimensions\s*=\s*1024\s*$' -and
            $embeddingBody -match '(?m)^\s*model\s*=\s*"bge-m3"\s*$' -and
            $embeddingBody -match '(?m)^\s*vector_index\s*=\s*"auto"\s*$'
        )
        $isLegacyProfile = (
            $embeddingBody -match '(?m)^\s*provider\s*=\s*"simple"\s*$' -and
            $embeddingBody -match '(?m)^\s*dimensions\s*=\s*128\s*$' -and
            $embeddingBody -match '(?m)^\s*model\s*=\s*"simple"\s*$' -and
            $embeddingBody -match '(?m)^\s*vector_index\s*=\s*"exact"\s*$'
        )
        if ($isLegacyProfile) {
            $legacyMigrationRequired = $true
        } elseif (-not $isBgeProfile) {
            throw "HOLD: perfil embedding no soportado; se requiere simple/128/exact o bge-m3/1024/auto"
        }
    }
    $upstreamMatch = [regex]::Match(
        $soulConfigText,
        '(?ms)^\s*\[upstream\]\s*\r?\n(?<body>.*?)(?=^\s*\[[^\]]+\]\s*$|\z)'
    )
    if ($upstreamMatch.Success) {
        $modelMatch = [regex]::Match($upstreamMatch.Groups['body'].Value, '(?m)^\s*model\s*=\s*"(?<model>[^"]+)"\s*$')
        if ($modelMatch.Success) { $legacyModel = $modelMatch.Groups['model'].Value }
    }
}

if ($legacyMigrationRequired -and $Check) {
    throw "HOLD: MachineSoul usa embeddings legacy 128d. Ejecuta el instalador sin -Check para migrar de forma reversible."
}
if ($Check -and -not $NoMachine -and -not (Test-Path -LiteralPath $soulConfig -PathType Leaf)) {
    throw "No existe una MachineSoul configurada. Ejecuta el instalador sin -Check."
}

if (-not $Check) {
    if (Test-Path $Venv) {
        if (-not (Test-Path (Join-Path $Venv "pyvenv.cfg"))) {
            throw "$Venv existe pero no es un entorno virtual. No lo modifico; elige otra ruta con -Venv."
        }
        if (-not (Test-Path $venvPython)) {
            $backup = "$Venv.broken.$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))"
            Move-Item -LiteralPath $Venv -Destination $backup
            Step "Entorno incompleto preservado en $backup"
        }
    }
    if (-not (Test-Path $venvPython)) {
        Step "Creando entorno aislado en $Venv"
        Invoke-Checked $launcher.Exe (@($launcher.Args) + @("-m", "venv", $Venv))
    }
    Step "Instalando SOUL Platform dentro del entorno aislado"
    Invoke-Checked $venvPython @("-m", "pip", "install", "--upgrade", "pip")
    if ($resolvedPackageIsBundled) {
        # Install the exact hash-verified Core bytes first.  Merely offering a
        # --find-links directory still allows an index candidate with the same
        # version to win dependency resolution.
        Invoke-Checked $venvPython @("-m", "pip", "install", "--no-deps", "--force-reinstall", $BundledCoreWheel)
        Invoke-Checked $venvPython @("-m", "pip", "install", "--upgrade", "--find-links", $PSScriptRoot, $resolvedInstallSpec)
    } elseif ($resolvedPackageIsLocalFile) {
        Invoke-Checked $venvPython @("-m", "pip", "install", "--force-reinstall", "--no-deps", $resolvedPackageSource)
    } else {
        Invoke-Checked $venvPython @("-m", "pip", "install", "--upgrade", $resolvedInstallSpec)
    }
    if ($resolvedPackageIsBundled) {
        # `pip --upgrade` skips a local wheel when the same version is already
        # present. Reinstall only this package so updates with an unchanged
        # semantic version still load the exact verified bundle bytes.
        Invoke-Checked $venvPython @("-m", "pip", "install", "--force-reinstall", "--no-deps", $resolvedPackageSource)
        Invoke-Checked $venvPython @("-m", "pip", "install", "--force-reinstall", "--no-deps", $BundledCoreWheel)
    }
}

if (-not (Test-Path $venvPython)) { throw "No existe un entorno SOUL verificable en $Venv" }
Invoke-Checked $venvPython @("-c", "import soul_platform, soul_framework")
Invoke-Checked $venvPython @("-m", "pip", "check")
$installedVersion = & $venvPython -c "from importlib.metadata import version; print(version('soul-platform'))"
if ($LASTEXITCODE -ne 0) { throw "No pude leer la version instalada de soul-platform" }
if ([version]$installedVersion.Trim() -lt [version]"0.4.0") {
    throw "Se requiere soul-platform 0.4.0 o superior; quedo instalada $installedVersion"
}

$installedCoreVersion = & $venvPython -c "import importlib.metadata as m; print(m.version('soul-framework'))"
if ($LASTEXITCODE -ne 0 -or $installedCoreVersion.Trim() -ne "0.4.2") {
    throw "Se requiere soul-framework 0.4.2 exacto; quedo instalada $installedCoreVersion"
}
if ($resolvedPackageIsLocalFile) {
    $provenanceCheck = "import importlib.metadata as m,json,pathlib,sys,urllib.parse; name,wheel,expected=sys.argv[1:]; d=json.loads(m.distribution(name).read_text('direct_url.json')); url=str(d.get('url') or ''); assert urllib.parse.urlsplit(url).scheme=='file'; assert url==pathlib.Path(wheel).resolve().as_uri(); hashes=(d.get('archive_info') or {}).get('hashes') or {}; observed=str(hashes.get('sha256') or '').removeprefix('sha256=').lower(); assert observed==expected.lower()"
    Invoke-Checked $venvPython @("-c", $provenanceCheck, "soul-platform", $resolvedPackageSource, $localPlatformHash)
    if ($resolvedPackageIsBundled) {
        Invoke-Checked $venvPython @("-c", $provenanceCheck, "soul-framework", $BundledCoreWheel, $BundledCoreHash)
    }
    Good "Procedencia PEP 610 y hash del wheel local verificados"
}

if (-not $NoMachine) {
$ollamaCommand = Get-Command "ollama" -ErrorAction SilentlyContinue
if (-not $ollamaCommand) {
    throw "SOUL 0.4 requiere Ollama local para BGE-M3. Instala Ollama y vuelve a ejecutar el instalador."
}
$ollamaList = @(& $ollamaCommand.Source list 2>$null)
$bgeInstalled = @($ollamaList | Where-Object { $_ -match '^bge-m3(:latest)?\s' }).Count -gt 0
if (-not $bgeInstalled) {
    if ($Check) { throw "Falta el modelo local bge-m3. Ejecuta el instalador sin -Check para instalarlo." }
    Step "Instalando BGE-M3 local para memoria multilingue"
    Invoke-Checked $ollamaCommand.Source @("pull", "bge-m3")
}
Invoke-Checked $venvPython @("-c", "import asyncio; from soul_platform.local_embedding import LocalBgeM3Embedding as E; assert len(asyncio.run(E().embed('SOUL readiness')))==1024")
Good "BGE-M3 local verificado (1024 dimensiones)"
}

$machine = Join-Path $Venv "Scripts\soul-machine.exe"
if (-not (Test-Path $machine)) { throw "Falta soul-machine.exe en el paquete instalado" }
$cutover = Join-Path $Venv "Scripts\soul-machine-embedding-cutover.exe"
if (-not (Test-Path $cutover)) { throw "Falta soul-machine-embedding-cutover.exe en el paquete instalado" }
$autowire = Join-Path $Venv "Scripts\soul-autowire.exe"
if (-not (Test-Path $autowire)) { throw "Falta soul-autowire.exe en el paquete instalado" }
$soulMcp = Join-Path $Venv "Scripts\soul-mcp-stdio.exe"
if (-not (Test-Path $soulMcp)) { throw "Falta soul-mcp-stdio.exe en el paquete instalado" }
$soulMcpEnroll = Join-Path $Venv "Scripts\soul-mcp-enroll.exe"
if (-not (Test-Path $soulMcpEnroll)) { throw "Falta soul-mcp-enroll.exe en el paquete instalado" }

if (-not $NoMachine -and $legacyMigrationRequired) {
    if (-not (Test-Path -LiteralPath $soulDb -PathType Leaf)) {
        throw "HOLD: existe config legacy pero falta la base MachineSoul.db"
    }
    Step "Verificando BGE-M3 antes de detener el alma legacy"
    try {
        Invoke-Checked $venvPython @("-c", "import asyncio; from soul_platform.local_embedding import LocalBgeM3Embedding as E; assert len(asyncio.run(E().embed('SOUL readiness')))==1024")
    } catch {
        throw "HOLD: BGE-M3 local no esta listo; el alma legacy sigue activa. Ejecuta: ollama pull bge-m3"
    }
    Step "Deteniendo el runtime legacy solo despues del probe BGE-M3 verde"
    Invoke-Checked $machine @("disable-autostart", "--config", $soulConfig)

    $candidate = Join-Path $soulRoot "MachineSoul.bge-m3.candidate.db"
    $checkpoint = Join-Path $soulRoot "MachineSoul.bge-m3.checkpoint.json"
    $candidateExists = Test-Path -LiteralPath $candidate -PathType Leaf
    $checkpointExists = Test-Path -LiteralPath $checkpoint -PathType Leaf
    if ($candidateExists -xor $checkpointExists) {
        throw "HOLD: migracion parcial ambigua; candidate y checkpoint deben existir juntos"
    }
    try {
        if ($candidateExists -and $checkpointExists) {
            & $cutover verify $checkpoint *> $null
            if ($LASTEXITCODE -ne 0) {
                Step "Reanudando migracion BGE-M3 desde checkpoint"
                Invoke-Checked $cutover @("migrate", $soulDb, "--candidate", $candidate, "--checkpoint", $checkpoint, "--resume")
            }
        } else {
            Step "Migrando MachineSoul 128d a candidato BGE-M3 1024d (la original no se modifica)"
            Invoke-Checked $cutover @("migrate", $soulDb, "--candidate", $candidate, "--checkpoint", $checkpoint)
        }
        Invoke-Checked $cutover @("verify", $checkpoint)
        Invoke-Checked $cutover @("activate", $soulConfig, $checkpoint)
        $cutoverActivated = $true
        Good "MachineSoul activada en BGE-M3/índice auto; backup reversible preservado"
    } catch {
        Step "Fallo el upgrade; reactivando el runtime legacy preservado"
        $recoveryModel = $(if ($Model) { $Model } else { "legacy-recovery" })
        try {
            Invoke-Checked $machine @("init", "--root", $soulRoot, "--kind", $Kind, "--base-url", $BaseUrl, "--model", $recoveryModel)
        } catch {
            throw "HOLD CRITICO: fallo el upgrade y no pude reactivar el autostart legacy. Datos preservados en $soulRoot"
        }
        throw
    }
}
$tray = Join-Path $Venv "Scripts\soul-tray.exe"
if (-not $NoTray -and -not (Test-Path $tray)) { throw "Falta soul-tray.exe en el paquete instalado" }
Good "Paquete, dependencias y comandos verificados"

if (-not $Check -and -not $NoMachine -and (Test-Path -LiteralPath $soulConfig -PathType Leaf)) {
    Step "Actualizando el contrato T5 sin mover identidad, embeddings ni recuerdos"
    Invoke-Checked $machine @("upgrade-config", "--config", $soulConfig)
    Good "Contrato de memoria actualizado con backup reversible"
}

if (-not $Check -and -not $NoMachine) {
    if (-not $Model -and $legacyModel) { $Model = $legacyModel }
    if (-not $Model -and $Kind -eq "ollama") {
        $modelLine = @(& $ollamaCommand.Source list 2>$null | Select-Object -Skip 1 | Where-Object { $_ -notmatch '^bge-m3(:latest)?\s' } | Select-Object -First 1)
        if ($modelLine.Count -gt 0) { $Model = (($modelLine[0] -split '\s+')[0]) }
    }
    if ($Model) {
        Step "Inicializando alma persistente con cerebro ${Kind}:$Model"
        if ($cutoverActivated) { Step "Rollback byte-exacto armado si falla el runtime BGE" }
        Invoke-SoulPostActivateRuntime `
            -Machine $machine `
            -Cutover $cutover `
            -SoulConfig $soulConfig `
            -Checkpoint $checkpoint `
            -SoulRoot $soulRoot `
            -Kind $Kind `
            -BaseUrl $BaseUrl `
            -Model $Model `
            -CutoverActivated $cutoverActivated `
            -Runner { param($File, $Arguments) Invoke-Checked $File $Arguments }
        Good "Alma persistente y arranque automatico verificados"
    } else {
        Write-Warning "SOUL quedo instalado, pero no detecte un modelo Ollama. Cuando tengas uno ejecuta: $machine init --model NOMBRE"
    }
}

if (-not $NoTray -and -not $NoMachine) {
    Step "Verificando e instalando SOUL Tray al iniciar sesion"
    Invoke-Checked $tray @("--headless-check")
    if (-not $Check) {
        Invoke-Checked $tray @("--install-autostart")
    }
    Good "SOUL Tray verificado con tarea de usuario sin elevacion"
}

if (-not $NoMachine) {
    if ($Check) {
        Invoke-Checked $autowire @("--help")
    } else {
        Step "Detectando cerebros locales sin cambiar el cerebro activo"
        Invoke-Checked $autowire @("--root", $soulRoot, "reconcile")
        Invoke-Checked $autowire @("--root", $soulRoot, "install-autostart")
        Invoke-Checked $autowire @("--root", $soulRoot, "status")
        Good "AutoWire en shadow, per-user y con embeddings BGE-M3 bloqueados"
    }
}

if (-not $NoMachine -and -not $Check) {
    Step "Cableando SOUL local por las superficies MCP oficiales"
    $wiredClients = @(Install-SoulClientMcp $soulMcp $soulMcpEnroll $soulConfig)
    if ($wiredClients.Count -eq 0) {
        Write-Warning "No detecte Codex CLI ni Claude Code; SOUL queda Hot-Ready para cablearlos cuando aparezcan."
    } else {
        Good ("Clientes SOUL verificados: " + ($wiredClients -join ", "))
    }
}

if (-not $NoMachine -and -not $Check) {
    Step "Cerrando ACL de identidad, memoria, tokens y grants al usuario actual"
    Set-SoulPrivateAcl $soulRoot
    foreach ($sensitive in @(
        $soulConfig,
        $soulDb,
        (Join-Path $soulRoot "proxy.token"),
        (Join-Path $soulRoot "client-grants.json"),
        (Join-Path $soulRoot "autowire.sqlite3")
    )) { Set-SoulPrivateAcl $sensitive }
    Good "ACL Windows privada verificada (usuario actual + SYSTEM)"
}

Write-Host "SOUL listo. Datos persistentes: $env:LOCALAPPDATA\SOUL" -ForegroundColor Green
