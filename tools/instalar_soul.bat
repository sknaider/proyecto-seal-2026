@echo off
setlocal enabledelayedexpansion
title Instalar SOUL Core (un clic)
color 0b

echo ============================================
echo   Instalador de SOUL Core  -  un solo clic
echo ============================================
echo.

REM ---------- 1) Buscar Python 3.11+ ya instalado ----------
set "PYCMD="
for %%V in (3.13 3.12 3.11) do (
  py -%%V --version >nul 2>&1 && ( set "PYCMD=py -%%V" & goto :tienepython )
)
py -3 --version >nul 2>&1 && ( set "PYCMD=py -3" & goto :tienepython )
python --version >nul 2>&1 && ( set "PYCMD=python" & goto :tienepython )

REM ---------- 2) No hay Python -> instalar con winget ----------
echo No se encontro Python. Voy a instalarlo con winget...
where winget >nul 2>&1
if errorlevel 1 (
  echo.
  echo [!] winget no esta disponible en este equipo.
  echo     Instala Python 3.11 o superior a mano desde:
  echo         https://www.python.org/downloads/
  echo     IMPORTANTE: marca la casilla "Add Python to PATH".
  echo     Luego volve a hacer doble clic en este instalador.
  echo.
  pause
  exit /b 1
)
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
echo.
echo ==========================================================
echo   Python quedo instalado.
echo   CERRA esta ventana y volve a hacer doble clic en el .bat
echo   (Windows necesita reabrir la terminal para verlo.)
echo ==========================================================
pause
exit /b 0

:tienepython
echo Usando Python:  !PYCMD!
!PYCMD! --version
echo.

REM ---------- 3) Crear el entorno (desechable) ----------
set "VENV=%USERPROFILE%\soul-core"
if exist "%VENV%\Scripts\python.exe" (
  echo Entorno ya existe en:  %VENV%   ^(lo reuso^)
) else (
  echo Creando entorno en:  %VENV%
  !PYCMD! -m venv "%VENV%"
  if errorlevel 1 ( echo [!] No se pudo crear el entorno. & pause & exit /b 1 )
)
echo.

REM ---------- 4) Instalar SOUL Core + dependencias (un comando, SIN los 2.8GB de [all]) ----------
echo Instalando SOUL Core y sus dependencias (liviano)...
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
"%VENV%\Scripts\python.exe" -m pip install soul-framework
if errorlevel 1 ( echo [!] Fallo la instalacion. Revisa tu internet y reintenta. & pause & exit /b 1 )
echo.

REM ---------- 5) Verificar ----------
echo Verificando la instalacion...
"%VENV%\Scripts\python.exe" -c "import soul_framework; print('  OK - SOUL Core importa correctamente')"
if errorlevel 1 ( echo [!] Se instalo pero no importa. Avisa al equipo. & pause & exit /b 1 )
if exist "%VENV%\Scripts\soul.exe" echo   OK - el comando 'soul' quedo listo

echo.
echo ============================================
echo   LISTO. SOUL Core instalado y verificado.
echo ============================================
echo.
echo   Para USARLO, abri una terminal y escribi:
echo       "%VENV%\Scripts\activate"
echo       soul --help
echo.
echo   Para BORRAR todo (es desechable):
echo       rmdir /s /q "%VENV%"
echo.
pause
endlocal
