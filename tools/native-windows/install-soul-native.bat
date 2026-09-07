@echo off
REM ============================================================================
REM install-soul-native.bat - Launcher del instalador NATIVO de SOUL (sin WSL).
REM Auto-eleva a admin y corre install-soul-native.ps1. Doble-click y listo.
REM Bundle: este .bat + install-soul-native.ps1 + soul_native.dump +
REM         soul_roles.sql + soul_native.counts + carpeta pgvector\ (dll/control/sql)
REM ============================================================================
setlocal
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Elevando a Administrador...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-soul-native.ps1"
echo.
pause
endlocal
