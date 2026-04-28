# SEAL Installer para Windows v1.1
# Requiere: WSL2 (Ubuntu) + Tailscale activo en el laptop
#
# Ejecutar en PowerShell como Administrador:
#   Set-ExecutionPolicy Bypass -Scope Process -Force
#   irm http://100.75.201.110:9001/install.ps1 | iex

param(
    [string]$Profile  = "laptop_william",
    [string]$Agent    = "JARVIS",
    [string]$DbUrl    = "postgresql://seal:seal_memory_2026@100.75.201.110:5433/seal_memory",
    [string]$SkipDb   = "true"
)

$ErrorActionPreference = "Stop"

function Write-Step { param($msg) Write-Host "  >> $msg" -ForegroundColor Cyan }
function Write-Ok   { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  [!!] $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  ========================================" -ForegroundColor Blue
Write-Host "         SEAL Installer v1.1 (Windows)    " -ForegroundColor Blue
Write-Host "  ========================================" -ForegroundColor Blue
Write-Host ""

# ── 1. Verificar WSL2 ─────────────────────────────────────────────────────────

Write-Step "Verificando WSL2..."
$wslOk = $false
try {
    wsl --status 2>&1 | Out-Null
    $wslOk = ($LASTEXITCODE -eq 0)
} catch {}

if (-not $wslOk) {
    Write-Warn "WSL2 no detectado. Instalando Ubuntu 24.04..."
    wsl --install -d Ubuntu-24.04 --no-launch
    Write-Ok "WSL2 instalado. REINICIA Windows y vuelve a correr este script."
    pause
    exit 0
} else {
    Write-Ok "WSL2 disponible"
}

# ── 2. Descargar paquete SEAL via Tailscale ───────────────────────────────────

Write-Step "Descargando SEAL desde Spark (via Tailscale)..."
$sparkUrl = "http://100.75.201.110:9001/seal.tar.gz"
$tarWin   = "$env:TEMP\seal.tar.gz"

try {
    Invoke-WebRequest -Uri $sparkUrl -OutFile $tarWin -UseBasicParsing
    $sz = [math]::Round((Get-Item $tarWin).Length / 1KB)
    Write-Ok "Descargado: ${sz}KB"
} catch {
    Write-Warn "Error descargando desde Spark: $_"
    Write-Warn "Asegurate de que Tailscale este activo y conectado."
    exit 1
}

# Convert to WSL2-compatible path: C:\Users\... → /mnt/c/users/...
$tarWsl = "/mnt/" + ($tarWin -replace ':', '' -replace '\\', '/').ToLower()

# ── 3. Bootstrap en WSL2 ──────────────────────────────────────────────────────

Write-Step "Instalando SEAL en WSL2..."

$bootstrapCmd = @"
set -e
echo '[SEAL] Actualizando apt...'
sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv python3-pip 2>/dev/null

echo '[SEAL] Extrayendo paquete...'
rm -rf /tmp/seal_pkg && mkdir -p /tmp/seal_pkg
cp '$tarWsl' /tmp/seal_pkg/seal.tar.gz
tar -xzf /tmp/seal_pkg/seal.tar.gz -C /tmp/seal_pkg
cd /tmp/seal_pkg

echo '[SEAL] Creando entorno Python...'
python3 -m venv .venv
source .venv/bin/activate

echo '[SEAL] Ejecutando instalador...'
python3 -m seal.install \
  --profile $Profile \
  --agent   $Agent \
  --db-url  '$DbUrl' \
  --skip-systemd \
  --non-interactive

echo '[SEAL] Limpiando temporales...'
deactivate 2>/dev/null; rm -rf /tmp/seal_pkg

echo '[SEAL] Instalacion completada. CLI disponible en ~/.seal/bin/seal'
"@

wsl -e bash -c $bootstrapCmd

if ($LASTEXITCODE -ne 0) {
    Write-Warn "Algo fallo en WSL2. Revisa el output arriba."
    exit 1
}

Write-Ok "SEAL instalado en WSL2"

# ── 4. Crear lanzador en el Escritorio ────────────────────────────────────────

Write-Step "Creando acceso directo en el Escritorio..."

$desktop      = [Environment]::GetFolderPath("Desktop")
$launcherPath = "$desktop\SEAL - $Agent.bat"

$launcherContent = @"
@echo off
title SEAL - $Agent ($Profile)
echo Iniciando SEAL $Agent...
wsl -e bash -c "~/.seal/bin/seal start --profile $Profile --agent $Agent"
pause
"@

Set-Content -Path $launcherPath -Value $launcherContent -Encoding ASCII
Write-Ok "Launcher: $launcherPath"

# ── 5. Resultado ──────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  +------------------------------------------+" -ForegroundColor Green
Write-Host "  |     INSTALACION COMPLETADA               |" -ForegroundColor Green
Write-Host "  |                                          |" -ForegroundColor Green
Write-Host "  |  Perfil : $($Profile.PadRight(30))|" -ForegroundColor Green
Write-Host "  |  Agente : $($Agent.PadRight(30))|" -ForegroundColor Green
Write-Host "  |                                          |" -ForegroundColor Green
Write-Host "  |  Doble-click en el escritorio:           |" -ForegroundColor Green
Write-Host "  |  'SEAL - $($Agent.PadRight(26))'  |" -ForegroundColor Green
Write-Host "  +------------------------------------------+" -ForegroundColor Green
Write-Host ""
