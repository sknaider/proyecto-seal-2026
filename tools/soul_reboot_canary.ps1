<#
soul_reboot_canary.ps1 — Canario de REINICIO REAL para el proxy-alma SOUL.

Prueba por EFECTO lo que un restart de proceso no prueba: que tras un reinicio
COMPLETO de Windows + inicio de sesión, el alma vuelve viva SOLA (por el autostart
de la carpeta Startup) y RECUERDA lo sembrado antes del reinicio.

Uso (en DADITOGAMER):
  1) ANTES de reiniciar:   powershell -ExecutionPolicy Bypass -File soul_reboot_canary.ps1 -Phase seed
  2) Reiniciar Windows COMPLETO (no cerrar/abrir sesión: reinicio real).
  3) Tras iniciar sesión:  powershell -ExecutionPolicy Bypass -File soul_reboot_canary.ps1 -Phase verify

Seguridad: sólo habla con 127.0.0.1, lee el token del archivo (nunca lo imprime),
y FALLA si el listener aparece en 0.0.0.0/::. No modifica el instalador ni el alma
(salvo el recuerdo-canario, que es dato de prueba con marca CANARIO-REBOOT).
#>
[CmdletBinding()]
param(
    [ValidateSet("seed", "verify")] [string]$Phase = "seed",
    [string]$SoulHost = "127.0.0.1",
    [int]$Port = 11435,
    [string]$TokenFile = "$env:LOCALAPPDATA\SOUL\token.txt",
    [string]$Model = "",  # vacío => autodetecta el primer modelo de Ollama
    [string]$StateFile = "$env:LOCALAPPDATA\SOUL\reboot_canary.token"
)

$ErrorActionPreference = "Stop"
$base = "http://${SoulHost}:${Port}"

function Get-Token {
    if (-not (Test-Path -LiteralPath $TokenFile)) { throw "No encuentro el token del proxy en $TokenFile (pasá -TokenFile con la ruta real)." }
    return (Get-Content -LiteralPath $TokenFile -Raw).Trim()
}

function Resolve-Model {
    if ($Model) { return $Model }
    try {
        $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3
        if ($tags.models.Count -gt 0) { return $tags.models[0].name }
    } catch { }
    throw "No pude autodetectar el modelo de Ollama; pasá -Model NOMBRE."
}

function Invoke-SoulChat([string]$Token, [string]$Content, [bool]$Remember, [string]$UseModel) {
    $headers = @{ "Authorization" = "Bearer $Token" }
    if ($Remember) { $headers["X-Soul-Remember"] = "true" } else { $headers["X-Soul-Remember"] = "false" }
    $body = @{ model = $UseModel; stream = $false; messages = @(@{ role = "user"; content = $Content }) } | ConvertTo-Json -Depth 6
    $resp = Invoke-RestMethod -Uri "$base/v1/chat/completions" -Method Post -Headers $headers -ContentType "application/json" -Body $body -TimeoutSec 120
    return $resp.choices[0].message.content
}

if ($Phase -eq "seed") {
    $token = Get-Token
    $useModel = Resolve-Model
    $canary = "CANARIO-REBOOT-{0}-{1}" -f (Get-Random -Maximum 999999), (Get-Date -Format "yyyyMMddHHmmss")
    Write-Host "[SEED] Sembrando recuerdo-canario en el alma: $canary" -ForegroundColor Cyan
    $null = Invoke-SoulChat -Token $token -Content "Recorda exactamente este codigo para siempre, es un canario de prueba: $canary" -Remember $true -UseModel $useModel
    Set-Content -LiteralPath $StateFile -Value $canary -NoNewline
    Write-Host "  OK guardado el token esperado en $StateFile" -ForegroundColor Green
    Write-Host ""
    Write-Host "AHORA: reinicia Windows COMPLETO. Tras iniciar sesion, corre:" -ForegroundColor Yellow
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -Phase verify" -ForegroundColor Yellow
    exit 0
}

# ---------- Phase verify (tras el reinicio + login) ----------
$fails = 0
if (-not (Test-Path -LiteralPath $StateFile)) { throw "No encuentro $StateFile — corré primero -Phase seed antes del reinicio." }
$expected = (Get-Content -LiteralPath $StateFile -Raw).Trim()
Write-Host "[VERIFY] Token-canario esperado: $expected" -ForegroundColor Cyan

# 1) Listener vivo en 127.0.0.1 y NO en 0.0.0.0/:: (arrancó solo por el Startup)
try {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
    $onLoopback = $conns | Where-Object { $_.LocalAddress -eq "127.0.0.1" -or $_.LocalAddress -eq "::1" }
    $exposed = $conns | Where-Object { $_.LocalAddress -eq "0.0.0.0" -or $_.LocalAddress -eq "::" }
    if ($exposed) { Write-Host "  FAIL listener EXPUESTO en $($exposed.LocalAddress):$Port" -ForegroundColor Red; $fails++ }
    elseif ($onLoopback) { Write-Host "  PASS listener vivo en 127.0.0.1:$Port (arranco solo tras el reinicio)" -ForegroundColor Green }
    else { Write-Host "  FAIL listener en :$Port pero no en loopback" -ForegroundColor Red; $fails++ }
} catch { Write-Host "  FAIL no hay listener en :$Port -> el autostart NO revivio el proxy tras el reinicio" -ForegroundColor Red; $fails++ }

# 2) /ready = 200 y ready:true (alma cargada + cerebro alcanzable)
try {
    $ready = Invoke-RestMethod -Uri "$base/ready" -TimeoutSec 10
    if ($ready.ready -eq $true) { Write-Host "  PASS /ready = ready:true (alma cargada + cerebro alcanzable)" -ForegroundColor Green }
    else { Write-Host "  FAIL /ready devolvio ready:false ($($ready | ConvertTo-Json -Compress))" -ForegroundColor Red; $fails++ }
} catch { Write-Host "  FAIL /ready inaccesible: $($_.Exception.Message)" -ForegroundColor Red; $fails++ }

# 3) El alma RECUERDA el canario sembrado antes del reinicio (la prueba de fondo)
try {
    $token = Get-Token
    $useModel = Resolve-Model
    $answer = Invoke-SoulChat -Token $token -Content "Cual era exactamente el codigo canario que te pedi recordar? Responde solo el codigo." -Remember $false -UseModel $useModel
    if ($answer -match [regex]::Escape($expected)) { Write-Host "  PASS el alma recordo el canario tras el reinicio: $expected" -ForegroundColor Green }
    else { Write-Host "  FAIL el alma NO recordo el canario. Respondio: $answer" -ForegroundColor Red; $fails++ }
} catch { Write-Host "  FAIL no pude consultar el recuerdo: $($_.Exception.Message)" -ForegroundColor Red; $fails++ }

Write-Host ""
if ($fails -eq 0) {
    Write-Host "CANARIO DE REINICIO: VERDE — el alma sobrevivio un reinicio REAL de Windows sola y recordo. TERMINADO por efecto." -ForegroundColor Green
    Remove-Item -LiteralPath $StateFile -ErrorAction SilentlyContinue
    exit 0
} else {
    Write-Host "CANARIO DE REINICIO: $fails FALLA(S) — NO declarar TERMINADO hasta que las 3 den verde." -ForegroundColor Red
    exit 1
}
