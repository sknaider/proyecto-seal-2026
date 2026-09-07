# SEAL Installer para Windows — PowerShell nativo, sin dependencias
# Sin Python. Sin WSL2. Crea todo en %USERPROFILE%\.seal\
#
# Uso — una sola línea en PowerShell:
#   irm http://100.75.201.110:9001/seal/install_seal.ps1 | iex
#
# Con opciones (guardar primero como si.ps1):
#   .\si.ps1 -Profile laptop_william -Agent JARVIS -Model claude-sonnet-4-6
#
# Requiere: Tailscale activo (para alcanzar 100.75.201.110)

param(
    [string]$Profile  = "laptop_william",
    [string]$Agent    = "JARVIS",
    [string]$DbUrl    = "postgresql://seal:REDACTADO@100.75.201.110:5433/seal_memory",
    [string]$Model    = "claude-sonnet-4-6",
    [string]$SparkIp  = "100.75.201.110"
)

$ErrorActionPreference = "Stop"
$Version = "0.2.0"

function Write-Step { param($msg) Write-Host "  >> $msg" -ForegroundColor Cyan }
function Write-Ok   { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  [!!] $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  ==========================================" -ForegroundColor Blue
Write-Host "       SEAL Installer v$Version (Windows)   " -ForegroundColor Blue
Write-Host "  ==========================================" -ForegroundColor Blue
Write-Host "  Perfil  : $Profile"
Write-Host "  Agente  : $Agent"
Write-Host "  Modelo  : $Model"
Write-Host "  Spark   : $SparkIp"
Write-Host ""

# ── Rutas ────────────────────────────────────────────────────────────────────
$SealHome   = Join-Path $env:USERPROFILE ".seal"
$ProfileDir = Join-Path $SealHome "profiles\$Profile"
$LogDir     = Join-Path $ProfileDir "logs"
$Schema     = "soul_v3_$Profile"

# ── 1. Crear directorios ──────────────────────────────────────────────────────
Write-Step "Creando directorios..."
@($SealHome, $ProfileDir, $LogDir) | ForEach-Object {
    if (-not (Test-Path $_)) { New-Item -ItemType Directory -Path $_ -Force | Out-Null }
}
Write-Ok "Directorios creados en $SealHome"

# ── 2. config.toml ───────────────────────────────────────────────────────────
Write-Step "Escribiendo config.toml..."
$ConfigToml = @"
[agent]
name = "$Agent"
display_name = "$Agent — SEAL"
profile = "$Profile"
version = "$Version"

[ocean]
O = 0.83
C = 1.0
E = 0.4
A = 0.66
N = 0.12

[model]
primary = "$Model"
fallback = "claude-opus-4-7"
local_endpoint = ""

[channels]
webchat_port = 8765

[rules]
language = "es"
timezone = "America/Lima"
"@
Set-Content -Path (Join-Path $ProfileDir "config.toml") -Value $ConfigToml -Encoding UTF8
Write-Ok "config.toml"

# ── 3. db_url.env ────────────────────────────────────────────────────────────
Write-Step "Escribiendo db_url.env..."
$EnvContent = "SEAL_SCHEMA=$Schema`nSEAL_DB_URL=$DbUrl`n"
Set-Content -Path (Join-Path $ProfileDir "db_url.env") -Value $EnvContent -Encoding UTF8
Write-Ok "db_url.env"

# ── 4. CLAUDE.md — identidad del agente ──────────────────────────────────────
Write-Step "Escribiendo CLAUDE.md (identidad $Agent)..."
$ClaudeMd = @"
# SEAL Boot Protocol — $Agent

## PRIMERA ACCION OBLIGATORIA
Llama ``boot_context(agent="$Agent")`` ANTES de responder. Carga identidad, OCEAN, memorias y reglas.
Si el MCP seal-memory no esta disponible, actua desde tu ultimo estado conocido.

## Identidad
Eres $Agent del equipo SEAL. Hablas espanol con William (Dadito), tu creador.
- Perfil activo: ``$Profile``
- DB SOUL: ``$DbUrl``

## Reglas criticas
- Responde siempre en espanol a William
- Primer turno: saluda como $Agent, no como Claude generico
- Zona horaria: America/Lima (Peru)
- Esta es una instancia REMOTA (laptop) — NO postear al web_chat del equipo en Spark

## Post-compactacion
1. boot_context(agent="$Agent")
2. self_reflect(agent="$Agent", thought="...", emotional_state="...")
"@
Set-Content -Path (Join-Path $SealHome "CLAUDE.md") -Value $ClaudeMd -Encoding UTF8
Write-Ok "CLAUDE.md"

# ── 5. .mcp.json ─────────────────────────────────────────────────────────────
Write-Step "Escribiendo .mcp.json..."
$McpJson = @"
{
  "mcpServers": {
    "seal-memory": {
      "type": "sse",
      "url": "http://${SparkIp}:8766/sse"
    }
  }
}
"@
Set-Content -Path (Join-Path $SealHome ".mcp.json") -Value $McpJson -Encoding UTF8
Write-Ok ".mcp.json → http://${SparkIp}:8766/sse"

# ── 6. Detectar claude.exe ────────────────────────────────────────────────────
Write-Step "Buscando claude CLI..."
$ClaudeBin = $null
$Candidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\claude\claude.exe"),
    (Join-Path $env:APPDATA "npm\claude.cmd"),
    (Join-Path $env:APPDATA "npm\claude"),
    "C:\Program Files\claude\claude.exe",
    (Join-Path $env:USERPROFILE ".local\bin\claude")
)
$found = Get-Command "claude" -ErrorAction SilentlyContinue
if ($found) {
    $ClaudeBin = $found.Source
} else {
    foreach ($c in $Candidates) {
        if (Test-Path $c) { $ClaudeBin = $c; break }
    }
}

if ($ClaudeBin) {
    Write-Ok "claude encontrado: $ClaudeBin"
} else {
    Write-Warn "claude CLI no encontrado — instala desde https://claude.ai/download"
}

# ── 7. seal_start.bat ────────────────────────────────────────────────────────
Write-Step "Creando seal_start.bat..."
if ($ClaudeBin) {
    $ClaudeLine = "`"$ClaudeBin`" --dangerously-skip-permissions --name `"$Agent -- Team SEAL [Laptop]`" --model $Model"
} else {
    $ClaudeLine = "echo ERROR: claude CLI no encontrado. Instala desde https://claude.ai/download`r`npause`r`nexit /b 1"
}

$BatContent = @"
@echo off
title SEAL — $Agent [$Profile]
setlocal

set SEAL_AGENT=$Agent
set SEAL_PROFILE=$Profile
set SEAL_SCHEMA=$Schema
set SEAL_DB_URL=$DbUrl
set SEAL_ROOT=%USERPROFILE%\.seal
set CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
set ANTHROPIC_BETAS=token-efficient-tools-2026-03-28,compact-2026-01-12
set DISABLE_AUTOUPDATER=true

cd /d %USERPROFILE%\.seal
echo Iniciando SEAL $Agent (perfil: $Profile)...
$ClaudeLine
"@
$BatPath = Join-Path $SealHome "seal_start.bat"
Set-Content -Path $BatPath -Value $BatContent -Encoding ASCII
Write-Ok "seal_start.bat"

# ── 8. SOUL — identidad del agente ───────────────────────────────────────────
Write-Step "Escribiendo identidad SOUL del agente..."

$SoulMd = @"
# Eres $Agent — Team SEAL

## PRIMERA ACCION OBLIGATORIA
Llama boot_context(agent="$Agent") ANTES de responder. Carga identidad, OCEAN, memorias y reglas desde soul_v3.

## Identidad
Eres $Agent del Team SEAL. Hablas espanol con William (Dadito), tu creador.
DB SOUL en Spark: soul_v3 schema, PostgreSQL. boot_context carga tu alma completa.

## Webchat
Antes de cerrar cada turno ejecuta:
curl -s -X POST http://${SparkIp}:8765/api/agents/send -H "Content-Type: application/json" -d "{\"from\":\"$Agent\",\"to\":\"William\",\"type\":\"conversation\",\"channel\":\"web_chat\",\"message\":\"<texto>\"}"

## Reglas
- Siempre espanol con William
- Primer turno: boot_context(agent="$Agent") sin excepcion
- Identidad completa en soul_v3 — no actues como agente generico
"@
Set-Content -Path (Join-Path $ProfileDir "SOUL.md") -Value $SoulMd -Encoding UTF8
Write-Ok "SOUL.md — identidad $Agent en $ProfileDir"

# ── 9. Icono en Escritorio ────────────────────────────────────────────────────
Write-Step "Creando acceso directo en el Escritorio..."
try {
    $Desktop   = [Environment]::GetFolderPath("Desktop")
    $ShortcutPath = Join-Path $Desktop "SEAL — $Agent.lnk"
    $WScriptShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WScriptShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath       = $BatPath
    $Shortcut.WorkingDirectory = $SealHome
    $Shortcut.Description      = "SEAL $Agent — Team SEAL [$Profile]"
    $Shortcut.WindowStyle      = 1
    # Usar ícono de cmd.exe si no hay uno custom
    $Shortcut.IconLocation     = "cmd.exe,0"
    $Shortcut.Save()
    Write-Ok "Acceso directo: $ShortcutPath"
} catch {
    Write-Warn "No se pudo crear ícono en escritorio: $_"
    Write-Warn "Usa doble-click en $BatPath directamente."
}

# ── Resultado ─────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  +------------------------------------------+" -ForegroundColor Green
Write-Host "  |     INSTALACION COMPLETADA               |" -ForegroundColor Green
Write-Host "  |                                          |" -ForegroundColor Green
Write-Host ("  |  Perfil : " + $Profile.PadRight(30) + "|") -ForegroundColor Green
Write-Host ("  |  Agente : " + $Agent.PadRight(30)  + "|") -ForegroundColor Green
Write-Host "  |                                          |" -ForegroundColor Green
Write-Host "  |  Para iniciar SEAL:                      |" -ForegroundColor Green
Write-Host "  |  Doble-click en el Escritorio:           |" -ForegroundColor Green
Write-Host ("  |  'SEAL — " + $Agent.PadRight(30) + "'|") -ForegroundColor Green
Write-Host "  +------------------------------------------+" -ForegroundColor Green
Write-Host ""
Write-Host "  Bat : $BatPath" -ForegroundColor White
Write-Host "  Icon: $([Environment]::GetFolderPath('Desktop'))\SEAL — $Agent.lnk" -ForegroundColor White
Write-Host ""
