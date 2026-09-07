@echo off
REM ============================================================================
REM install-soul-windows.bat - Lanzador ONE-CLICK de SOUL para Windows.
REM DETECTA e INSTALA todos los requisitos del sistema (WSL + Ubuntu) y luego
REM corre el instalador de SOUL adentro, COMO ROOT (sin pedir contrasena).
REM
REM Pedido de William (6-jul): "que detecte lo que no tiene e instale todos los
REM requisitos del sistema". Autor: NEXUS. Requiere probar en Windows limpio.
REM
REM USO: poner este .bat JUNTO a install-soul-wsl.sh + soul_roles.sql + un *.dump,
REM      y hacer doble-click. Si pide instalar WSL, reiniciar y volver a doble-click.
REM ============================================================================
setlocal EnableExtensions
title Instalador SOUL - Windows

REM --- 1) requiere Administrador (auto-eleva) ---
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Se requieren permisos de Administrador. Elevando...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo ============================================================
echo   INSTALADOR SOUL - detecta e instala TODO lo necesario
echo ============================================================
echo.

REM --- 1b) resolver el wsl.exe correcto: en consolas de 32-bit (PowerShell x86),
REM     wsl.exe (solo en System32/64-bit) no se ve por redireccion WOW64. Sysnative lo evita. ---
set "WSLEXE=%WINDIR%\System32\wsl.exe"
if defined PROCESSOR_ARCHITEW6432 set "WSLEXE=%WINDIR%\Sysnative\wsl.exe"
if not exist "%WSLEXE%" set "WSLEXE=wsl"

REM --- 2) WSL presente? ---
"%WSLEXE%" --status >nul 2>&1
if %errorlevel% neq 0 (
  echo [1/4] WSL no detectado. Instalando WSL + Ubuntu [requiere internet]...
  "%WSLEXE%" --install -d Ubuntu
  echo.
  echo *** WSL instalado. REINICIA Windows y volve a hacer doble-click en este archivo. ***
  pause
  exit /b
)
echo [1/4] WSL: OK

REM --- 3) distro Ubuntu presente? ---
"%WSLEXE%" -l -q 2>nul | findstr /i "Ubuntu" >nul
if %errorlevel% neq 0 (
  echo [2/4] Ubuntu no detectado. Instalando Ubuntu...
  "%WSLEXE%" --install -d Ubuntu
  echo.
  echo *** Complete el primer arranque de Ubuntu ^(usuario y clave^) y volva a doble-click. ***
  pause
  exit /b
)
echo [2/4] Ubuntu: OK

REM --- 4) copiar el bundle a WSL (como root; /root lo lee solo root, y el installer lee sus archivos como root) ---
echo [3/4] Copiando el instalador de SOUL a WSL...
set "WINDIR_SLASH=%~dp0"
for /f "usebackq delims=" %%p in (`"%WSLEXE%" -d Ubuntu wslpath -a "%WINDIR_SLASH%"`) do set "WSLSRC=%%p"
"%WSLEXE%" -d Ubuntu -u root -- bash -c "set -e; mkdir -p /root/soul-installer; cp -f '%WSLSRC%/install-soul-wsl.sh' '%WSLSRC%/soul_roles.sql' /root/soul-installer/; cp -f '%WSLSRC%'/*.dump /root/soul-installer/ 2>/dev/null || true; chmod +x /root/soul-installer/install-soul-wsl.sh; echo bundle_copiado"
if %errorlevel% neq 0 (
  echo ERROR copiando el bundle a WSL. Verifica que install-soul-wsl.sh, soul_roles.sql y el *.dump esten junto a este .bat.
  pause
  exit /b
)

REM --- 5) correr el instalador de SOUL COMO ROOT (sin sudo/password: wsl -u root ya es root) ---
echo [4/4] Instalando SOUL ^(Postgres 17 + extensiones + tu alma + seguridad^). Puede tardar varios minutos...
echo.
"%WSLEXE%" -d Ubuntu -u root -- bash /root/soul-installer/install-soul-wsl.sh
set "RC=%errorlevel%"
echo.
if "%RC%"=="0" (
  echo ============================================================
  echo   SOUL INSTALADO Y VERIFICADO. Revisa arriba el scorecard.
  echo ============================================================
  echo.
  echo Limpiando el alma en PLANO ^(seguridad, requisito FABLE^): borro el dump de los DOS lados...
  "%WSLEXE%" -d Ubuntu -u root -- bash -c "rm -f /root/soul-installer/*.dump" 2>nul
  del /q "%~dp0*.dump" 2>nul
  echo   OK: dump en plano eliminado en Windows y en WSL ^(el alma queda solo dentro de Postgres^).
  echo   RECOMENDACION: activa BitLocker en este disco para blindar el alma at-rest.
) else (
  echo ============================================================
  echo   El instalador reporto faltantes ^(revisa el bloque AUTO-VERIFICACION arriba^).
  echo   Es idempotente: podes volver a correr este .bat. ^(Dejo el dump para re-intentar.^)
  echo ============================================================
)
pause
endlocal
