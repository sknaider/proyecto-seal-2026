[CmdletBinding()]
param(
    [string]$Model = $env:SOUL_MODEL,
    [string]$Kind = $(if ($env:SOUL_UPSTREAM_KIND) { $env:SOUL_UPSTREAM_KIND } else { "ollama" }),
    [string]$BaseUrl = $(if ($env:SOUL_UPSTREAM_URL) { $env:SOUL_UPSTREAM_URL } else { "http://127.0.0.1:11434/v1" }),
    [string]$Venv = $(if ($env:SOUL_VENV) { $env:SOUL_VENV } else { Join-Path $env:LOCALAPPDATA "SOUL\venv" }),
    [string]$PackageSource = $(if ($env:SOUL_PACKAGE_SOURCE) { $env:SOUL_PACKAGE_SOURCE } else { "soul-platform" }),
    [switch]$NoMachine,
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
$venvPython = Join-Path $Venv "Scripts\python.exe"

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
    Invoke-Checked $venvPython @("-m", "pip", "install", "--upgrade", $PackageSource)
}

if (-not (Test-Path $venvPython)) { throw "No existe un entorno SOUL verificable en $Venv" }
Invoke-Checked $venvPython @("-c", "import soul_platform, soul_framework")
Invoke-Checked $venvPython @("-m", "pip", "check")
$installedVersion = & $venvPython -c "from importlib.metadata import version; print(version('soul-platform'))"
if ($LASTEXITCODE -ne 0) { throw "No pude leer la version instalada de soul-platform" }
if ([version]$installedVersion.Trim() -lt [version]"0.2.0") {
    throw "Se requiere soul-platform 0.2.0 o superior; quedo instalada $installedVersion"
}
$machine = Join-Path $Venv "Scripts\soul-machine.exe"
if (-not (Test-Path $machine)) { throw "Falta soul-machine.exe en el paquete instalado" }
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
        Good "Alma persistente y arranque automatico verificados"
    } else {
        Write-Warning "SOUL quedo instalado, pero no detecte un modelo Ollama. Cuando tengas uno ejecuta: $machine init --model NOMBRE"
    }
}

Write-Host "SOUL listo. Datos persistentes: $env:LOCALAPPDATA\SOUL" -ForegroundColor Green
