# ESAN Demo — JARVIS Live Watcher
# Ejecutar en la laptop de William (PowerShell).
# Muestra cada linea nueva que JARVIS escribe en tiempo real.

$FILE = "$env:USERPROFILE\Desktop\JARVIS_LIVE.txt"

Write-Host "" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  JARVIS SEAL -- STREAMING EN VIVO" -ForegroundColor Cyan
Write-Host "  Esperando mensajes desde DGX Spark..." -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""

# Crear archivo si no existe
if (-not (Test-Path $FILE)) {
    "" | Out-File -Encoding utf8 $FILE
}

# tail -f nativo de PowerShell
Get-Content $FILE -Wait -Tail 0 | ForEach-Object {
    $timestamp = Get-Date -Format "HH:mm:ss"
    Write-Host "[$timestamp] $_" -ForegroundColor Green
}
