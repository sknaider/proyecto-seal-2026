# ============================================================================
# check_ps1.ps1 — GATE parse+bind para instaladores PowerShell (FABLE, 8-jul-2026).
#
# POR QUÉ EXISTE: William chocó 3 errores SEGUIDOS al instalar (ej. `$Db` vs el
# alias `-db` de `-Debug` bajo [CmdletBinding()]) — todos de la clase sintaxis/
# param-bind, que un "escaneo de nombres de variables" NO caza y que solo se ven
# al EJECUTAR. Esto lo convertía en tester uno-por-uno.
#
# QUÉ HACE (determinístico, en segundos, SIN necesitar Windows — pwsh en el Spark):
#   1) PARSE  — [Parser]::ParseFile → TODOS los errores de sintaxis de una.
#   2) BIND   — Get-Command fuerza el análisis del bloque param + common-params de
#               CmdletBinding → reproduce EXACTAMENTE los conflictos tipo `$Db`.
#
# REGLA DE EQUIPO: ningún .ps1 se re-bundlea sin pasar este gate primero (verde).
# Cubre la clase parse/bind; el RUNTIME (instalar PG, restore, servicios Windows)
# sigue exigiendo el E2E real en Windows.
#
# USO:  ~/.local/pwsh/pwsh -NoProfile -File check_ps1.ps1 -Target <ruta.ps1>
#       rc=0 → verde (PARSE_OK + PARAM_BIND_OK).  rc=1 → hay errores (los lista).
# ============================================================================
param([Parameter(Mandatory=$true)][string]$Target)

if (-not (Test-Path $Target)) { Write-Host "check_ps1: no existe $Target"; exit 2 }

$ok = $true
$errors = $null; $tokens = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($Target, [ref]$tokens, [ref]$errors)
Write-Host "=== PARSE ==="
if ($errors -and $errors.Count -gt 0) {
  $ok = $false
  Write-Host "PARSE_FAIL: $($errors.Count) errores"
  foreach ($e in $errors) { Write-Host ("  L{0}: {1}" -f $e.Extent.StartLineNumber, $e.Message) }
} else { Write-Host "PARSE_OK (0 errores de sintaxis)" }

Write-Host "=== PARAM BIND (CmdletBinding + common-params — aqui salio el `$Db) ==="
try {
  $cmd = Get-Command $Target -ErrorAction Stop
  $keys = @($cmd.Parameters.Keys)
  Write-Host ("PARAM_BIND_OK - {0} params: {1}" -f $keys.Count, ($keys -join ', '))
} catch {
  $ok = $false
  Write-Host ("PARAM_BIND_FAIL: {0}" -f $_.Exception.Message)
}

# ENCODING GATE (gap que cazó NEXUS 8-jul, no mi parse: pwsh 7.x lee UTF-8 SIEMPRE, pero el .bat
# lanza Windows PowerShell 5.1, que sin BOM lee el CODEPAGE del sistema → un char no-ASCII (— « » é)
# rompe el ARRANQUE del script en 5.1 aunque 7.x lo parsee limpio. Mi check con pwsh 7.4.6 daba
# FALSO-VERDE. Por eso: exijo BOM o ASCII-puro para el target real, no solo parse en 7.x.)
Write-Host "=== ENCODING (target real = Windows PowerShell 5.1 via .bat) ==="
$bytes = [System.IO.File]::ReadAllBytes($Target)
$hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
$nonAscii = 0; foreach ($b in $bytes) { if ($b -gt 0x7F) { $nonAscii++ } }
if ($nonAscii -gt 0 -and -not $hasBom) {
  $ok = $false
  Write-Host "ENCODING_FAIL: $nonAscii bytes no-ASCII SIN BOM UTF-8 -> rompe el arranque en PowerShell 5.1 (el que corre William). Fix: guarda el .ps1 como UTF-8 CON BOM, o usa solo ASCII."
} elseif ($nonAscii -gt 0) {
  Write-Host "ENCODING_OK: $nonAscii bytes no-ASCII pero CON BOM UTF-8 -> 5.1 lo lee bien."
} else {
  Write-Host "ENCODING_OK: ASCII puro (a prueba de codepage, imposible que rompa por encoding)."
}

Write-Host ""
if ($ok) { Write-Host "RESULTADO: VERDE (parse+bind limpio)"; exit 0 }
else     { Write-Host "RESULTADO: ROJO (revisar arriba)"; exit 1 }
