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

function Step([string]$Message) { Write-Host "[SOUL] $Message" -ForegroundColor Cyan }
function Good([string]$Message) { Write-Host "  OK $Message" -ForegroundColor Green }
function Invoke-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "El comando fallo con codigo ${LASTEXITCODE}: $File $($Arguments -join ' ')"
    }
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
$resolvedPackageIsBundled = Test-Path -LiteralPath $resolvedPackageSource -PathType Leaf
$venvPython = Join-Path $Venv "Scripts\python.exe"
$soulRoot = Join-Path $env:LOCALAPPDATA "SOUL"
$soulConfig = Join-Path $soulRoot "proxy.toml"
$soulDb = Join-Path $soulRoot "MachineSoul.db"
$legacyMigrationRequired = $false
if (Test-Path -LiteralPath $soulConfig -PathType Leaf) {
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
}

if ($legacyMigrationRequired -and $Check) {
    throw "HOLD: MachineSoul usa embeddings legacy 128d. Ejecuta el instalador sin -Check para migrar de forma reversible."
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
        Invoke-Checked $venvPython @("-m", "pip", "install", "--upgrade", "--find-links", $PSScriptRoot, $resolvedPackageSource)
    } else {
        Invoke-Checked $venvPython @("-m", "pip", "install", "--upgrade", $resolvedPackageSource)
    }
    Step "Instalando la interfaz de bandeja con versiones verificadas"
    Invoke-Checked $venvPython @("-m", "pip", "install", "--upgrade", "pystray==0.19.5", "pillow==12.3.0")
    if ($resolvedPackageIsBundled) {
        # `pip --upgrade` skips a local wheel when the same version is already
        # present. Reinstall only this package so updates with an unchanged
        # semantic version still load the exact verified bundle bytes.
        Invoke-Checked $venvPython @("-m", "pip", "install", "--force-reinstall", "--no-deps", $resolvedPackageSource)
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

$ollamaCommand = Get-Command "ollama" -ErrorAction SilentlyContinue
if (-not $ollamaCommand) {
    throw "SOUL 0.4 requiere Ollama local para BGE-M3. Instala Ollama y vuelve a ejecutar el instalador."
}
$bgeInstalled = $false
try {
    $ollamaTags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
    $bgeInstalled = @($ollamaTags.models | Where-Object {
        $_.name -eq "bge-m3" -or $_.name -eq "bge-m3:latest" -or $_.model -eq "bge-m3:latest"
    }).Count -gt 0
} catch {
    if ($Check) { throw "No pude verificar Ollama/BGE-M3 local: $($_.Exception.Message)" }
}
if (-not $bgeInstalled) {
    if ($Check) { throw "Falta el modelo local bge-m3. Ejecuta el instalador sin -Check para instalarlo." }
    Step "Instalando BGE-M3 local para memoria multilingue"
    Invoke-Checked $ollamaCommand.Source @("pull", "bge-m3")
}
$bgeProbe = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:11434/api/embed" `
    -ContentType "application/json" -Body '{"model":"bge-m3","input":"SOUL readiness"}' -TimeoutSec 120
if (@($bgeProbe.embeddings).Count -ne 1 -or @($bgeProbe.embeddings[0]).Count -ne 1024) {
    throw "BGE-M3 local no devolvio un embedding de 1024 dimensiones."
}
Good "BGE-M3 local verificado (1024 dimensiones)"

$machine = Join-Path $Venv "Scripts\soul-machine.exe"
if (-not (Test-Path $machine)) { throw "Falta soul-machine.exe en el paquete instalado" }
$cutover = Join-Path $Venv "Scripts\soul-machine-embedding-cutover.exe"
if (-not (Test-Path $cutover)) { throw "Falta soul-machine-embedding-cutover.exe en el paquete instalado" }

if ($legacyMigrationRequired) {
    if (-not (Test-Path -LiteralPath $soulDb -PathType Leaf)) {
        throw "HOLD: existe config legacy pero falta la base MachineSoul.db"
    }
    Step "Verificando BGE-M3 antes de detener el alma legacy"
    $probeBody = @{ model = "bge-m3"; input = @("SOUL readiness") } | ConvertTo-Json -Compress
    try {
        $probe = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/embed" -Method Post -ContentType "application/json" -Body $probeBody -TimeoutSec 90
    } catch {
        throw "HOLD: BGE-M3 local no esta listo; el alma legacy sigue activa. Ejecuta: ollama pull bge-m3"
    }
    if (-not $probe.embeddings -or $probe.embeddings.Count -ne 1 -or $probe.embeddings[0].Count -ne 1024) {
        throw "HOLD: BGE-M3 no devolvio exactamente 1024 dimensiones; el alma legacy sigue activa"
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
if (-not (Test-Path $tray)) { throw "Falta soul-tray.exe en el paquete instalado" }
$trayCli = Join-Path $Venv "Scripts\soul-tray-cli.exe"
if (-not (Test-Path $trayCli)) { throw "Falta soul-tray-cli.exe en el paquete instalado" }
Invoke-Checked $trayCli @("--check-desktop")
Good "Paquete, dependencias y comandos verificados"

if (-not $Check -and -not $NoMachine) {
    if (-not $Model -and $Kind -eq "ollama") {
        try {
            $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2
            if ($tags.models.Count -gt 0) { $Model = $tags.models[0].name }
        } catch { }
    }
    if ($Model) {
        Step "Inicializando alma persistente con cerebro ${Kind}:$Model"
        Invoke-Checked $machine @("init", "--kind", $Kind, "--base-url", $BaseUrl, "--model", $Model)
        Invoke-Checked $trayCli @("--check")
        Good "Alma persistente y arranque automatico verificados"
    } else {
        Write-Warning "SOUL quedo instalado, pero no detecte un modelo Ollama. Cuando tengas uno ejecuta: $machine init --model NOMBRE"
    }
}

if (-not $Check -and -not $NoTray) {
    $trayAutostartInstalled = $false
    try {
        Step "Registrando la interfaz para cada inicio de sesion"
        Invoke-Checked $trayCli @("--install-autostart")
        $trayAutostartInstalled = $true
        Step "Abriendo la interfaz SOUL junto al reloj"
        $trayProcess = Start-Process -FilePath $tray -WindowStyle Hidden -PassThru
        Start-Sleep -Seconds 2
        if ($trayProcess.HasExited -and $trayProcess.ExitCode -ne 0) {
            throw "La interfaz SOUL termino al iniciar (codigo $($trayProcess.ExitCode))."
        }
        Good "Interfaz de bandeja iniciada"
    } catch {
        if ($trayAutostartInstalled) {
            & $trayCli --remove-autostart | Out-Null
        }
        throw
    }
}

if (-not $Check -and $NoTray) {
    Step "Desactivando el arranque automatico de la interfaz por -NoTray"
    Invoke-Checked $trayCli @("--remove-autostart")
    Good "Interfaz de bandeja desactivada; alma y memoria preservadas"
}

Write-Host "SOUL listo. Datos persistentes: $env:LOCALAPPDATA\SOUL" -ForegroundColor Green
